"""Durable, source-bound chat presentation of explicit reminders.

Scheduling and occurrences stay with ReminderStore. Reading a message never
claims it was displayed; only an exact renderer receipt records presentation.
"""
import hashlib
import hmac
from datetime import timedelta

from ...core.records import Failure
from ...core.reminders import encoded
from ...core.schedules import instant, utc

REMINDER_CHAT_SCHEMA = '''
CREATE TABLE reminder_chat_messages(sequence INTEGER PRIMARY KEY,
 occurrence_id TEXT NOT NULL UNIQUE REFERENCES reminder_occurrences(id),
 occurrence_version INTEGER NOT NULL, reminder_id TEXT NOT NULL,
 reminder_revision INTEGER NOT NULL, conversation_id TEXT NOT NULL REFERENCES conversations(id),
 revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','presented','snoozed','dismissed')),
 due_at TEXT NOT NULL, created_at TEXT NOT NULL, presented_at TEXT, after_sequence INTEGER,
 FOREIGN KEY(reminder_id,reminder_revision) REFERENCES reminder_versions(reminder_id,revision));
CREATE INDEX reminder_chat_inbox ON reminder_chat_messages(status,due_at,sequence);
CREATE INDEX reminder_chat_history ON reminder_chat_messages(conversation_id,after_sequence,sequence);
'''


class ReminderChat:
    def __init__(self, store):
        self.store, self.db, self.owner = store, store.db, store.reminders

    def prepare(self, now):
        # Standalone local reminders have no invented chat destination. Explicit
        # chat requests already carry an exact originating conversation.
        with self.owner._transaction(write=True):
            rows = self.db.execute('''SELECT o.*,v.reminder_revision,v.first_due FROM reminder_occurrences o
                JOIN reminder_occurrence_versions v ON v.occurrence_id=o.id AND v.version=o.version
                JOIN reminder_versions dv ON dv.reminder_id=o.reminder_id AND dv.revision=v.reminder_revision
                LEFT JOIN reminder_chat_messages m ON m.occurrence_id=o.id
                WHERE o.status='pending' AND json_array_length(dv.definition,'$.support')>0
                AND coalesce(json_extract(dv.definition,'$.origin'),'requested')='requested'
                AND (m.occurrence_id IS NULL OR m.occurrence_version!=o.version)
                ORDER BY o.sequence LIMIT 50''').fetchall()
            for row in rows:
                value = self.owner._definition(row['reminder_id'], row['reminder_revision'])
                if not value.support:
                    continue
                cid = value.support[-1].conversation_id
                self.db.execute('''INSERT INTO reminder_chat_messages(occurrence_id,occurrence_version,reminder_id,
                    reminder_revision,conversation_id,due_at,created_at) VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(occurrence_id) DO UPDATE SET occurrence_version=excluded.occurrence_version,
                    reminder_revision=excluded.reminder_revision,conversation_id=excluded.conversation_id,
                    revision=reminder_chat_messages.revision+1
                    WHERE reminder_chat_messages.status='pending' ''',
                    (row['id'],row['version'],row['reminder_id'],row['reminder_revision'],cid,row['first_due'],now))

    def _row(self, ident):
        row = self.db.execute('SELECT * FROM reminder_chat_messages WHERE occurrence_id=?', (self.owner._id(ident),)).fetchone()
        if row is None:
            raise Failure('reminder_message_missing', 'This reminder message is unavailable.')
        return row

    def _available(self, row):
        value = self.owner._definition(row['reminder_id'], row['reminder_revision'])
        current = self.owner._reminder(row['reminder_id'])
        if current['revision'] != row['reminder_revision'] or current['cancelled'] or not current['enabled']:
            raise Failure('reminder_stale', 'The reminder was changed, paused or cancelled.')
        state = self.owner._state(value)
        if row['status'] == 'pending':
            occurrence = self.owner._pending(row['reminder_id'])
            if occurrence is None or occurrence['id'] != row['occurrence_id'] or occurrence['version'] != row['occurrence_version']:
                raise Failure('reminder_stale', 'This occurrence has changed or was already delivered.')
        return value, state

    def _message(self, row, now):
        value = self.owner._definition(row['reminder_id'], row['reminder_revision'])
        available, state = True, None
        try:
            _, state = self._available(row)
        except Failure as error:
            if error.code not in ('reminder_stale','reminder_source_changed','reminder_source_unavailable'):
                raise
            available = False
        due = instant(row['due_at']) <= instant(now)
        if row['status'] in ('pending','snoozed') and (not available or not due):
            return None
        head = self.db.execute('SELECT max(sequence) FROM turns WHERE conversation_id=?', (row['conversation_id'],)).fetchone()[0]
        anchor = head if row['status'] in ('pending','snoozed') else row['after_sequence']
        if anchor is None:
            return None
        key = self.db.execute('SELECT handle_key FROM memory_banks WHERE slot=1').fetchone()[0]
        payload = encoded(dict(state_id=self.store.state_id, id=row['occurrence_id'], revision=row['revision'],
            status=row['status'], due_at=row['due_at'], definition=value.digest, state=state))
        receipt = hmac.digest(key, payload.encode(), 'sha256').hex() if available else None
        return dict(id=row['occurrence_id'], sequence=str(row['sequence']), conversation_id=row['conversation_id'],
            reminder_id=row['reminder_id'], reminder_revision=row['reminder_revision'], revision=row['revision'],
            status=row['status'], due_at=row['due_at'], created_at=row['created_at'], presented_at=row['presented_at'],
            after_sequence=str(anchor), title=value.title, text=value.text, timezone=value.schedule.timezone,
            receipt=receipt, available=available)

    def page(self, cid, *, now, before=None, limit=20):
        self.owner._id(cid)
        self.owner._page_limit(limit)
        if before is not None and (type(before) is not int or before < 1):
            raise Failure('reminder_page', 'Use an exact message cursor.')
        with self.owner._transaction():
            rows = self.db.execute('''SELECT * FROM reminder_chat_messages WHERE conversation_id=?
                AND (? IS NULL OR sequence<?) ORDER BY sequence DESC LIMIT ?''', (cid,before,before,limit+1)).fetchall()
            messages = [self._message(row, now) for row in rows[:limit]]
            return dict(items=[m for m in messages if m is not None],
                next_cursor=rows[limit-1]['sequence'] if len(rows)>limit else None)

    def inbox(self, *, now, after=0, limit=50):
        self.owner._page_limit(limit)
        with self.owner._transaction():
            rows = self.db.execute('''SELECT * FROM reminder_chat_messages
                WHERE status IN ('pending','snoozed') AND due_at<=? AND sequence>?
                ORDER BY sequence LIMIT ?''', (utc(instant(now)),after,limit+1)).fetchall()
            return dict(items=[dict(id=m['id'],conversation_id=m['conversation_id']) for row in rows[:limit]
                               if (m := self._message(row, now)) is not None],
                        next_cursor=rows[limit-1]['sequence'] if len(rows)>limit else None)

    def validate(self, ident, receipt, now):
        with self.owner._transaction():
            return self._validated(ident, receipt, now)

    def _validated(self, ident, receipt, now):
        message = self._message(self._row(ident), now)
        if message is None or message['receipt'] is None or not isinstance(receipt,str) or not hmac.compare_digest(message['receipt'],receipt):
            raise Failure('reminder_stale', 'This reminder changed; refresh the message before acting.')
        return message

    def change(self, args, *, now):
        action = args['action']
        if action not in ('present','later','dismiss'):
            raise Failure('reminder_message_action', 'Choose an explicit reminder action.')
        now = utc(instant(now))
        intent = {k:v for k,v in args.items() if k!='mutation_key'}
        with self.owner._transaction(write=True):
            digest, prior = self.owner._receipt(args['mutation_key'], intent)
            if prior is not None:
                return dict(prior, mutation_key=args['mutation_key'], intent_sha256=digest)
            row = self._row(args['id'])
            if row['revision'] != args['expected_revision']:
                raise Failure('reminder_stale', 'This reminder message changed; refresh before acting.')
            message = self._validated(args['id'],args['receipt'],now)
            if action == 'present':
                if self.store.active_turn_id() is not None:
                    raise Failure('reminder_foreground', 'Finish the current answer before presenting a reminder.')
                if row['status'] not in ('pending','snoozed'):
                    raise Failure('reminder_stale', 'This reminder has already been presented.')
                if row['status']=='pending':
                    notice = self.owner._notice(self.owner._reminder(row['reminder_id']), self.owner._pending(row['reminder_id']))
                    key = hashlib.sha256((args['mutation_key']+':occurrence').encode()).hexdigest()[:32]
                    self.owner._acknowledge(row['occurrence_id'], notice['receipt'],mutation_key=key,channel='presented',now=now)
                status, due = 'presented', row['due_at']
                self.db.execute('UPDATE reminder_chat_messages SET presented_at=?,after_sequence=? WHERE occurrence_id=?',
                    (now,int(message['after_sequence']),row['occurrence_id']))
            elif action == 'later':
                try:
                    due = utc(instant(args['until']))
                    if not instant(now)<instant(due)<=instant(now)+timedelta(days=366): raise ValueError
                except (ValueError,TypeError,KeyError):
                    raise Failure('reminder_time', 'Choose a future reminder time within the next year.') from None
                if row['status'] not in ('pending','presented'):
                    raise Failure('reminder_stale', 'Refresh the reminder before postponing it.')
                if row['status']=='pending':
                    notice=self.owner._notice(self.owner._reminder(row['reminder_id']),self.owner._pending(row['reminder_id']))
                    key=hashlib.sha256((args['mutation_key']+':occurrence').encode()).hexdigest()[:32]
                    self.owner._acknowledge(row['occurrence_id'],notice['receipt'],mutation_key=key,channel='explicit',now=now)
                status='snoozed'
            else:
                if row['status']=='pending':
                    notice=self.owner._notice(self.owner._reminder(row['reminder_id']),self.owner._pending(row['reminder_id']))
                    key=hashlib.sha256((args['mutation_key']+':occurrence').encode()).hexdigest()[:32]
                    self.owner._acknowledge(row['occurrence_id'],notice['receipt'],mutation_key=key,channel='dismissed',now=now)
                status,due='dismissed',row['due_at']
            if action == 'dismiss' and row['after_sequence'] is None:
                self.db.execute('UPDATE reminder_chat_messages SET after_sequence=? WHERE occurrence_id=?',
                    (int(message['after_sequence']),row['occurrence_id']))
            self.db.execute('UPDATE reminder_chat_messages SET status=?,due_at=?,revision=revision+1 WHERE occurrence_id=?',
                            (status,due,row['occurrence_id']))
            result=dict(status=status,id=row['occurrence_id'],reminder_id=row['reminder_id'],revision=row['revision']+1,
                due_at=due,mutation_key=args['mutation_key'],intent_sha256=digest,replayed=False)
            return self.owner._record(args['mutation_key'],digest,result,now)

    def history_records(self, cid, after_sequence, ledger):
        # Identical immutable revisions share one exact text, with presentation
        # count/times retained. A bounded tail of distinct revisions cannot lock
        # the next reply out of its conversation.
        rows=self.db.execute('''SELECT m.*,g.presentations,g.first_presented FROM (
            SELECT max(sequence) AS latest,count(*) AS presentations,min(presented_at) AS first_presented
            FROM reminder_chat_messages WHERE conversation_id=? AND after_sequence=? AND presented_at IS NOT NULL
            GROUP BY reminder_id,reminder_revision ORDER BY latest DESC LIMIT 16) g
            JOIN reminder_chat_messages m ON m.sequence=g.latest ORDER BY m.sequence''',(cid,after_sequence)).fetchall()
        total=self.db.execute('''SELECT count(*) FROM reminder_chat_messages
            WHERE conversation_id=? AND after_sequence=? AND presented_at IS NOT NULL''',(cid,after_sequence)).fetchone()[0]
        result=[]
        for row in rows:
            try:
                value=self.owner._definition(row['reminder_id'],row['reminder_revision'])
                self.owner._state(value)
            except Failure as error:
                if error.code in ('reminder_stale','reminder_source_changed','reminder_source_unavailable'): continue
                raise
            record=dict(reminder_message_id=row['occurrence_id'],reminder_id=row['reminder_id'],
                revision=row['reminder_revision'],source_dependencies=[s.user_id for s in value.support],
                presented_at=row['presented_at'],first_presented_at=row['first_presented'],
                presentation_count=row['presentations'],text=value.text)
            ledger.spend_payload(len(encoded(record).encode()))
            result.append(record)
        omitted=total-sum(row['presentations'] for row in rows)
        if omitted:
            result.append(dict(omitted_presentations=omitted,source_dependencies=[]))
        return result
