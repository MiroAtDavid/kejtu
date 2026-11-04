"""Reminder capability and bounded model projection over the one reminder owner."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import time

from ...core.records import Failure
from ...core.reminders import ReminderSupport, encoded, identity
from ...core.reminder_tools import definition, mutation_key

REMINDER_ACCESS_SCHEMA = '''
CREATE TABLE reminder_grants(conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),revision INTEGER NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE reminder_grant_operations(mutation_key TEXT PRIMARY KEY,
 conversation_id TEXT NOT NULL REFERENCES conversations(id),intent_sha256 TEXT NOT NULL,
 receipt TEXT NOT NULL);
'''


class ReminderAccess:
    def __init__(self, reminders):
        self.owner = reminders
        self.store, self.db = reminders.store, reminders.db

    def epoch(self):
        # Indexed rowid tails, not archive scans. Occurrence coalescing is checked
        # independently by the exact delivery receipt; it is not a model write.
        return tuple(self.db.execute(f'SELECT coalesce(max(rowid),0) FROM {table}').fetchone()[0]
                     for table in ('reminder_operations', 'reminder_grant_operations')) + (self.store.access_settings.value['revision'],)

    def status(self, cid):
        conversation = self.store.conversation(cid)
        row = self.db.execute('SELECT enabled,revision FROM reminder_grants WHERE conversation_id=?', (cid,)).fetchone()
        readable = conversation.history_enabled and conversation.memory_bank_id is not None and self.db.execute(
            'SELECT 1 FROM memory_grants WHERE conversation_id=? AND enabled=1', (cid,)).fetchone() is not None
        return dict(conversation_id=cid, enabled=conversation.reminders_enabled,
                    revision=row['revision'] if row else 0, readable=bool(readable))

    def access(self, args, now):
        action, cid = args.get('action'), args.get('conversation_id')
        fields = {'read': {'action', 'conversation_id'},
                  'set': {'action', 'conversation_id', 'mutation_key', 'expected_revision', 'enabled'},
                  'lookup': {'action', 'conversation_id', 'mutation_key', 'intent_sha256'}}
        if not isinstance(action, str) or action not in fields or set(args) != fields[action]:
            raise Failure('reminder_access', 'Use an exact reminder permission operation.')
        self.owner._id(cid)
        if action != 'read':
            self.owner._id(args['mutation_key'])
        with self.owner._transaction(write=action == 'set'):
            current = self.status(cid)
            if action == 'read':
                return current
            row = self.db.execute('SELECT * FROM reminder_grant_operations WHERE mutation_key=?', (args['mutation_key'],)).fetchone()
            if action == 'lookup':
                if row is None:
                    return dict(status='absent')
                if row['conversation_id'] != cid or row['intent_sha256'] != args['intent_sha256']:
                    raise Failure('reminder_conflict', 'Permission receipt belongs to another exact intent.')
                return json.loads(row['receipt']) | {'replayed': True}
            if type(args['enabled']) is not bool or type(args['expected_revision']) is not int or not 0 <= args['expected_revision'] < 9007199254740991:
                raise Failure('reminder_access', 'Use explicit enabled and current permission revision.')
            digest = hashlib.sha256(encoded({k:v for k,v in args.items() if k != 'mutation_key'}).encode()).hexdigest()
            if row:
                if row['intent_sha256'] != digest:
                    raise Failure('reminder_conflict', 'Permission mutation key belongs to another intent.')
                return json.loads(row['receipt']) | {'replayed': True}
            self.store.access_settings.require_conversation_control('reminders')
            if current['revision'] != args['expected_revision']:
                raise Failure('reminder_conflict', 'Reminder permission changed; read its current revision.')
            if args['enabled'] and not current['readable']:
                raise Failure('reminder_forbidden', 'This permission cannot grant missing conversation history/memory access.')
            changed = args['enabled'] != current['enabled']
            revision = current['revision'] + int(changed)
            self.db.execute('INSERT INTO reminder_grants VALUES(?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET enabled=excluded.enabled,revision=excluded.revision,updated_at=excluded.updated_at',
                            (cid, int(args['enabled']), revision, now))
            result = dict(status='committed' if changed else 'unchanged', conversation_id=cid,
                enabled=args['enabled'], revision=revision, mutation_key=args['mutation_key'],
                intent_sha256=digest, replayed=False)
            self.db.execute('INSERT INTO reminder_grant_operations VALUES(?,?,?,?)',
                (args['mutation_key'], cid, digest, encoded(result)))
            return result

    def guard(self, cid, tid, ledger, job_run_id=None):
        if time.monotonic() >= ledger.deadline:
            raise Failure('timeout', 'Reminder operation reached the shared turn deadline.')
        ledger.spend_sql(100)
        current = self.status(cid)
        if job_run_id is not None:
            definition=self.store.jobs.check_run(job_run_id)
            if definition.kind!='research' or definition.conversation_id!=cid or tid!=job_run_id or not current['enabled'] or not current['readable']:
                raise Failure('reminder_forbidden','This run has no current reminder read permission.')
            return
        turn = self.db.execute('SELECT status,conversation_id FROM recall_turns WHERE id=?', (tid,)).fetchone()
        if not current['enabled'] or not current['readable'] or turn is None or turn['status'] != 'running' or turn['conversation_id'] != cid:
            raise Failure('reminder_forbidden', 'Current reminder permission and a genuine running foreground turn are required.')

    @contextmanager
    def actor(self, cid, tid, ledger, job_run_id=None):
        if self.owner.actor is not None:
            raise Failure('reminder_busy', 'Another reminder actor is active.')
        self.guard(cid, tid, ledger, job_run_id)
        self.owner.actor = (cid, tid, ledger, job_run_id)
        try:
            yield
        finally:
            self.owner.actor = None

    def _support(self, cid, tid, quote):
        row = self.db.execute('SELECT user_id FROM recall_turns WHERE id=? AND conversation_id=?', (tid, cid)).fetchone()
        if row is None:
            raise Failure('reminder_source_unavailable', 'A genuine current user message is required.')
        source = self.db.execute('SELECT sha256 FROM artifacts WHERE id=?', (row[0],)).fetchone()
        support = ReminderSupport(cid, row[0], source[0], quote)
        self.owner._support(support)
        return support

    def _sign(self, data):
        raw = encoded([self.store.state_id, data]).encode()
        key = self.db.execute('SELECT handle_key FROM memory_banks WHERE slot=1').fetchone()[0]
        return base64.urlsafe_b64encode(raw + hmac.digest(key, raw, 'sha256')).decode().rstrip('=')

    def _decode(self, value):
        try:
            raw = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
            body, tag = raw[:-32], raw[-32:]
            key = self.db.execute('SELECT handle_key FROM memory_banks WHERE slot=1').fetchone()[0]
            if not hmac.compare_digest(tag, hmac.digest(key, body, 'sha256')):
                raise ValueError
            state, data = json.loads(body)
            if state != self.store.state_id:
                raise ValueError
            return data
        except (ValueError, TypeError, UnicodeError):
            raise Failure('reminder_reference', 'Use the exact reference from a current reminder read.') from None

    def _current(self, ident):
        row, value = self.owner._reminder(ident), self.owner._definition(ident)
        self.owner._state(value)
        return row, value

    def _read_ref(self, row, value):
        return self._sign(['read', row['id'], row['revision'], bool(row['cancelled']), value.digest])

    def _from_ref(self, ref, reads):
        if ref not in reads:
            raise Failure('reminder_read_required', 'Read the complete current reminder in this turn before changing it.')
        data = self._decode(ref)
        if not isinstance(data, list) or len(data) != 5 or data[0] != 'read':
            raise Failure('reminder_reference', 'Use a reminder read reference, not a page cursor.')
        row, value = self._current(data[1])
        if self._read_ref(row, value) != ref:
            raise Failure('reminder_conflict', 'Reminder state changed; read its current definition.')
        return row, value

    def execute(self, args, *, cid, tid, ledger, budget, reads, notices, job_run_id=None):
        now = datetime.now(timezone.utc).isoformat()
        action = args['action']
        if job_run_id is not None and action not in ('list','read','pending','outcome'):
            raise Failure('reminder_forbidden','Background tasks have read-only reminder access.')
        with self.actor(cid, tid, ledger, job_run_id):
            if action in ('save', 'cancel', 'dismiss'):
                support = self._support(cid, tid, args['source_quote'])
                # Reserve the worst whole receipt before an effect; the shared
                # ledger owns actual escaped-envelope fit, never a second limit.
                envelope = dict(status='committed', outcome='acknowledged', reminder_id='f'*32,
                    revision=9007199254740991, occurrence_id='f'*32, occurrence_version=9007199254740991,
                    channel='explicit', replayed=False, mutation_key='f'*32)
                if not budget.fits(envelope):
                    raise Failure('reminder_result_capacity', 'The complete mutation receipt does not fit; nothing changed.')
                ledger.spend_payload(len(encoded(envelope).encode()))
                key = mutation_key(self.store.state_id, tid, args)
                if action == 'save':
                    value = definition(args['definition'])
                    row, old = self._from_ref(args['read_ref'], reads) if 'read_ref' in args else (None, None)
                    try:
                        value = replace(value, support=(support,), note_id=old.note_id if old else None,
                                        task_id=old.task_id if old else None)
                    except ValueError as error:
                        raise Failure('reminder_definition', str(error)) from None
                    result = self.owner.save(value, mutation_key=key, now=now,
                        reminder_id=row['id'] if row else None, expected_revision=row['revision'] if row else None,
                        reactivate=args.get('reactivate', False))
                elif action == 'cancel':
                    row, _ = self._from_ref(args['read_ref'], reads)
                    result = self.owner.cancel(row['id'], expected_revision=row['revision'], mutation_key=key, now=now)
                else:
                    if (args['occurrence_id'], args['receipt']) not in notices:
                        raise Failure('reminder_read_required', 'Read this pending notice in the current turn before an explicit dismissal.')
                    result = self.owner.acknowledge(args['occurrence_id'], args['receipt'], mutation_key=key, channel='explicit', now=now)
                result = dict(result, outcome=result['status'], status='unchanged' if result['status']=='unchanged' else 'committed', mutation_key=key)
            elif action == 'outcome':
                result = self.owner.lookup(args['mutation_key'])
                if result['status'] == 'absent':
                    result = dict(status='not_recorded', mutation_key=args['mutation_key'])
                else:
                    with self.owner._transaction():
                        self._current(result['reminder_id'])
                    result = dict(result, outcome=result['status'], status='unchanged' if result['status']=='unchanged' else 'committed',
                                  mutation_key=args['mutation_key'])
            elif action == 'read':
                with self.owner._transaction():
                    row, value = self._current(args['reminder_id'])
                    result = dict(status='complete', reminder=dict(id=row['id'], revision=row['revision'], cancelled=bool(row['cancelled']),
                        title=value.title, text=value.text, schedule=value.record()['schedule'],
                        enabled=bool(row['enabled']), read_ref=self._read_ref(row, value)), now=now)
            elif action in ('list', 'pending'):
                scope = action
                start = self._decode(args['cursor']) if args.get('cursor') else [scope, '' if action=='list' else 0]
                if not isinstance(start, list) or len(start) != 2 or start[0] != scope:
                    raise Failure('reminder_page', 'This cursor belongs to a different reminder page.')
                if action == 'pending':
                    page = self.owner.pending(after=start[1], limit=args.get('limit', 20))
                    items = [{k:v for k,v in row.items() if k != 'links'} for row in page['items']]
                else:
                    page = self.owner.page(after=start[1], limit=args.get('limit', 20))
                    items = []
                    with self.owner._transaction():
                        for row in page['items']:
                            try:
                                self._current(row['id'])
                            except Failure as error:
                                if error.code in ('reminder_source_unavailable', 'reminder_source_changed'):
                                    continue
                                raise
                            items.append(dict(id=row['id'], title=row['title'], enabled=bool(row['enabled']), cancelled=bool(row['cancelled'])))
                result = dict(status='complete', items=items,
                    next_cursor=self._sign([scope, page['next_cursor']]) if page['next_cursor'] is not None else None, now=now)
            else:
                raise Failure('reminder_arguments', 'Unsupported reminder action.')
            if action not in ('save', 'cancel', 'dismiss'):
                ledger.spend_payload(len(encoded(result).encode()))
            if not budget.fits(result):
                # Reads are whole records/pages, never cropped into false write
                # authority. The model can ask for a smaller discovery page.
                raise Failure('reminder_result_capacity', 'The complete reminder result does not fit; reduce the page size.')
            return result

    def history_actions(self, past_turn, *, cid, tid, ledger):
        """Body-free durable outcome evidence, including commit-before-reply loss."""
        limits = self.store.execution.for_turn(past_turn)
        with self.actor(cid, tid, ledger):
            with self.owner._transaction():
                rows = self.db.execute('''SELECT h.arguments FROM history_operations h JOIN recall_turns t ON t.id=h.turn_id
                    WHERE h.turn_id=? AND t.conversation_id=? AND t.status!='running' AND h.name='reminders'
                    ORDER BY h.ordinal LIMIT ?''', (past_turn, cid, limits.operations+1)).fetchall()
            if len(rows)>limits.operations:
                raise Failure('history_work_limit', 'Reminder history exceeds the saved operation bound.')
            result=[]
            for row in rows:
                try:
                    key=json.loads(row['arguments']).get('mutation_key')
                    if key is None:
                        continue
                    if not identity(key):
                        raise ValueError
                    receipt=self.owner.lookup(key)
                    if receipt['status']=='absent':
                        result.append(dict(mutation_key=key,status='not_recorded'))
                        continue
                    with self.owner._transaction():
                        self._current(receipt['reminder_id'])
                    if receipt['status'] not in ('committed','unchanged','cancelled','acknowledged'):
                        raise ValueError
                    result.append(dict(mutation_key=key,status=receipt['status'],reminder_id=receipt['reminder_id']))
                except Failure as error:
                    if error.code in ('history_work_limit','timeout','reminder_forbidden'):
                        raise
                    result.append(dict(status='unavailable'))
                except (ValueError,TypeError,AttributeError):
                    result.append(dict(status='unknown'))
            ledger.spend_payload(len(encoded(result).encode()))
            return result
