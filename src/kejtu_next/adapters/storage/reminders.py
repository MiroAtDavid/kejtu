"""One explicit reminder owner: definitions, pending occurrences and exact receipts.

No model authority, background loop, note/task mutation or implicit acknowledgement.
Delivery code must pass the exact still-current occurrence token atomically.
"""
from contextlib import contextmanager
import hashlib
import hmac
import json
import re
import sqlite3
import time
import uuid

from ...core.records import Failure
from ...core.reminders import ReminderDefinition, encoded, identity
from ...core.schedules import instant, utc

REMINDER_TABLES = ('reminder_operations', 'reminder_occurrence_versions',
                   'reminder_occurrences', 'reminder_versions', 'reminders')
REMINDER_SCHEMA = '''
CREATE TABLE reminders(id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
 title TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
 cancelled INTEGER NOT NULL DEFAULT 0 CHECK(cancelled IN (0,1)),
 next_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX reminders_due ON reminders(next_at,id) WHERE enabled=1 AND cancelled=0;
CREATE TABLE reminder_versions(reminder_id TEXT NOT NULL REFERENCES reminders(id),
 revision INTEGER NOT NULL, definition TEXT NOT NULL, sha256 TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(reminder_id,revision));
CREATE TRIGGER reminder_definition_immutable BEFORE UPDATE ON reminder_versions
 BEGIN SELECT RAISE(ABORT,'Reminder definitions are immutable revisions.'); END;
CREATE TABLE reminder_occurrences(sequence INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,
 reminder_id TEXT NOT NULL REFERENCES reminders(id), version INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','acknowledged','cancelled','superseded')),
 created_at TEXT NOT NULL, finished_at TEXT, channel TEXT);
CREATE UNIQUE INDEX reminder_one_pending ON reminder_occurrences(reminder_id) WHERE status='pending';
CREATE INDEX reminder_occurrence_history ON reminder_occurrences(reminder_id,sequence);
CREATE TRIGGER reminder_occurrence_identity_immutable BEFORE UPDATE ON reminder_occurrences
 WHEN NEW.id!=OLD.id OR NEW.reminder_id!=OLD.reminder_id OR NEW.created_at!=OLD.created_at
 BEGIN SELECT RAISE(ABORT,'Occurrence identity is immutable.'); END;
CREATE TABLE reminder_occurrence_versions(occurrence_id TEXT NOT NULL REFERENCES reminder_occurrences(id),
 version INTEGER NOT NULL, reminder_id TEXT NOT NULL, reminder_revision INTEGER NOT NULL,
 first_due TEXT NOT NULL, covered_through TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(occurrence_id,version),
 FOREIGN KEY(reminder_id,reminder_revision) REFERENCES reminder_versions(reminder_id,revision));
CREATE TRIGGER reminder_occurrence_version_immutable BEFORE UPDATE ON reminder_occurrence_versions
 BEGIN SELECT RAISE(ABORT,'Occurrence snapshots are immutable versions.'); END;
CREATE TRIGGER reminder_occurrence_terminal_immutable BEFORE UPDATE ON reminder_occurrences
 WHEN OLD.status IN ('acknowledged','cancelled','superseded')
 BEGIN SELECT RAISE(ABORT,'Finished occurrence receipts are immutable.'); END;
CREATE TABLE reminder_operations(mutation_key TEXT PRIMARY KEY, intent_sha256 TEXT NOT NULL,
 receipt TEXT NOT NULL, created_at TEXT NOT NULL);
'''


class ReminderStore:
    def __init__(self, store):
        self.store, self.db = store, store.db
        from .reminder_access import ReminderAccess
        self.model = ReminderAccess(self)
        self.actor = None

    @contextmanager
    def _transaction(self, *, write=False):
        if self.db.in_transaction:
            raise Failure('reminder_busy', 'Reminder work cannot commit another owner transaction.')
        if not self.store.path.is_file():
            raise Failure('reminder_unavailable', 'The selected reminder store is unavailable.')
        deadline, steps, stopped, ledger_failure = time.monotonic() + 5, 0, False, None
        def progress():
            nonlocal steps, stopped, ledger_failure
            steps += 100
            if self.actor is not None:
                try:
                    self.actor[2].spend_sql(100)
                    if time.monotonic() >= self.actor[2].deadline:
                        raise Failure('timeout', 'Reminder work reached its shared turn deadline.')
                except Failure as error:
                    ledger_failure = error
                    return 1
            stopped = steps > 200000 or time.monotonic() >= deadline
            return int(stopped)
        self.db.set_progress_handler(progress, 100)
        committing = False
        try:
            self.db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            if self.actor is not None:
                self.model.guard(*self.actor)
            yield
            if progress():
                raise Failure('reminder_limit', 'Reminder maintenance reached its bounded allowance.')
            committing = True
            self.db.commit()
        except BaseException as error:
            uncertain = write and committing and not self.db.in_transaction
            self.db.set_progress_handler(None, 0)
            self.db.rollback()
            if ledger_failure is not None:
                raise ledger_failure
            if uncertain:
                raise Failure('reminder_outcome_unknown', 'Look up the exact reminder mutation before another intent.') from error
            if stopped:
                raise Failure('reminder_limit', 'Reminder maintenance reached its bounded allowance.') from None
            if isinstance(error, sqlite3.Error):
                raise Failure('reminder_store_unavailable', 'Reminder storage failed; inspect the exact receipt before retrying.') from error
            raise
        finally:
            self.db.set_progress_handler(None, 0)

    def _id(self, value):
        if not identity(value):
            raise Failure('reminder_identity', 'Use an exact reminder, occurrence or mutation identity.')
        return value

    def _reminder(self, ident):
        row = self.db.execute('SELECT * FROM reminders WHERE id=?', (self._id(ident),)).fetchone()
        if row is None:
            raise Failure('reminder_missing', 'The selected reminder does not exist.')
        return row

    def _definition(self, ident, revision=None):
        current = self._reminder(ident)
        revision = current['revision'] if revision is None else revision
        if type(revision) is not int or not 1 <= revision < 2**63:
            raise Failure('reminder_revision', 'Use an exact positive definition revision.')
        row = self.db.execute('''SELECT CASE WHEN length(CAST(definition AS BLOB))<=12000 THEN definition END AS definition,
            sha256 FROM reminder_versions WHERE reminder_id=? AND revision=?''', (ident, revision)).fetchone()
        try:
            if row is None or row['definition'] is None:
                raise ValueError
            value = ReminderDefinition.from_record(json.loads(row['definition']))
            if value.digest != row['sha256']:
                raise ValueError
            return value
        except (ValueError, TypeError, RecursionError):
            raise Failure('reminder_integrity', 'Reminder definition failed exact validation.') from None

    def _support(self, source):
        # Bound the payload read before decoding it. Only a real original user
        # artifact with its current read grant can substantiate a model request.
        row = self.db.execute('''SELECT a.kind,a.sha256,a.bytes,g.epoch,
            CASE WHEN length(CAST(p.body AS BLOB))<=262144 THEN p.body END AS body
            FROM recall_turns t JOIN artifacts a ON a.id=t.user_id
            JOIN artifact_payloads p ON p.artifact_id=a.id
            JOIN memory_grants g ON g.conversation_id=t.conversation_id
            WHERE t.user_id=? AND t.conversation_id=? AND g.enabled=1''',
            (source.user_id, source.conversation_id)).fetchone()
        if row is None or row['kind'] != 'user_message' or row['body'] is None:
            raise Failure('reminder_source_unavailable', 'Supporting original user text is unavailable or exceeds its validation bound.')
        raw = row['body'].encode()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != source.sha256 or digest != row['sha256'] or len(raw) != row['bytes'] or source.quote not in row['body']:
            raise Failure('reminder_source_changed', 'Supporting quotation or exact original version changed.')
        return dict(conversation_id=source.conversation_id, user_id=source.user_id,
                    sha256=digest, grant_epoch=row['epoch'])

    def _state(self, definition):
        sources = [self._support(source) for source in definition.support]
        note = self.db.execute('SELECT head_revision,readable,validity FROM memory_records WHERE id=?', (definition.note_id,)).fetchone() if definition.note_id else None
        task = self.db.execute('SELECT revision,outcome,attention FROM personal_tasks WHERE id=?', (definition.task_id,)).fetchone() if definition.task_id else None
        return dict(sources=sources, note=dict(note) if note else None, task=dict(task) if task else None)

    def _links(self, definition, original=None):
        for field, table in (('note_id', 'note_records'), ('task_id', 'personal_tasks')):
            value = getattr(definition, field)
            if value is not None and (original is None or value != getattr(original, field)):
                column = 'record_id' if field == 'note_id' else 'id'
                if self.db.execute(f'SELECT 1 FROM {table} WHERE {column}=?', (value,)).fetchone() is None:
                    raise Failure('reminder_link_unavailable', 'Choose an existing linked record or explicitly clear the optional link.')

    def _receipt(self, key, intent):
        key = self._id(key)
        digest = hashlib.sha256(encoded(intent).encode()).hexdigest()
        row = self.db.execute('SELECT * FROM reminder_operations WHERE mutation_key=?', (key,)).fetchone()
        if row is None:
            return digest, None
        if row['intent_sha256'] != digest:
            raise Failure('reminder_conflict', 'The mutation key belongs to a different reminder intent.')
        return digest, dict(json.loads(row['receipt']), replayed=True)

    def _record(self, key, digest, receipt, now):
        self.db.execute('INSERT INTO reminder_operations VALUES(?,?,?,?)', (key, digest, encoded(receipt), now))
        return receipt

    def lookup(self, key):
        with self._transaction():
            row = self.db.execute('SELECT receipt FROM reminder_operations WHERE mutation_key=?', (self._id(key),)).fetchone()
            return dict(json.loads(row[0]), replayed=True) if row else dict(status='absent')

    def control_receipt(self, key, *, expected_intent=None):
        """Resolve a lost local reply only for its exact frozen mutation intent."""
        with self._transaction():
            row = self.db.execute('SELECT * FROM reminder_operations WHERE mutation_key=?', (self._id(key),)).fetchone()
            if row is None:
                return dict(status='absent')
            if expected_intent is not None and row['intent_sha256'] != expected_intent:
                raise Failure('reminder_conflict', 'Reminder receipt belongs to another exact intent.')
            return dict(json.loads(row['receipt']), mutation_key=key,
                        intent_sha256=row['intent_sha256'], replayed=True)

    def _pending(self, ident):
        return self.db.execute('''SELECT o.*,v.reminder_revision,v.first_due,v.covered_through
            FROM reminder_occurrences o JOIN reminder_occurrence_versions v
            ON v.occurrence_id=o.id AND v.version=o.version WHERE o.reminder_id=? AND o.status='pending' ''', (ident,)).fetchone()

    def _occurrence(self, ident, revision, first, through, now):
        oid = uuid.uuid4().hex
        self.db.execute("INSERT INTO reminder_occurrences(id,reminder_id,version,status,created_at) VALUES(?,?,1,'pending',?)", (oid, ident, now))
        self.db.execute('INSERT INTO reminder_occurrence_versions VALUES(?,1,?,?,?,?,?)', (oid, ident, revision, first, through, now))
        return oid

    def save(self, definition, *, mutation_key, now, reminder_id=None, expected_revision=None, reactivate=False):
        """Explicit local user operation; this does not grant any model tool.

        A valid source quote proves its origin, not that it requested a reminder.
        A future model-facing path must enforce its own explicit request/capability
        and shared-ledger contract before invoking this owner.
        """
        if type(definition) is not ReminderDefinition or type(reactivate) is not bool:
            raise Failure('reminder_definition', 'Use a validated complete definition and explicit reactivation choice.')
        now = utc(instant(now))
        intent = dict(action='save', reminder_id=reminder_id, expected_revision=expected_revision,
                      reactivate=reactivate, definition=definition.record())
        with self._transaction(write=True):
            digest, prior = self._receipt(mutation_key, intent)
            if prior is not None:
                return prior
            old = self._reminder(reminder_id) if reminder_id is not None else None
            original = self._definition(reminder_id) if old is not None else None
            if old is None and (expected_revision is not None or reactivate):
                raise Failure('reminder_revision', 'New reminders have no existing revision to reactivate.')
            if old is not None and (type(expected_revision) is not int or expected_revision != old['revision']):
                raise Failure('reminder_conflict', 'The reminder changed; inspect its current revision.')
            if reactivate and (not old['cancelled'] or not definition.enabled):
                raise Failure('reminder_definition', 'Reactivation requires a cancelled reminder and explicit enabled schedule.')
            # Support is required when first claimed or when newly enabled, not
            # to retain/cancel/disable an old record after source loss.
            if original is None or definition.support != original.support or reactivate or definition.enabled and not old['cancelled']:
                self._state(definition)
            self._links(definition, original)
            if old is not None and original == definition and not reactivate:
                return self._record(mutation_key, digest,
                    dict(status='unchanged', reminder_id=reminder_id, revision=old['revision'], replayed=False), now)
            if old is None:
                if self.db.execute('SELECT count(*) FROM reminders').fetchone()[0] >= 10000:
                    raise Failure('reminder_limit', 'Reminder record bound reached; existing controls remain available.')
                ident, revision, cancelled, next_at = uuid.uuid4().hex, 1, False, utc(definition.schedule.start)
                self.db.execute('INSERT INTO reminders VALUES(?,?,?,?,0,?,?,?)',
                    (ident, revision, definition.title, int(definition.enabled), next_at, now, now))
            else:
                ident, revision = reminder_id, old['revision'] + 1
                cancelled = bool(old['cancelled']) and not reactivate
                same_schedule = original.schedule.timing_key == definition.schedule.timing_key and not reactivate
                next_at = old['next_at'] if same_schedule else utc(definition.schedule.start)
                if cancelled:
                    next_at = None
                pending = self._pending(ident)
                if pending is not None:
                    self.db.execute("UPDATE reminder_occurrences SET status='superseded',finished_at=? WHERE id=?", (now, pending['id']))
                self.db.execute('UPDATE reminders SET revision=?,title=?,enabled=?,cancelled=?,next_at=?,updated_at=? WHERE id=?',
                    (revision, definition.title, int(definition.enabled and not cancelled), int(cancelled), next_at, now, ident))
            self.db.execute('INSERT INTO reminder_versions VALUES(?,?,?,?,?)',
                (ident, revision, encoded(definition.record()), definition.digest, now))
            if old is not None and pending is not None and same_schedule and not cancelled:
                # Edits/pauses replace the notice snapshot, not its due trigger;
                # they neither acknowledge it nor silently reset its recurrence.
                self._occurrence(ident, revision, pending['first_due'], pending['covered_through'], now)
            return self._record(mutation_key, digest,
                dict(status='committed', reminder_id=ident, revision=revision, replayed=False), now)

    def cancel(self, ident, *, expected_revision, mutation_key, now):
        now = utc(instant(now))
        with self._transaction(write=True):
            digest, prior = self._receipt(mutation_key, dict(action='cancel', reminder_id=ident, expected_revision=expected_revision))
            if prior is not None:
                return prior
            row = self._reminder(ident)
            if type(expected_revision) is not int or expected_revision != row['revision']:
                raise Failure('reminder_conflict', 'The reminder changed before cancellation.')
            self.db.execute('UPDATE reminders SET cancelled=1,enabled=0,next_at=NULL,updated_at=? WHERE id=?', (now, ident))
            self.db.execute("UPDATE reminder_occurrences SET status='cancelled',finished_at=? WHERE reminder_id=? AND status='pending'", (now, ident))
            return self._record(mutation_key, digest,
                dict(status='cancelled', reminder_id=ident, revision=row['revision'], replayed=False), now)

    def poll(self, now, *, limit=20):
        now = utc(instant(now))
        self._page_limit(limit)
        with self._transaction(write=True):
            rows = self.db.execute('SELECT * FROM reminders WHERE enabled=1 AND cancelled=0 AND next_at<=? ORDER BY next_at,id LIMIT ?', (now, limit)).fetchall()
            made = []
            for row in rows:
                definition = self._definition(row['id'])
                due = definition.schedule.advance(row['next_at'], now)
                if due is None:
                    continue
                pending = self._pending(row['id'])
                if pending is None:
                    oid = self._occurrence(row['id'], row['revision'], due.first_due, due.covered_through, now)
                else:
                    oid, version = pending['id'], pending['version'] + 1
                    self.db.execute('INSERT INTO reminder_occurrence_versions VALUES(?,?,?,?,?,?,?)',
                        (oid, version, row['id'], row['revision'], pending['first_due'], due.covered_through, now))
                    self.db.execute('UPDATE reminder_occurrences SET version=? WHERE id=?', (version, oid))
                self.db.execute('UPDATE reminders SET next_at=? WHERE id=?', (due.next_at, row['id']))
                made.append(oid)
            return made

    def _token(self, reminder, occurrence, definition):
        state = self._state(definition)
        raw = encoded(dict(state_id=self.store.state_id, reminder_id=reminder['id'],
            revision=reminder['revision'], definition=definition.digest,
            occurrence_id=occurrence['id'], occurrence_version=occurrence['version'],
            first_due=occurrence['first_due'], covered_through=occurrence['covered_through'], sources=state)).encode()
        key = self.db.execute('SELECT handle_key FROM memory_banks WHERE slot=1').fetchone()[0]
        return hmac.digest(key, raw, 'sha256').hex(), state

    def _notice(self, reminder, occurrence):
        if (occurrence is None or occurrence['reminder_id'] != reminder['id']
                or occurrence['reminder_revision'] != reminder['revision']):
            raise Failure('reminder_integrity', 'Pending occurrence does not match its exact definition.')
        definition = self._definition(reminder['id'])
        try:
            receipt, state = self._token(reminder, occurrence, definition)
            availability = 'available' if definition.support else 'manual'
        except Failure as error:
            if error.code not in ('reminder_source_unavailable', 'reminder_source_changed'):
                raise
            receipt, state, availability = None, None, 'unavailable'
        active = bool(reminder['enabled'] and not reminder['cancelled'] and occurrence['status'] == 'pending')
        return dict(reminder_id=reminder['id'], reminder_revision=reminder['revision'],
            occurrence_id=occurrence['id'], occurrence_version=occurrence['version'],
            title=definition.title, text=definition.text, first_due=occurrence['first_due'],
            covered_through=occurrence['covered_through'], timezone=definition.schedule.timezone,
            repeat=definition.schedule.repeat, interval=definition.schedule.interval,
            source_status=availability, links=state and dict(note=state['note'], task=state['task']),
            receipt=receipt if active else None, deliverable=bool(active and receipt is not None))

    def _page_limit(self, limit):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise Failure('reminder_page', 'Use a page size of 1–50.')

    def pending(self, *, after=0, limit=20, include_withheld=False):
        self._page_limit(limit)
        if type(after) is not int or not 0 <= after < 2**63 or type(include_withheld) is not bool:
            raise Failure('reminder_page', 'Use an exact occurrence sequence and explicit inspection mode.')
        with self._transaction():
            rows = self.db.execute('''SELECT o.*,v.reminder_revision,v.first_due,v.covered_through FROM reminder_occurrences o
                JOIN reminder_occurrence_versions v ON v.occurrence_id=o.id AND v.version=o.version
                WHERE o.status='pending' AND o.sequence>? ORDER BY o.sequence LIMIT ?''', (after, limit + 1)).fetchall()
            values = []
            for row in rows[:limit]:
                notice = self._notice(self._reminder(row['reminder_id']), row)
                if include_withheld or notice['deliverable']:
                    values.append(notice)
            # Cursor follows examined rows, even when source loss hides a body.
            return dict(items=values, next_cursor=rows[limit - 1]['sequence'] if len(rows) > limit else None)

    def _validated_notice(self, ident, receipt):
        if not isinstance(receipt, str) or re.fullmatch('[0-9a-f]{64}', receipt) is None:
            raise Failure('reminder_receipt', 'Use the exact available occurrence receipt.')
        row = self.db.execute('SELECT * FROM reminder_occurrences WHERE id=?', (self._id(ident),)).fetchone()
        if row is None or row['status'] != 'pending':
            raise Failure('reminder_stale', 'That occurrence is no longer pending; nothing was acknowledged.')
        current = self._pending(row['reminder_id'])
        notice = self._notice(self._reminder(row['reminder_id']), current)
        if not notice['deliverable'] or not hmac.compare_digest(receipt, notice['receipt']):
            raise Failure('reminder_stale', 'Reminder, coalesced occurrence or source version changed; refresh before displaying or acknowledging.')
        return notice

    def validate_notice(self, ident, receipt):
        """Recheck immediately before rendering; never consumes a pending notice."""
        with self._transaction():
            return self._validated_notice(ident, receipt)

    def acknowledge(self, ident, receipt, *, mutation_key, channel, now):
        if channel not in ('dismissed', 'presented', 'explicit') or not isinstance(receipt, str) or re.fullmatch('[0-9a-f]{64}', receipt) is None:
            raise Failure('reminder_receipt', 'Use the exact displayed occurrence receipt and explicit delivery channel.')
        now = utc(instant(now))
        with self._transaction(write=True):
            return self._acknowledge(ident, receipt, mutation_key=mutation_key, channel=channel, now=now)

    def _acknowledge(self, ident, receipt, *, mutation_key, channel, now):
        """Shared owner inside its own transaction or the delivery owner's commit."""
        assert self.db.in_transaction
        digest, prior = self._receipt(mutation_key, dict(action='acknowledge', occurrence_id=ident, receipt=receipt, channel=channel))
        if prior is not None:
            return prior
        notice = self._validated_notice(ident, receipt)
        self.db.execute("UPDATE reminder_occurrences SET status='acknowledged',finished_at=?,channel=? WHERE id=?", (now, channel, ident))
        return self._record(mutation_key, digest, dict(status='acknowledged', reminder_id=notice['reminder_id'],
            occurrence_id=ident, occurrence_version=notice['occurrence_version'], channel=channel, replayed=False), now)

    def read(self, ident, *, revision=None, before=None, limit=20):
        self._page_limit(limit)
        if before is not None and (type(before) is not int or not 1 <= before < 2**63):
            raise Failure('reminder_page', 'Use a positive history revision cursor.')
        with self._transaction():
            reminder = self._reminder(ident)
            definition = self._definition(ident, revision)
            # Inspection is manual and remains available after source loss.
            rows = self.db.execute('''SELECT revision,sha256,created_at FROM reminder_versions
                WHERE reminder_id=? AND (? IS NULL OR revision<?) ORDER BY revision DESC LIMIT ?''', (ident, before, before, limit + 1)).fetchall()
            return dict(reminder=dict(reminder), definition=definition.record(),
                        definition_revision=reminder['revision'] if revision is None else revision,
                        history=[dict(row) for row in rows[:limit]],
                        next_cursor=rows[limit - 1]['revision'] if len(rows) > limit else None)

    def page(self, *, after='', limit=20):
        self._page_limit(limit)
        if after != '' or not isinstance(after, str):
            self._id(after)
        with self._transaction():
            rows = [dict(row) for row in self.db.execute('SELECT * FROM reminders WHERE id>? ORDER BY id LIMIT ?', (after, limit + 1))]
            return dict(items=rows[:limit], next_cursor=rows[limit - 1]['id'] if len(rows) > limit else None)

    def occurrences(self, ident, *, after=0, limit=20):
        """Body-free exact occurrence history; inspection never consumes notices."""
        self._page_limit(limit)
        if type(after) is not int or not 0 <= after < 2**63:
            raise Failure('reminder_page', 'Use an exact occurrence sequence cursor.')
        with self._transaction():
            self._reminder(ident)
            rows = [dict(row) for row in self.db.execute('''SELECT o.*,v.reminder_revision,v.first_due,v.covered_through
                FROM reminder_occurrences o JOIN reminder_occurrence_versions v
                ON v.occurrence_id=o.id AND v.version=o.version
                WHERE o.reminder_id=? AND o.sequence>? ORDER BY o.sequence LIMIT ?''', (ident, after, limit + 1))]
            return dict(items=rows[:limit], next_cursor=rows[limit - 1]['sequence'] if len(rows) > limit else None)
