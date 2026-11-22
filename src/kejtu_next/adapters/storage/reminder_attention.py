"""Pending attention and exact conversational admissions over ReminderStore.

Reading, offering and mentioning never acknowledge an occurrence. Snoozes leave
its schedule intact. Legacy transcript snoozes retain their original identities.
"""
import hashlib
import hmac
import json
from datetime import timedelta

from ...core.records import Failure
from ...core.reminders import encoded
from ...core.schedules import instant, utc

OFFER_HISTORY_INDEX = 'CREATE INDEX reminder_offer_history ON reminder_offers(occurrence_id,reminder_revision,attention_revision);'

REMINDER_ATTENTION_SCHEMA = '''
CREATE TABLE reminder_snoozes(occurrence_id TEXT PRIMARY KEY REFERENCES reminder_occurrences(id),
 revision INTEGER NOT NULL, until TEXT NOT NULL);
CREATE TABLE reminder_offers(turn_id TEXT PRIMARY KEY REFERENCES turns(id) ON DELETE CASCADE,
 occurrence_id TEXT NOT NULL REFERENCES reminder_occurrences(id), reminder_revision INTEGER NOT NULL,
 attention_revision INTEGER NOT NULL, reference_id TEXT NOT NULL UNIQUE, admission TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('offered','mentioned','unused','uncertain')), created_at TEXT NOT NULL);
CREATE UNIQUE INDEX reminder_offer_once ON reminder_offers(occurrence_id,reminder_revision,attention_revision) WHERE status!='unused';
''' + OFFER_HISTORY_INDEX + '''
CREATE TABLE reminder_reply_references(turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
 answer_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
 reference_id TEXT NOT NULL REFERENCES reminder_offers(reference_id) ON DELETE CASCADE,
 record TEXT NOT NULL, PRIMARY KEY(turn_id,reference_id));
'''


class ReminderAttention:
    def __init__(self, store):
        self.store, self.db, self.owner = store, store.db, store.reminders

    def _rows(self, after, limit, *, unoffered=False, now=None, include_companion=True):
        return self.db.execute('''SELECT o.*,v.reminder_revision,v.first_due,v.covered_through,
            s.until,s.revision AS snooze_revision,m.status AS legacy_status,m.due_at AS legacy_due,
            m.revision AS legacy_revision,m.reminder_revision AS legacy_definition
            FROM reminder_occurrences o JOIN reminder_occurrence_versions v
            ON v.occurrence_id=o.id AND v.version=o.version
            LEFT JOIN reminder_snoozes s ON s.occurrence_id=o.id
            LEFT JOIN reminder_chat_messages m ON m.occurrence_id=o.id
            JOIN reminders r ON r.id=o.reminder_id
            JOIN reminder_versions d ON d.reminder_id=r.id AND d.revision=r.revision
            WHERE (o.status='pending' OR m.status='snoozed') AND o.sequence>?
            AND (? OR coalesce(json_extract(d.definition,'$.origin'),'requested')='requested')
            AND r.enabled=1 AND r.cancelled=0 AND r.revision=
                CASE WHEN o.status='pending' THEN v.reminder_revision ELSE m.reminder_revision END
            AND (? IS NULL OR CASE WHEN o.status='pending' THEN coalesce(s.until,v.first_due) ELSE m.due_at END<=?)
            ''' + ('''AND NOT EXISTS(SELECT 1 FROM reminder_offers f WHERE f.occurrence_id=o.id
                AND f.reminder_revision=v.reminder_revision AND f.attention_revision=
                CASE WHEN o.status='pending' THEN coalesce(s.revision,0) ELSE m.revision END
                AND f.status!='unused') ''' if unoffered else '') +
            'ORDER BY o.sequence LIMIT ?', (after, include_companion, now, now, limit)).fetchall()

    def _item(self, row, now):
        legacy = row['status'] != 'pending'
        revision = row['legacy_definition'] if legacy else row['reminder_revision']
        current = self.owner._reminder(row['reminder_id'])
        if current['cancelled'] or not current['enabled'] or current['revision'] != revision:
            return None
        value = self.owner._definition(row['reminder_id'], revision)
        try:
            state = self.owner._state(value)
        except Failure as error:
            if error.code in ('reminder_source_unavailable', 'reminder_source_changed'):
                return None
            raise
        if value.task_id and (not state['task'] or state['task']['outcome'] != 'open'):
            return None
        due = row['legacy_due'] if legacy else row['until'] or row['first_due']
        if instant(due) > instant(now):
            return None
        attention_revision = row['legacy_revision'] if legacy else row['snooze_revision'] or 0
        task = self.db.execute('SELECT * FROM personal_tasks WHERE id=?',(value.task_id,)).fetchone() if value.task_id else None
        item = dict(id=row['id'], sequence=str(row['sequence']), reminder_id=row['reminder_id'],
            reminder_revision=revision, attention_revision=attention_revision,
            conversation_id=value.support[-1].conversation_id if value.support else None,
            title=value.title, text=value.text, due_at=due, timezone=value.schedule.timezone,
            event_at=value.event_at if instant(row['first_due'])==value.schedule.start else None, timing=value.timing, task_id=value.task_id,
            task_ref=self.store.personal_tasks._ref(task) if task else None)
        key = self.db.execute('SELECT handle_key FROM memory_banks WHERE slot=1').fetchone()[0]
        # Bind every effect and mention to the current occurrence AND source state.
        body = encoded(dict(item, state_id=self.store.state_id, definition=value.digest,
                            occurrence_version=row['version'], state=state))
        item['receipt'] = hmac.digest(key, body.encode(), 'sha256').hex()
        return item

    def _one(self, ident, now):
        self.owner._id(ident)
        row = self.db.execute('SELECT sequence FROM reminder_occurrences WHERE id=?', (ident,)).fetchone()
        if row:
            rows = self._rows(row[0]-1, 1)
            if rows and rows[0]['id'] == ident:
                return rows[0], self._item(rows[0], now)
        return None, None

    def page(self, *, now, after=0, limit=20, include_companion=False):
        self.owner._page_limit(limit)
        if type(after) is not int or after < 0:
            raise Failure('reminder_page', 'Use an exact reminder cursor.')
        with self.owner._transaction():
            rows = self._rows(after, limit+1, now=utc(instant(now)), include_companion=include_companion)
            return dict(items=[item for row in rows[:limit] if (item := self._item(row, now))],
                next_cursor=rows[limit-1]['sequence'] if len(rows)>limit else None)

    def change(self, args, *, now):
        action, ident = args['action'], args['id']
        if action not in ('acknowledge', 'later', 'resolve'):
            raise Failure('reminder_action', 'Choose Got it or a reminder time.')
        now = utc(instant(now))
        intent = {k:v for k,v in args.items() if k != 'mutation_key'}
        with self.owner._transaction(write=True):
            digest, prior = self.owner._receipt(args['mutation_key'], intent)
            if prior is not None:
                return dict(prior, mutation_key=args['mutation_key'], intent_sha256=digest)
            row, item = self._one(ident, now)
            if item is None or not isinstance(args['receipt'], str) or not hmac.compare_digest(item['receipt'], args['receipt']):
                raise Failure('reminder_stale', 'The reminder changed; refresh it before acting.')
            if self.owner.actor is not None and self._model_item(item,self.owner.actor[0]) is None:
                raise Failure('reminder_forbidden','The linked source is no longer available in this conversation.')
            due = item['due_at']
            if action == 'later':
                try:
                    due = utc(instant(args['until']))
                    if not instant(now) < instant(due) <= instant(now)+timedelta(days=366):
                        raise ValueError
                except (ValueError, TypeError, KeyError):
                    raise Failure('reminder_time', 'Choose a future reminder time within the next year.') from None
                if row['status'] == 'pending':
                    self.db.execute('''INSERT INTO reminder_snoozes VALUES(?,1,?) ON CONFLICT(occurrence_id)
                        DO UPDATE SET revision=revision+1,until=excluded.until''', (ident,due))
                else:
                    self.db.execute("UPDATE reminder_chat_messages SET due_at=?,revision=revision+1 WHERE occurrence_id=?", (due,ident))
            elif action == 'resolve':
                # The existing terminal cancellation state ends this occurrence
                # only. The operation receipt distinguishes model resolution from
                # cancelling a definition; no user-acknowledgement channel is set.
                if args.get('reason') not in ('handled','obsolete'):
                    raise Failure('reminder_action','Choose a reminder resolution reason.')
                if row['status'] == 'pending':
                    self.db.execute("UPDATE reminder_occurrences SET status='cancelled',finished_at=?,channel=NULL WHERE id=?", (now,ident))
            elif row['status'] == 'pending':
                notice = self.owner._notice(self.owner._reminder(item['reminder_id']), self.owner._pending(item['reminder_id']))
                key = hashlib.sha256((args['mutation_key']+':occurrence').encode()).hexdigest()[:32]
                self.owner._acknowledge(ident,notice['receipt'],mutation_key=key,channel='explicit',now=now)
            if action in ('acknowledge','resolve'):
                # Preserve old visible history without claiming a new presentation.
                self.db.execute('''UPDATE reminder_chat_messages SET status='dismissed',revision=revision+1,
                    after_sequence=coalesce(after_sequence,(SELECT max(sequence) FROM turns WHERE conversation_id=reminder_chat_messages.conversation_id))
                    WHERE occurrence_id=? AND status IN ('pending','snoozed')''', (ident,))
            result = dict(status='resolved' if action=='resolve' else 'snoozed' if action=='later' else 'acknowledged', id=ident,
                reminder_id=item['reminder_id'], due_at=due, mutation_key=args['mutation_key'],
                intent_sha256=digest, replayed=False)
            if action=='resolve':
                result.update(occurrence_id=ident,reason=args['reason'])
            return self.owner._record(args['mutation_key'],digest,result,now)

    def _model_item(self, item, cid):
        value = self.owner._definition(item['reminder_id'], item['reminder_revision'])
        if cid is None:
            if not value.support or any(not self.owner.model.status(source.conversation_id)['enabled']
                    or not self.owner.model.status(source.conversation_id)['readable'] for source in value.support):
                return None
            cid = value.support[-1].conversation_id
        bank = self.store.conversation(cid).memory_bank_id
        for source in value.support:
            grant = self.db.execute('SELECT bank_id FROM memory_grants WHERE conversation_id=? AND enabled=1', (source.conversation_id,)).fetchone()
            if not grant or grant[0] != bank:
                return None
        dependencies = [s.user_id for s in value.support]
        if value.note_id:
            # Legacy manual links have no retained snapshot proving what was
            # copied. Local attention remains usable without model admission.
            return None
        if value.task_id:
            # An old manual link does not establish which task material was copied.
            # Keep local controls, but never guess automatic model dependencies.
            if value.task_revision is None:
                return None
            if not self.store.conversation(cid).tasks_enabled or not self.store.personal_tasks._eligible(value.task_id):
                return None
            task = self.db.execute('SELECT * FROM personal_tasks WHERE id=?', (value.task_id,)).fetchone()
            dependencies.extend(self.store.personal_tasks._source_ids(dict(task,revision=value.task_revision)))
        return dict(item, origin=value.origin, source_dependencies=sorted(set(dependencies)))

    def model_page(self, cid, *, now, after=0, limit=5):
        page = self.page(now=now,after=after,limit=limit,include_companion=True)
        with self.owner._transaction():
            return dict(page,items=[value for item in page['items'] if (value := self._model_item(item,cid))])

    def context_page(self, cid, tid, ledger, now):
        with self.owner.model.actor(cid,tid,ledger):
            self.owner.poll(now)
            return self.model_page(cid,now=now,limit=5)

    def check_item(self, admitted, cid, tid, ledger, now):
        try:
            with self.owner.model.actor(cid, tid, ledger):
                with self.owner._transaction():
                    _, item = self._one(admitted['id'],now)
                    return bool(item and hmac.compare_digest(item['receipt'],admitted['receipt']) and self._model_item(item,cid))
        except Failure as error:
            if error.code in ('timeout','history_work_limit'):
                raise
            return False

    def offered(self, tid, item, now):
        with self.db:
            self.db.execute('INSERT INTO reminder_offers VALUES(?,?,?,?,?,?,?,?)',
                (tid,item['id'],item['reminder_revision'],item['attention_revision'],item['reference_id'],encoded(dict(item,presentation='context')),'offered',now))

    def admission(self, tid):
        row = self.db.execute('SELECT admission FROM reminder_offers WHERE turn_id=?', (tid,)).fetchone()
        return json.loads(row[0]) if row else None

    def check_offer(self, tid, cid, ledger, now):
        admitted = self.admission(tid)
        if not admitted:
            return False
        try:
            with self.owner.model.actor(cid,tid,ledger):
                with self.owner._transaction():
                    _, item = self._one(admitted['id'],now)
                    return bool(item and hmac.compare_digest(item['receipt'],admitted['receipt']) and self._model_item(item,cid))
        except Failure:
            # Withhold the optional aside without discarding the ordinary answer.
            return False

    def finish(self, tid, status):
        # Existing status names are retained for archive compatibility. "unused"
        # now means a completed context opportunity, not an interpretation of the
        # answer. The existing bounded retry cadence remains independent of prose.
        self.db.execute('UPDATE reminder_offers SET status=? WHERE turn_id=?',
            ('unused' if status=='completed' else 'uncertain',tid))

    def recover(self):
        self.db.execute("UPDATE reminder_offers SET status='uncertain' WHERE status='offered'")
