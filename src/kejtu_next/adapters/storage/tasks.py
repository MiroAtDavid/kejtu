"""One transactional task owner for local controls and model operations.

Task history survives source loss. Eligibility is checked at read time; references
do not confer lifetime ownership. Nothing here starts timers, executes tasks, or
changes state during reads. Retained task text participates in note-copy checks.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import sqlite3
import time
import uuid

from ...core.records import Failure
from ...core.tasks import validate
from .history import encoded

TASK_SCHEMA = '''
CREATE TABLE personal_tasks(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,title TEXT NOT NULL,
 due TEXT,note_id TEXT,outcome TEXT NOT NULL CHECK(outcome IN ('open','done','cancelled')),
 attention TEXT NOT NULL CHECK(attention IN ('active','parked','archived')),
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE personal_task_sources(task_id TEXT NOT NULL REFERENCES personal_tasks(id),
 user_id TEXT NOT NULL,quote TEXT NOT NULL,revision INTEGER NOT NULL,PRIMARY KEY(user_id,quote));
CREATE TABLE personal_task_history(task_id TEXT NOT NULL REFERENCES personal_tasks(id),
 revision INTEGER NOT NULL,action TEXT NOT NULL,record TEXT NOT NULL,
 PRIMARY KEY(task_id,revision));
CREATE TABLE personal_task_operations(operation_key TEXT PRIMARY KEY,intent TEXT NOT NULL,
 task_id TEXT NOT NULL REFERENCES personal_tasks(id),revision INTEGER NOT NULL,status TEXT NOT NULL,
 turn_id TEXT);
CREATE TABLE personal_task_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1),epoch INTEGER NOT NULL);
INSERT INTO personal_task_state VALUES(1,0);
CREATE INDEX personal_task_listing ON personal_tasks(outcome,attention,due,id);
CREATE INDEX personal_task_source_task ON personal_task_sources(task_id);
CREATE INDEX personal_task_turn ON personal_task_operations(turn_id);
'''

# Current capability has its own owner. Configuration artifacts keep their
# original creation intent; enabling tasks later never rewrites past prompts.
TASK_ACCESS_SCHEMA = '''
CREATE TABLE personal_task_grants(conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),revision INTEGER NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE personal_task_grant_operations(mutation_key TEXT PRIMARY KEY,
 conversation_id TEXT NOT NULL REFERENCES conversations(id),intent TEXT NOT NULL,receipt TEXT NOT NULL);
CREATE INDEX personal_task_grant_operations_conversation ON personal_task_grant_operations(conversation_id);
'''


class TaskStore:
    def __init__(self, store):
        self.store, self.db = store, store.db
        self._ledger = None

    def _query(self, sql, parameters=()):
        if self._ledger:
            self._ledger.spend_sql(100)  # Each short statement's final VM quantum.
        return self.db.execute(sql,parameters)

    def epoch(self):
        return self._query('SELECT epoch FROM personal_task_state WHERE singleton=1').fetchone()[0] + self.store.access_settings.value['revision']

    def lookup(self, key):
        with self.transaction():
            row = self._query('SELECT * FROM personal_task_operations WHERE operation_key=?', (key,)).fetchone()
            return (dict(status=row['status'],task_id=row['task_id'],revision=row['revision'],replayed=True)
                    if row else dict(status='absent'))

    def _access_status(self, conversation_id):
        saved = self.store.conversation(conversation_id)
        row = self._query('SELECT enabled,revision FROM personal_task_grants WHERE conversation_id=?',
                          (conversation_id,)).fetchone()
        if row is None:
            raise Failure('store_integrity', 'Task capability record is missing; no permission was inferred.')
        readable = saved.memory_bank_id is not None and self._query(
            'SELECT 1 FROM memory_grants WHERE conversation_id=? AND bank_id=? AND enabled=1',
            (conversation_id, saved.memory_bank_id)).fetchone() is not None
        return dict(conversation_id=conversation_id, enabled=saved.tasks_enabled, revision=row['revision'],
                    readable=readable, writable=bool(readable and saved.notes_writable))

    def access(self, args):
        """Local user control, not a model tool or a source of write authority."""
        action, cid = args.get('action'), args.get('conversation_id')
        fields = {'read': {'action','conversation_id'},
                  'lookup': {'action','conversation_id','mutation_key'},
                  'set': {'action','conversation_id','mutation_key','enabled','expected_revision'}}
        if action not in fields or set(args) != fields[action] or not self.store._identity(cid):
            raise Failure('invalid_request', 'Invalid task capability request.')
        if action != 'read' and not self.store._identity(args['mutation_key']):
            raise Failure('invalid_request', 'Task capability changes need an exact mutation identity.')
        if action == 'set' and (type(args['enabled']) is not bool or type(args['expected_revision']) is not int
                or not 0 <= args['expected_revision'] < 9007199254740991):
            raise Failure('invalid_request', 'Task capability changes need an enabled value and current revision.')
        with self.transaction(write=action == 'set'):
            current = self._access_status(cid)
            if action == 'read':
                return current
            prior = self._query('SELECT * FROM personal_task_grant_operations WHERE mutation_key=?',
                                (args['mutation_key'],)).fetchone()
            if action == 'lookup':
                return (json.loads(prior['receipt']) | {'replayed': True}
                        if prior and prior['conversation_id'] == cid else dict(status='absent'))
            intent = encoded({key: args[key] for key in ('conversation_id','enabled','expected_revision')})
            if prior:
                if prior['intent'] != intent or prior['conversation_id'] != cid:
                    raise Failure('task_access_conflict', 'This mutation key belongs to a different task capability change.')
                return json.loads(prior['receipt']) | {'replayed': True}
            self.store.access_settings.require_conversation_control('tasks')
            if args['expected_revision'] != current['revision']:
                raise Failure('task_access_conflict', 'Task access changed. Read its current state before choosing again.')
            if args['enabled'] and not current['readable']:
                raise Failure('task_forbidden', 'Enable the existing conversation memory permission first; task access cannot grant it.')
            changed = args['enabled'] != current['enabled']
            revision = current['revision'] + int(changed)
            if changed:
                self._query('UPDATE personal_task_grants SET enabled=?,revision=?,updated_at=? WHERE conversation_id=?',
                            (int(args['enabled']), revision, datetime.now(timezone.utc).isoformat(), cid))
                # In-flight task contexts must recheck rather than retain a
                # stale grant or reuse a summary made under different access.
                self._query('UPDATE personal_task_state SET epoch=epoch+1 WHERE singleton=1')
            receipt = dict(status='committed' if changed else 'unchanged', conversation_id=cid,
                           mutation_key=args['mutation_key'], enabled=args['enabled'], revision=revision, replayed=False)
            self._query('INSERT INTO personal_task_grant_operations VALUES(?,?,?,?)',
                        (args['mutation_key'], cid, intent, encoded(receipt)))
            return receipt

    def history_actions(self, turn_id, ledger):
        with self.transaction(ledger):
            rows = self._query('''SELECT DISTINCT t.id,t.revision,t.outcome,t.attention FROM personal_task_operations o
                JOIN personal_tasks t ON t.id=o.task_id WHERE o.turn_id=? ORDER BY t.id LIMIT ?''',
                (turn_id,self.store.execution.for_turn(turn_id).operations)).fetchall()
            return [dict(row) for row in rows if self._eligible(row['id'])]

    @contextmanager
    def transaction(self, ledger=None, *, write=False):
        if self.db.in_transaction:
            raise Failure('task_busy', 'Another transaction owns the store; retry after it finishes.')
        deadline = ledger.deadline if ledger else time.monotonic() + 5
        steps, stopped = 0, None
        def progress():
            nonlocal steps, stopped
            steps += 100
            try:
                if ledger:
                    ledger.spend_sql(100)
                elif steps > 200000:
                    raise Failure('task_work_limit', 'Task maintenance work bound reached.')
                if time.monotonic() >= deadline:
                    raise Failure('timeout', 'Task access reached its deadline.')
            except Failure as error:
                stopped = error
                return 1
            return 0
        if progress():
            raise stopped
        self.db.set_progress_handler(progress, 100)
        self._ledger = ledger
        committing = False
        try:
            self._query('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield
            if progress():
                raise stopped
            committing = True
            self.db.commit()
        except BaseException as error:
            uncertain = write and committing and not self.db.in_transaction
            self.db.set_progress_handler(None, 0)
            self.db.rollback()
            if uncertain:
                raise Failure('task_outcome_unknown', 'Resolve the same operation before starting a new intent.') from error
            if stopped:
                raise stopped from None
            if isinstance(error, sqlite3.Error):
                raise Failure('task_store_unavailable', 'Task storage failed; inspect the operation before retrying.') from error
            raise
        finally:
            self.db.set_progress_handler(None, 0)
            self._ledger = None

    def _key(self):
        return self._query('SELECT handle_key FROM memory_banks WHERE slot=1').fetchone()[0]

    def _ref(self, row):
        packet = encoded([self.store.state_id, row['id'], row['revision']]).encode()
        return 'T1.' + base64.urlsafe_b64encode(packet + hmac.digest(self._key(), packet, 'sha256')).decode().rstrip('=')

    def _resolve(self, ref, *, current=False):
        try:
            if not ref.startswith('T1.'):
                raise ValueError
            raw = base64.b64decode(ref[3:] + '=' * (-len(ref[3:]) % 4), altchars=b'-_', validate=True)
            packet, tag = raw[:-32], raw[-32:]
            if not hmac.compare_digest(hmac.digest(self._key(), packet, 'sha256'), tag):
                raise ValueError
            state, ident, revision = json.loads(packet)
            if state != self.store.state_id or type(revision) is not int:
                raise ValueError
            row = self._query('SELECT * FROM personal_tasks WHERE id=?', (ident,)).fetchone()
            if row is None:
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise Failure('task_reference', 'Task reference is unavailable; list tasks again.') from None
        if current and row['revision'] != revision:
            raise Failure('task_conflict', 'Task changed. Read its current state; keep your draft and choose the intended change.')
        return row

    def _eligible(self, task_id):
        return self._query('''SELECT 1 FROM personal_task_sources s WHERE s.task_id=? AND NOT EXISTS(
            SELECT 1 FROM recall_turns t JOIN artifacts a ON a.id=t.user_id
            JOIN memory_grants g ON g.conversation_id=t.conversation_id AND g.enabled=1
            WHERE t.user_id=s.user_id) LIMIT 1''', (task_id,)).fetchone() is None

    def _description(self, row):
        # The current revision owns its exact body. Older task revisions had no
        # description; reading them supplies an empty body without rewriting history.
        saved = self._query('SELECT record FROM personal_task_history WHERE task_id=? AND revision=?',
                            (row['id'],row['revision'])).fetchone()
        return json.loads(saved['record']).get('description','') if saved else ''

    def _record(self, row):
        value = dict(row)
        value['description'] = self._description(row)
        value['placements'] = self.store.planner.task_placements(row['id'])
        value['read_ref'] = self._ref(row)
        source = self._query('SELECT 1 FROM personal_task_sources WHERE task_id=? LIMIT 1', (row['id'],)).fetchone()
        value['source_status'] = 'manual' if source is None else 'available' if self._eligible(row['id']) else 'unavailable'
        value['note_status'] = 'none' if row['note_id'] is None else 'available' if self._query(
            'SELECT 1 FROM memory_records WHERE id=? AND readable=1 AND validity=\'current\'', (row['note_id'],)).fetchone() else 'unavailable'
        return value

    def _authority(self, conversation_id, writing):
        conversation = self.store.conversation(conversation_id)
        if not conversation.tasks_enabled or self._query(
                'SELECT 1 FROM memory_grants WHERE conversation_id=? AND enabled=1', (conversation_id,)).fetchone() is None:
            raise Failure('task_forbidden', 'Task recall is not granted. Local controls remain available.')
        if writing and not conversation.notes_writable:
            raise Failure('task_forbidden', 'Task writing is not granted. Local controls remain available.')

    def _fit(self, result, maximum, budget, ledger):
        size = len(encoded(result).encode())
        if size > maximum or budget is not None and not budget.fits(result):
            raise Failure('task_result_capacity', 'The complete task result cannot fit; no change committed.')
        return result

    def execute(self, args, *, conversation_id=None, turn_id=None, operation_key=None,
                ledger=None, maximum=65536, budget=None, read_refs=frozenset()):
        manual = conversation_id is None
        args = validate(args, manual=manual)
        action = args['action']
        writing = action in ('save', 'transition')
        with self.transaction(ledger, write=writing):
            if not manual:
                self._authority(conversation_id, writing)
            fit = lambda result: self._fit(result, maximum, budget, ledger)
            if not writing:
                result = self._read(args, manual, fit)
                if ledger:
                    ledger.spend_payload(len(encoded(result).encode()))
                return result
            key = args['mutation_key'] if manual else operation_key
            if not isinstance(key, str) or not 1 <= len(key) <= 256:
                raise Failure('task_arguments', 'An application operation identity is required.')
            intent = hashlib.sha256(encoded([conversation_id, {k:v for k,v in args.items() if k != 'mutation_key'}]).encode()).hexdigest()
            prior = self._query('SELECT * FROM personal_task_operations WHERE operation_key=?', (key,)).fetchone()
            if prior:
                if prior['intent'] != intent:
                    raise Failure('task_conflict', 'Operation already belongs to a different intent; preserve the draft.')
                result = fit(dict(status=prior['status'], task_id=prior['task_id'], revision=prior['revision'], replayed=True))
                if ledger:
                    ledger.spend_payload(len(encoded(result).encode()))
                return result
            old = self._resolve(args['read_ref'], current=True) if args.get('read_ref') else None
            if old is not None:
                old = dict(old, description=self._description(old))
            placement_changed = False
            if 'placement' in args:
                settings = self.store.planner.settings()['settings']
                if not settings['university_enabled'] or not manual and not settings['model_write']:
                    raise Failure('task_placement_forbidden','Project placement needs the existing workspace permission.')
                placement_changed = self.store.planner.placement_changed(old['id'] if old else None,args['placement'])
            if old is not None and not manual:
                if not self._eligible(old['id']):
                    raise Failure('task_source_unavailable', 'Supporting source is unavailable; use local controls.')
                if args['read_ref'] not in read_refs:
                    raise Failure('task_read_required', 'Get the current task with list or read before changing it; use its returned read_ref.')
            source = None
            if not manual:
                turn = self.store._row(turn_id)
                if turn['conversation_id'] != conversation_id or turn['status'] != 'running':
                    raise Failure('task_forbidden', 'Task mutation requires the current active user turn.')
                if action == 'save':
                    quote = args.get('source_quote')
                    original = self.store._body(turn['user_id'])
                    if ledger:
                        ledger.spend_payload(len(original.encode()))
                    if not isinstance(quote, str) or not quote.strip() or quote not in original:
                        raise Failure('task_quote', 'Save needs an exact quotation from the current user message.')
                    source = (turn['user_id'], quote)
            # Exact-source repeats resolve existing work without reopening or
            # overwriting it. Different quotations still need model judgement.
            duplicate = self._query('SELECT task_id FROM personal_task_sources WHERE user_id=? AND quote=?', source).fetchone() if source else None
            if duplicate and old is not None and duplicate[0] != old['id']:
                raise Failure('task_conflict', 'This action source belongs to another task.')
            if duplicate and old is None:
                row = self._query('SELECT * FROM personal_tasks WHERE id=?', (duplicate[0],)).fetchone()
                status = 'unchanged'
            else:
                if source and old is None and self._query('''SELECT 1 FROM note_metadata m
                    JOIN memory_records r ON r.head_revision=m.revision_id
                    WHERE r.readable=1 AND r.validity='current' AND json_extract(m.body,'$.purpose')='open_thread'
                    AND json_extract(m.body,'$.attribution.artifact_id')=?
                    AND json_extract(m.body,'$.attribution.source_quote')=? LIMIT 1''',source).fetchone():
                    raise Failure('task_legacy_conflict', 'This action already has an open-thread note. Keep using that record; automatic conversion is unavailable.')
                stamp = datetime.now(timezone.utc).isoformat()
                row = dict(old) if old else dict(id=uuid.uuid4().hex, revision=0, title='', description='', due=None,
                    note_id=None, outcome='open', attention='active', created_at=stamp, updated_at=stamp)
                fields = ('title','description','due','note_id') if action == 'save' else ('outcome','attention')
                for name in fields:
                    if name in args:
                        row[name] = args[name]
                if row['note_id'] is not None and (old is None or row['note_id'] != old['note_id']):
                    if self._query('''SELECT 1 FROM note_records n JOIN memory_records r ON r.id=n.record_id
                            WHERE r.id=? AND r.readable=1 AND r.validity='current' ''', (row['note_id'],)).fetchone() is None:
                        raise Failure('task_note_unavailable', 'Choose an available note or explicitly clear the link.')
                new_source = source and self._query('SELECT 1 FROM personal_task_sources WHERE user_id=? AND quote=?',source).fetchone() is None
                status = 'unchanged' if old and all(row[k] == old[k] for k in fields) and not new_source and not placement_changed else 'committed'
                if status == 'committed':
                    if old is None and self._query('SELECT count(*) FROM personal_tasks').fetchone()[0] >= 10000:
                        raise Failure('task_store_limit', 'Development task bound reached; existing tasks remain editable.')
                    row['revision'] += 1
                    row['updated_at'] = stamp
                    if old is None:
                        self._query('INSERT INTO personal_tasks VALUES(?,?,?,?,?,?,?,?,?)', tuple(row[k] for k in
                            ('id','revision','title','due','note_id','outcome','attention','created_at','updated_at')))
                    else:
                        self._query('''UPDATE personal_tasks SET revision=?,title=?,due=?,note_id=?,outcome=?,attention=?,updated_at=? WHERE id=?''',
                            tuple(row[k] for k in ('revision','title','due','note_id','outcome','attention','updated_at','id')))
                    if placement_changed:
                        self.store.planner.set_task_placement(row['id'],args['placement'],key)
                    row['placements'] = self.store.planner.task_placements(row['id'])
                    if source:
                        self._query('INSERT OR IGNORE INTO personal_task_sources VALUES(?,?,?,?)', (row['id'],*source,row['revision']))
                    self._query('INSERT INTO personal_task_history VALUES(?,?,?,?)',
                        (row['id'],row['revision'],'create' if old is None else action,encoded(row)))
                    self._query('UPDATE personal_task_state SET epoch=epoch+1 WHERE singleton=1')
            result = fit(dict(status=status, task_id=row['id'], revision=row['revision'], replayed=False))
            if ledger:
                ledger.spend_payload(len(encoded(result).encode()))
            self._query('INSERT INTO personal_task_operations VALUES(?,?,?,?,?,?)',
                (key,intent,row['id'],row['revision'],status,turn_id))
            return result

    def _read(self, args, manual, fit):
        offset, limit = args.get('offset',0), args.get('limit',12)
        if args['action'] == 'read':
            row = self._resolve(args['read_ref'])
            if not manual and not self._eligible(row['id']):
                raise Failure('task_source_unavailable', 'Supporting source is unavailable; local controls remain usable.')
            if 'view' not in args:
                # Ordinary reads authorize edits from the complete current head.
                # Historical bodies consume context only when explicitly asked
                # for; retain the wire envelope without implying a history page.
                return fit(dict(status='complete', task=self._record(row), history=[], next_offset=None))
            if args.get('view') == 'sources':
                entries = self._query('''SELECT s.user_id,s.quote,s.revision,t.id AS turn_id,t.conversation_id
                    FROM personal_task_sources s LEFT JOIN recall_turns t ON t.user_id=s.user_id
                    WHERE s.task_id=? ORDER BY s.revision,s.user_id,s.quote LIMIT ? OFFSET ?''',
                    (row['id'],limit+1,offset)).fetchall()
                result = dict(status='complete',task=self._record(row),history=[],sources=[],next_offset=None)
                fit(result)
                for entry in entries[:limit]:
                    candidate = dict(result,sources=result['sources']+[dict(entry)],next_offset=offset+len(result['sources'])+1)
                    try: fit(candidate)
                    except Failure as error:
                        if error.code != 'task_result_capacity': raise
                        break
                    result=candidate
                count=len(result['sources'])
                result['next_offset']=offset+count if len(entries)>count else None
                if entries and not count:
                    raise Failure('task_result_capacity','One exact source cannot fit; no truncated quotation was delivered.')
                return fit(result)
            entries = self._query('SELECT record,action FROM personal_task_history WHERE task_id=? ORDER BY revision DESC LIMIT ? OFFSET ?',
                (row['id'],limit+1,offset)).fetchall()
            result = dict(status='complete',task=self._record(row),history=[],next_offset=None)
            fit(result)
            for entry in entries[:limit]:
                candidate = dict(result,history=result['history']+[dict(json.loads(entry['record']),action=entry['action'])],next_offset=offset+len(result['history'])+1)
                try:
                    fit(candidate)
                except Failure as error:
                    if error.code != 'task_result_capacity': raise
                    break
                result = candidate
            count = len(result['history'])
            result['next_offset'] = offset+count if len(entries)>count else None
            if entries and not count:
                raise Failure('task_result_capacity','Task history page cannot fit; request a smaller page.')
            return fit(result)
        outcome, attention = args.get('outcome','open'),args.get('attention','all')
        clauses, params = [], []
        for name, value in (('outcome',outcome),('attention',attention)):
            if value != 'all': clauses.append('t.'+name+'=?'); params.append(value)
        if args.get('project_id'):
            clauses.append("""EXISTS(SELECT 1 FROM planner_records l LEFT JOIN planner_records c
                ON c.kind='course' AND c.id=json_extract(l.body,'$.course_id')
                WHERE l.kind='task_link' AND l.cancelled=0 AND json_extract(l.body,'$.task_id')=t.id
                AND (json_extract(l.body,'$.semester_id')=? OR json_extract(c.body,'$.semester_id')=?))""")
            params.extend([args['project_id']]*2)
        if args.get('section_id'):
            clauses.append("""EXISTS(SELECT 1 FROM planner_records l WHERE l.kind='task_link' AND l.cancelled=0
                AND json_extract(l.body,'$.task_id')=t.id AND json_extract(l.body,'$.course_id')=?)""")
            params.append(args['section_id'])
        if args.get('query'):
            self.db.create_function('task_casefold',1,str.casefold,deterministic=True)
            clauses.append('instr(task_casefold(t.title),?)>0'); params.append(args['query'].casefold())
        eligibility = '''NOT EXISTS(SELECT 1 FROM personal_task_sources s WHERE s.task_id=t.id AND NOT EXISTS(
            SELECT 1 FROM recall_turns u JOIN artifacts a ON a.id=u.user_id JOIN memory_grants g ON g.conversation_id=u.conversation_id
            WHERE u.user_id=s.user_id AND g.enabled=1))'''
        if not manual: clauses.append(eligibility)
        where = ' AND '.join(clauses) or '1'
        total = self._query('SELECT count(*) FROM personal_tasks t WHERE '+where,params).fetchone()[0]
        opened = self._query("SELECT count(*) FROM personal_tasks t WHERE outcome='open' AND "+('1' if manual else eligibility)).fetchone()[0]
        rows = self._query('SELECT * FROM personal_tasks t WHERE '+where+
            ' ORDER BY due IS NULL,due,created_at,id LIMIT ? OFFSET ?', (*params,limit,offset)).fetchall()
        result = dict(status='complete',items=[],total=total,open_total=opened,next_offset=None)
        fit(result)
        for row in rows:
            item = self._record(row)
            if args.get('day'):
                item['day_group'] = 'quiet' if row['outcome']!='open' or row['attention']!='active' else 'undated' if row['due'] is None else 'overdue' if row['due']<args['day'] else 'due' if row['due']==args['day'] else 'future'
            candidate = dict(result,items=result['items']+[item],next_offset=offset+len(result['items'])+1)
            try: fit(candidate)
            except Failure as error:
                if error.code != 'task_result_capacity': raise
                break
            result = candidate
        count=len(result['items'])
        result['next_offset']=offset+count if offset+count<total else None
        if total>offset and not count:
            raise Failure('task_result_capacity','One complete task cannot fit; no partial task was delivered.')
        return fit(result)
