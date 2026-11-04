"""Source-bound outbox; preparation, presentation and private results are distinct.

Only an explicit renderer receipt changes presentation state. This owner uses
the same SQLite transaction as run completion and reminder acknowledgement.
It never fabricates a user turn or starts inference.
"""
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import hmac
import json
import re
import sqlite3
import time
import uuid

from ...core.jobs import REVIEW_KINDS
from ...core.records import Failure, Message
from ...core.schedules import instant, utc
from .jobs import encoded, identity

DELIVERY_SCHEMA = '''
CREATE TABLE background_deliveries(sequence INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,
 run_id TEXT NOT NULL UNIQUE REFERENCES background_runs(id), conversation_id TEXT NOT NULL,
 kind TEXT NOT NULL, channel TEXT NOT NULL, source_key TEXT NOT NULL, content_sha256 TEXT NOT NULL,
 binding TEXT NOT NULL, result_id TEXT NOT NULL REFERENCES artifacts(id),
 after_sequence INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','presented','dismissed','cancelled')),
 created_at TEXT NOT NULL, finished_at TEXT, reason TEXT);
CREATE INDEX delivery_pending ON background_deliveries(sequence) WHERE status='pending';
CREATE INDEX delivery_sources ON background_deliveries(conversation_id,kind,after_sequence,status);
CREATE INDEX delivery_content ON background_deliveries(conversation_id,content_sha256,status);
CREATE INDEX delivery_history ON background_deliveries(conversation_id,after_sequence,sequence) WHERE status='presented';
CREATE INDEX delivery_presented_epoch ON background_deliveries(sequence) WHERE status='presented';
CREATE INDEX delivery_budget ON background_deliveries(created_at);
CREATE TRIGGER delivery_identity_immutable BEFORE UPDATE OF id,run_id,conversation_id,kind,channel,
 source_key,content_sha256,binding,result_id,after_sequence,created_at ON background_deliveries
 BEGIN SELECT RAISE(ABORT,'Delivery identity and content are immutable.'); END;
CREATE TRIGGER delivery_terminal_immutable BEFORE UPDATE ON background_deliveries
 WHEN OLD.status!='pending' BEGIN SELECT RAISE(ABORT,'Delivery receipts are terminal.'); END;
CREATE TABLE background_delivery_operations(mutation_key TEXT PRIMARY KEY, intent_sha256 TEXT NOT NULL,
 receipt TEXT NOT NULL, created_at TEXT NOT NULL);
'''


class DeliveryStore:
    def __init__(self, store):
        self.store, self.db = store, store.db

    @contextmanager
    def transaction(self):
        if self.db.in_transaction:
            raise Failure('delivery_busy', 'Delivery cannot commit another owner transaction.')
        deadline, steps, stopped = time.monotonic() + 5, 0, False
        def progress():
            nonlocal steps, stopped
            steps += 100
            stopped = steps > 200000 or time.monotonic() >= deadline
            return int(stopped)
        self.db.set_progress_handler(progress, 100)
        committing = False
        try:
            self.db.execute('BEGIN IMMEDIATE')
            yield
            if progress():
                raise Failure('delivery_limit', 'Bounded delivery work exhausted its allowance.')
            committing = True
            self.db.commit()
        except BaseException as error:
            uncertain = committing and not self.db.in_transaction
            self.db.set_progress_handler(None, 0)
            self.db.rollback()
            if uncertain:
                raise Failure('delivery_outcome_unknown', 'Resolve the exact delivery mutation before another intent.') from error
            if stopped:
                raise Failure('delivery_limit', 'Bounded delivery work exhausted its allowance.') from None
            if isinstance(error, sqlite3.Error):
                raise Failure('delivery_unavailable', 'Delivery storage failed; inspect the exact outcome before retrying.') from error
            raise
        finally:
            self.db.set_progress_handler(None, 0)

    def _row(self, ident):
        row = self.db.execute('SELECT * FROM background_deliveries WHERE id=?', (identity(ident),)).fetchone()
        if row is None:
            raise Failure('delivery_missing', 'The exact delivery does not exist.')
        return row

    @staticmethod
    def metadata(row):
        return {key: str(row[key]) if key in ('sequence', 'after_sequence') else row[key]
                for key in ('id', 'sequence', 'run_id', 'conversation_id', 'kind', 'channel',
                            'result_id', 'after_sequence', 'status', 'created_at', 'finished_at', 'reason')}

    def _binding(self, definition, snapshot, result):
        config = snapshot['configuration']
        return dict(configuration=config, grant=snapshot['grant'],
            originals=[dict(artifact_id=part['artifact_id'], sha256=part['sha256'])
                for turn in snapshot['conversation']['selected_complete_exchanges']
                for part in (turn['user'], turn['answer'], *turn.get('background_presentations', []))],
            reminders=result['occurrence_receipts'])

    def _fresh(self, row, ledger=None):
        if self.db.execute('SELECT 1 FROM excluded_background_runs WHERE run_id=?', (row['run_id'],)).fetchone():
            raise Failure('delivery_stale', 'Supporting material was excluded from recall.')
        run = self.store.jobs.run(row['run_id'])
        job = self.store.jobs._job(run['job_id'])
        definition = self.store.jobs.definition(run['job_id'], run['revision'])
        if (not job['enabled'] or job['cancelled'] or job['revision'] != run['revision']
                or not definition.permissions.deliver or definition.kind in REVIEW_KINDS):
            raise Failure('delivery_stale', 'Delivery permission or job revision changed.')
        self.store.jobs._sources(definition)
        if self.store.active_turn_id() is not None:
            raise Failure('delivery_foreground', 'Foreground work takes precedence over unseen delivery.')
        cid = row['conversation_id']
        binding = json.loads(row['binding'])
        grant = self.db.execute('SELECT bank_id,enabled,epoch FROM memory_grants WHERE conversation_id=?', (cid,)).fetchone()
        head = self.db.execute('SELECT max(sequence) FROM recall_turns WHERE conversation_id=?', (cid,)).fetchone()[0]
        if grant is None or dict(grant) != binding['grant'] or head != row['after_sequence']:
            raise Failure('delivery_stale', 'Source grants or conversation changed.')
        if self.store.configuration_id(cid) != binding['configuration']['artifact_id']:
            raise Failure('delivery_stale', 'Saved conversation instructions changed.')
        inspected = 0
        for source in [binding['configuration'], *binding['originals']]:
            original = self.db.execute('SELECT sha256,bytes FROM artifacts WHERE id=?', (source['artifact_id'],)).fetchone()
            if original is None or original[0] != source['sha256']:
                raise Failure('delivery_stale', 'An exact supporting original is no longer available.')
            inspected += original['bytes']
            if not 0 <= original['bytes'] <= 262144 or inspected > 8 * 1024 * 1024:
                raise Failure('delivery_limit', 'Exact source validation exceeds its bounded maintenance allowance.')
            if ledger is not None:
                ledger.spend_payload(original['bytes'])
            try:
                self.store._body(source['artifact_id'])
            except Failure:
                raise Failure('delivery_stale', 'An exact supporting original payload is unavailable or changed.') from None
        for notice in binding['reminders']:
            if ledger is not None:
                occurrence = self.db.execute('SELECT reminder_id FROM reminder_occurrences WHERE id=?', (notice['occurrence_id'],)).fetchone()
                if occurrence is None:
                    raise Failure('delivery_stale', 'A selected occurrence is unavailable.')
                value = self.store.reminders._definition(occurrence[0])
                ledger.spend_payload(len(encoded(value.record()).encode()))
                for source in value.support:
                    metadata = self.db.execute('SELECT bytes FROM artifacts WHERE id=?', (source.user_id,)).fetchone()
                    if metadata is not None:
                        ledger.spend_payload(metadata[0])
            self.store.reminders._validated_notice(notice['occurrence_id'], notice['receipt'])
        return definition

    def enqueue(self, run, definition, snapshot, result_id, result, now, ledger):
        """Called inside run completion: its result and availability commit together."""
        assert self.db.in_transaction
        if (definition.kind not in ('check_in', 'reminder_delivery') or not definition.permissions.deliver
                or result['decision'] != 'send'):
            raise Failure('delivery_forbidden', 'Only an authorized send decision can enter the outbox.')
        cid, head = definition.conversation_id, snapshot['latest_sequence']
        ledger.spend_sql(2000)
        ledger.spend_payload(len(encoded(result).encode()))
        if time.monotonic() >= ledger.deadline:
            raise Failure('timeout', 'Delivery preparation reached the shared job deadline.')
        content = hashlib.sha256(result['text'].encode()).hexdigest()
        # One unseen item across jobs/conversations prevents a queued burst.
        # Different job IDs or source-window sizes cannot reset repetition state.
        since = utc(instant(now) - timedelta(days=1))
        pending = self.db.execute("SELECT 1 FROM background_deliveries WHERE status='pending' LIMIT 1").fetchone()
        repeated = self.db.execute('''SELECT 1 FROM background_deliveries WHERE conversation_id=?
            AND status IN ('pending','presented','dismissed') AND kind='check_in' AND ?='check_in'
            AND (after_sequence=? OR (content_sha256=? AND created_at>?)) LIMIT 1''',
            (cid, definition.kind, head, content, since)).fetchone()
        count, last = self.db.execute('SELECT count(*),max(created_at) FROM background_deliveries WHERE created_at>?', (since,)).fetchone()
        if pending or repeated or count >= definition.limits.runs_per_day or (last is not None and
                (instant(now) - instant(last)).total_seconds() < definition.limits.cooldown_seconds):
            return None
        ident = uuid.uuid4().hex
        binding = encoded(self._binding(definition, snapshot, result))
        self.db.execute('''INSERT INTO background_deliveries(id,run_id,conversation_id,kind,channel,
            source_key,content_sha256,binding,result_id,after_sequence,status,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?)''',
            (ident, run['id'], cid, definition.kind, definition.delivery, snapshot['source_key'],
             content, binding, result_id, head, now))
        self._fresh(self._row(ident), ledger)
        return ident

    def cancel_unseen(self, reason, now):
        if not isinstance(reason, str) or re.fullmatch('[a-z_]{1,64}', reason) is None:
            raise Failure('delivery_reason', 'Use a bounded cancellation reason.')
        with self.transaction():
            self.db.execute("UPDATE background_deliveries SET status='cancelled',finished_at=?,reason=? WHERE status='pending'",
                            (utc(instant(now)), reason))

    def sweep(self, eligible, now):
        """Revoke unseen proposals when their activity/source lease no longer holds."""
        with self.transaction():
            rows = self.db.execute("SELECT * FROM background_deliveries WHERE status='pending' ORDER BY sequence LIMIT 65").fetchall()
            for row in rows:
                try:
                    eligible(self._fresh(row))
                except Failure as error:
                    self.db.execute("UPDATE background_deliveries SET status='cancelled',finished_at=?,reason=? WHERE id=?",
                                    (utc(instant(now)), error.code, row['id']))

    def _notice(self, row):
        if row['status'] != 'pending':
            raise Failure('delivery_stale', 'That delivery is no longer pending.')
        self._fresh(row)
        result = json.loads(self.store._body(row['result_id']))
        if hashlib.sha256(result['text'].encode()).hexdigest() != row['content_sha256']:
            raise Failure('delivery_integrity', 'The retained delivery result changed.')
        key = self.db.execute('SELECT handle_key FROM memory_banks WHERE slot=1').fetchone()[0]
        receipt = hmac.new(key, encoded(dict(state_id=self.store.state_id,
            delivery_id=row['id'], binding=row['binding'], result_id=row['result_id'],
            content_sha256=row['content_sha256'])).encode(), hashlib.sha256).hexdigest()
        return dict(self.metadata(row), text=result['text'], receipt=receipt)

    def pending(self, *, eligible, after=0, limit=5):
        if type(limit) is not int or not 1 <= limit <= 5 or type(after) is not int or not 0 <= after < 2**63:
            raise Failure('delivery_page', 'Use a bounded exact delivery page.')
        with self.transaction():
            rows = self.db.execute("SELECT * FROM background_deliveries WHERE status='pending' AND sequence>? ORDER BY sequence LIMIT ?",
                                   (after, limit + 1)).fetchall()
            notices = []
            for row in rows[:limit]:
                try:
                    eligible(self._fresh(row))
                    notices.append(self._notice(row))
                except Failure as error:
                    if error.code in ('job_activity', 'job_forbidden', 'delivery_stale', 'delivery_foreground',
                                      'reminder_stale', 'reminder_source_changed', 'reminder_source_unavailable'):
                        continue
                    raise
            return dict(items=notices, next_cursor=rows[limit-1]['sequence'] if len(rows) > limit else None)

    def validate(self, ident, receipt, *, eligible):
        with self.transaction():
            return self._validated(ident, receipt, eligible)

    def _validated(self, ident, receipt, eligible):
        row = self._row(ident)
        eligible(self._fresh(row))
        notice = self._notice(row)
        if not isinstance(receipt, str) or not hmac.compare_digest(receipt, notice['receipt']):
            raise Failure('delivery_stale', 'Use the exact still-current delivery receipt.')
        return notice

    def _receipt(self, key, intent):
        identity(key)
        digest = hashlib.sha256(encoded(intent).encode()).hexdigest()
        row = self.db.execute('SELECT * FROM background_delivery_operations WHERE mutation_key=?', (key,)).fetchone()
        if row is not None and row['intent_sha256'] != digest:
            raise Failure('delivery_conflict', 'Mutation key belongs to another frozen intent.')
        return digest, dict(json.loads(row['receipt']), replayed=True) if row else None

    def _record(self, key, digest, result, now):
        result = dict(result, mutation_key=key, intent_sha256=digest, replayed=False)
        self.db.execute('INSERT INTO background_delivery_operations VALUES(?,?,?,?)', (key, digest, encoded(result), now))
        return result

    def acknowledge(self, ident, receipt, *, channel, mutation_key, now, eligible):
        if channel not in ('presented', 'dismissed'):
            raise Failure('delivery_receipt', 'Explicitly record presented or dismissed.')
        now = utc(instant(now))
        with self.transaction():
            digest, prior = self._receipt(mutation_key, dict(action='acknowledge', delivery_id=ident,
                                                          receipt=receipt, channel=channel))
            if prior is not None:
                return prior
            self._validated(ident, receipt, eligible)
            row = self._row(ident)
            # The reminder owner remains authoritative, inside this SAME commit.
            # A stale second occurrence rolls back all acknowledgements.
            for item in json.loads(row['binding'])['reminders']:
                key = hashlib.sha256((mutation_key + ':' + item['occurrence_id']).encode()).hexdigest()[:32]
                self.store.reminders._acknowledge(item['occurrence_id'], item['receipt'],
                    mutation_key=key, channel=channel, now=now)
            self.db.execute('UPDATE background_deliveries SET status=?,finished_at=? WHERE id=?', (channel, now, ident))
            if channel == 'presented':
                # This is the original background RESULT, labeled as such, not
                # a synthetic user/assistant exchange or duplicate text owner.
                anchor = self.db.execute('SELECT id FROM recall_turns WHERE conversation_id=? AND sequence=?',
                                        (row['conversation_id'], row['after_sequence'])).fetchone()
                if anchor is None:
                    raise Failure('delivery_stale', 'The presentation history anchor is unavailable.')
                source = self.db.execute('''INSERT INTO history_sources(conversation_id,turn_id,sequence,artifact_id,role)
                    VALUES(?,?,?,?,'background')''',
                    (row['conversation_id'], anchor[0], row['after_sequence'], row['result_id'])).lastrowid
                self.db.execute('INSERT INTO history_fts(rowid,scope,body) SELECT ?,?,body FROM artifact_payloads WHERE artifact_id=?',
                                (source, 'c' + row['conversation_id'], row['result_id']))
            return self._record(mutation_key, digest, dict(status=channel, delivery_id=ident), now)

    def cancel(self, ident, *, mutation_key, now):
        now = utc(instant(now))
        with self.transaction():
            digest, prior = self._receipt(mutation_key, dict(action='cancel', delivery_id=ident))
            if prior is not None:
                return prior
            row = self._row(ident)
            if row['status'] == 'pending':
                self.db.execute("UPDATE background_deliveries SET status='cancelled',finished_at=?,reason='owner_cancelled' WHERE id=?", (now, ident))
            elif row['status'] != 'cancelled':
                raise Failure('delivery_stale', 'An already acknowledged delivery cannot be cancelled.')
            return self._record(mutation_key, digest, dict(status='cancelled', delivery_id=ident), now)

    def lookup(self, key, expected_intent):
        if not isinstance(expected_intent, str) or re.fullmatch('[a-f0-9]{64}', expected_intent) is None:
            raise Failure('delivery_receipt', 'Use the exact original intent digest.')
        with self.transaction():
            row = self.db.execute('SELECT * FROM background_delivery_operations WHERE mutation_key=?', (identity(key),)).fetchone()
            if row is None:
                return dict(status='absent')
            if row['intent_sha256'] != expected_intent:
                raise Failure('delivery_conflict', 'Receipt belongs to another exact intent.')
            return dict(json.loads(row['receipt']), replayed=True)

    def read(self, ident):
        with self.transaction():
            return self.metadata(self._row(ident))

    def page(self, cid, *, after=0, limit=20):
        if type(limit) is not int or not 1 <= limit <= 50 or type(after) is not int or not 0 <= after < 2**63:
            raise Failure('delivery_page', 'Use a bounded delivery-history page.')
        with self.transaction():
            rows = self.db.execute('SELECT * FROM background_deliveries WHERE conversation_id=? AND sequence>? ORDER BY sequence LIMIT ?',
                                   (identity(cid), after, limit+1)).fetchall()
            return dict(items=[self.metadata(row) for row in rows[:limit]],
                        next_cursor=rows[limit-1]['sequence'] if len(rows)>limit else None)

    def history_records(self, cid, after_sequence, ledger):
        """Called under the caller's SQL/payload ledger; exact whole presentations."""
        rows = self.db.execute('''SELECT d.id,d.result_id,d.kind,d.channel,d.finished_at,a.sha256
            FROM background_deliveries d JOIN artifacts a ON a.id=d.result_id
            WHERE d.conversation_id=? AND d.after_sequence=? AND d.status='presented'
            AND NOT EXISTS(SELECT 1 FROM excluded_background_runs e WHERE e.run_id=d.run_id)
            ORDER BY d.sequence LIMIT 17''',
            (cid, after_sequence)).fetchall()
        if len(rows) > 16:
            raise Failure('context_limit', 'This exchange has too many whole background presentations; use exact history reads.')
        records = []
        for row in rows:
            body = self.store._history.body(row['result_id'], 'background_result', ledger, 131072)
            result = json.loads(body)
            records.append(dict(delivery_id=row['id'], artifact_id=row['result_id'], sha256=row['sha256'],
                kind=row['kind'], channel=row['channel'], presented_at=row['finished_at'], text=result['text']))
        return records

    def history_messages(self, cid, after_sequence, ledger):
        messages = []
        for record in self.history_records(cid, after_sequence, ledger):
            messages.extend((Message('system', 'Application background presentation: ' + encoded({k:v for k,v in record.items() if k!='text'})
                + '. Renderer reported presentation, not proof of reading or action. This is not a user message.'),
                Message('assistant', record['text'])))
        return tuple(messages)
