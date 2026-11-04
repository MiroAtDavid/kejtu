"""Durable bounded jobs, immutable definitions and terminal run evidence.

No timer thread, inference, reminder acknowledgement or task mutation lives here.
The application supplies clock/activity observations and executes installed
procedures. Reservation is durable before IO; recovery never replays a run.
"""
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
import re
import sqlite3
import time
import uuid

from ...core.jobs import REVIEW_KINDS, QUIET_KINDS, JobDefinition, RUN_TERMINAL
from ...core.records import Failure
from ...core.research import SERIALIZED_BYTES as RESEARCH_SERIALIZED_BYTES
from ...core.reference import exact_page
from ...core.schedules import instant, utc

JOB_TABLES = ('background_job_operations', 'background_run_events', 'background_runs',
              'background_job_versions', 'background_jobs')
JOB_SCHEMA = '''
CREATE TABLE background_jobs(id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
 title TEXT NOT NULL, kind TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
 cancelled INTEGER NOT NULL DEFAULT 0 CHECK(cancelled IN (0,1)),
 next_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX background_jobs_due ON background_jobs(next_at,id) WHERE enabled=1 AND cancelled=0;
CREATE TABLE background_job_versions(job_id TEXT NOT NULL REFERENCES background_jobs(id),
 revision INTEGER NOT NULL, definition TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(job_id,revision));
CREATE TRIGGER background_definition_immutable BEFORE UPDATE ON background_job_versions
 BEGIN SELECT RAISE(ABORT,'Job definitions are immutable revisions.'); END;
CREATE TABLE background_runs(sequence INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,
 job_id TEXT NOT NULL, revision INTEGER NOT NULL,
 scheduled_at TEXT NOT NULL, covered_through TEXT NOT NULL, created_at TEXT NOT NULL,
 started_at TEXT, finished_at TEXT,
 status TEXT NOT NULL CHECK(status IN ('pending','running','cancel_requested','completed','deferred','failed','cancelled','interrupted','skipped')),
 result_id TEXT REFERENCES artifacts(id), error_code TEXT,
 FOREIGN KEY(job_id,revision) REFERENCES background_job_versions(job_id,revision),
 UNIQUE(job_id,revision,scheduled_at));
CREATE INDEX background_runs_job ON background_runs(job_id,sequence);
CREATE INDEX background_runs_budget ON background_runs(job_id,started_at);
CREATE INDEX background_runs_pending ON background_runs(sequence) WHERE status='pending';
CREATE UNIQUE INDEX background_one_runner ON background_runs((1)) WHERE status IN ('running','cancel_requested');
CREATE UNIQUE INDEX background_one_pending_job ON background_runs(job_id) WHERE status='pending';
CREATE TRIGGER background_run_identity_immutable BEFORE UPDATE OF
 id,job_id,revision,scheduled_at,covered_through,created_at ON background_runs
 BEGIN SELECT RAISE(ABORT,'Run identity and input definition are immutable.'); END;
CREATE TRIGGER background_run_terminal_immutable BEFORE UPDATE ON background_runs
 WHEN OLD.status IN ('completed','deferred','failed','cancelled','interrupted','skipped')
 BEGIN SELECT RAISE(ABORT,'Terminal run outcomes are immutable.'); END;
CREATE TABLE background_run_events(sequence INTEGER PRIMARY KEY,
 run_id TEXT NOT NULL REFERENCES background_runs(id), kind TEXT NOT NULL,
 payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX background_events_run ON background_run_events(run_id,sequence);
CREATE TABLE background_job_operations(mutation_key TEXT PRIMARY KEY, intent_sha256 TEXT NOT NULL,
 result TEXT NOT NULL, created_at TEXT NOT NULL);
'''


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def identity(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{32}', value) is None:
        raise Failure('job_identity', 'Use an exact job, run or mutation identity.')
    return value


class JobStore:
    @property
    def care(self):
        return self.store.care

    def __init__(self, store):
        self.store, self.db = store, store.db

    @contextmanager
    def _transaction(self):
        if self.db.in_transaction:
            raise Failure('job_busy', 'Job operations cannot commit another owner transaction.')
        if not self.store.path.is_file():
            raise Failure('job_unavailable', 'The selected job store is unavailable.')
        committing = False
        try:
            self.db.execute('BEGIN IMMEDIATE')
            yield
            committing = True
            self.db.commit()
        except BaseException as error:
            uncertain = committing and not self.db.in_transaction
            self.db.rollback()
            if uncertain:
                raise Failure('job_outcome_unknown', 'Inspect the exact job mutation/run receipt before another intent.') from error
            raise

    def _receipt(self, key, intent):
        identity(key)
        digest = hashlib.sha256(encoded(intent).encode()).hexdigest()
        row = self.db.execute('SELECT * FROM background_job_operations WHERE mutation_key=?', (key,)).fetchone()
        if row is not None:
            if row['intent_sha256'] != digest:
                raise Failure('job_conflict', 'Mutation key already belongs to a different job intent.')
            return digest, dict(json.loads(row['result']), replayed=True)
        return digest, None

    def _record_receipt(self, key, digest, result, now):
        self.db.execute('INSERT INTO background_job_operations VALUES(?,?,?,?)',
                        (key, digest, encoded(result), now))
        return result

    def lookup(self, key):
        row = self.db.execute('SELECT result FROM background_job_operations WHERE mutation_key=?', (identity(key),)).fetchone()
        return dict(json.loads(row[0]), replayed=True) if row else dict(status='absent')

    def control_receipt(self, key, *, expected_intent=None):
        """Exact native recovery binding without rewriting historical receipts."""
        if expected_intent is not None and (not isinstance(expected_intent, str)
                or re.fullmatch('[0-9a-f]{64}', expected_intent) is None):
            raise Failure('job_identity', 'Use the exact saved job intent digest.')
        row = self.db.execute('SELECT intent_sha256,result FROM background_job_operations WHERE mutation_key=?',
                              (identity(key),)).fetchone()
        if row is None:
            return dict(status='absent')
        if expected_intent is not None and row['intent_sha256'] != expected_intent:
            raise Failure('job_conflict', 'The receipt belongs to another job control intent.')
        return dict(json.loads(row['result']), mutation_key=key, intent_sha256=row['intent_sha256'], replayed=True)

    def inspect(self, ident, *, revision=None):
        job = dict(self._job(ident))
        job['enabled'], job['cancelled'] = bool(job['enabled']), bool(job['cancelled'])
        return dict(job=job, selected_revision=job['revision'] if revision is None else revision,
                    definition=self.definition(ident, revision).record())

    def _job(self, ident):
        row = self.db.execute('SELECT * FROM background_jobs WHERE id=?', (identity(ident),)).fetchone()
        if row is None:
            raise Failure('job_missing', 'The selected job does not exist.')
        return row

    def definition(self, ident, revision=None):
        job = self._job(ident)
        revision = job['revision'] if revision is None else revision
        if type(revision) is not int or revision < 1:
            raise Failure('job_revision', 'Use a positive job revision.')
        row = self.db.execute('SELECT definition,sha256 FROM background_job_versions WHERE job_id=? AND revision=?',
                              (ident, revision)).fetchone()
        if row is None:
            raise Failure('job_revision', 'The selected job revision is unavailable.')
        try:
            raw = json.loads(row['definition'])
            value = JobDefinition.from_record(raw)
            if value.digest != row['sha256']:
                raise ValueError
        except (ValueError, TypeError, RecursionError):
            raise Failure('job_integrity', 'Stored job definition failed validation.') from None
        return value

    def _sources(self, definition):
        if definition.kind == 'research':
            self.store.research.guard(definition)
        if definition.conversation_id is None:
            if self.db.execute('SELECT 1 FROM memory_banks WHERE slot=1').fetchone() is None:
                raise Failure('job_forbidden', 'The notebook for this job is unavailable.')
            return
        conv = self.store.conversation(definition.conversation_id)
        read = self.db.execute('SELECT 1 FROM memory_grants WHERE conversation_id=? AND enabled=1',
                               (definition.conversation_id,)).fetchone()
        if definition.permissions.read_memory and (not conv.memory_bank_id or read is None):
            raise Failure('job_forbidden', 'Background source-read permission does not replace live memory access.')
        if definition.permissions.write_notes and not conv.notes_writable:
            raise Failure('job_forbidden', 'The current notebook-write grant does not permit this job.')

    def save(self, definition, *, mutation_key, now, job_id=None, expected_revision=None, result_check=None):
        if type(definition) is not JobDefinition:
            raise Failure('job_definition', 'Use a validated explicit job definition.')
        now = utc(instant(now))
        # Research carries the complete user assignment. Its domain owns enough
        # serialized headroom for that text and the surrounding job/profile.
        maximum = RESEARCH_SERIALIZED_BYTES if definition.kind == 'research' else 16384
        if len(encoded(definition.record()).encode()) > maximum:
            raise Failure('job_definition', 'The complete definition exceeds its control-record bound.')
        intent = dict(action='save', job_id=job_id, expected_revision=expected_revision,
                      definition=definition.record())
        with self._transaction():
            pending_care = None
            digest, prior = self._receipt(mutation_key, intent)
            if prior is not None:
                if result_check is not None: result_check(prior)
                return prior
            if definition.conversation_id is not None:
                self.store.conversation(definition.conversation_id)
            if definition.enabled:
                self._sources(definition)
            if job_id is None:
                if definition.kind == 'notebook_care' and self.db.execute("SELECT 1 FROM background_jobs WHERE kind='notebook_care' AND cancelled=0").fetchone():
                    raise Failure('care_exists', 'Update the existing notebook-care schedule instead of creating another queue.')
                if expected_revision is not None:
                    raise Failure('job_revision', 'A new job cannot claim an existing revision.')
                if self.db.execute('SELECT count(*) FROM background_jobs WHERE cancelled=0').fetchone()[0] >= 64:
                    raise Failure('job_limit', 'The active job-definition limit is reached.')
                ident, revision = uuid.uuid4().hex, 1
                self.db.execute('INSERT INTO background_jobs VALUES(?,?,?,?,?,0,?,?,?)',
                    (ident, revision, definition.title, definition.kind, int(definition.enabled),
                     utc(definition.schedule.start), now, now))
            else:
                job = self._job(job_id)
                if type(expected_revision) is not int or job['revision'] != expected_revision:
                    raise Failure('job_conflict', 'The job changed; inspect its current revision.')
                if job['cancelled']:
                    raise Failure('job_cancelled', 'A cancelled job cannot be silently revived; create a new explicit job.')
                ident, revision = job_id, job['revision'] + 1
                original = self.definition(job_id)
                if original.kind != definition.kind and any(kind in ('notebook_care','research') for kind in (original.kind, definition.kind)):
                    raise Failure('care_kind', 'Notebook care has its own explicit schedule; keep this job kind unchanged.')
                # Ordinary metadata/permission edits do not rearm a completed
                # occurrence. Only an explicit schedule change starts a new
                # anchor; reserved old runs are cancelled, never auto-replayed.
                next_at = job['next_at'] if original.schedule.timing_key == definition.schedule.timing_key else utc(definition.schedule.start)
                if (definition.kind == 'notebook_care' and definition.enabled
                        and original.schedule.timing_key == definition.schedule.timing_key):
                    # A settings save must not discard an unstarted daily pass.
                    # Transfer only pending work to the new definition, never
                    # replay a started/completed run or revive a disabled job.
                    pending_care = self.db.execute("SELECT * FROM background_runs WHERE job_id=? AND status='pending'",
                        (ident,)).fetchone()
                self._cancel_runs(ident, now, 'definition_changed')
                self.db.execute('UPDATE background_jobs SET revision=?,title=?,kind=?,enabled=?,next_at=?,updated_at=? WHERE id=?',
                    (revision, definition.title, definition.kind, int(definition.enabled),
                     next_at, now, ident))
            self.db.execute('INSERT INTO background_job_versions VALUES(?,?,?,?,?)',
                (ident, revision, encoded(definition.record()), definition.digest, now))
            if pending_care is not None:
                self.db.execute('''INSERT INTO background_runs(id,job_id,revision,scheduled_at,covered_through,created_at,status)
                    VALUES(?,?,?,?,?,?,'pending')''', (uuid.uuid4().hex,ident,revision,
                    pending_care['scheduled_at'],pending_care['covered_through'],now))
            if definition.kind == 'notebook_care' and job_id is not None:
                # An explicit user Save may retry attention items; immutable run
                # history still enforces the same rolling daily spend allowance.
                self.db.execute('UPDATE notebook_care_state SET failures=0,retry_at=NULL,attention=NULL WHERE job_id=?', (ident,))
                self.db.execute("UPDATE notebook_care_items SET status='pending' WHERE job_id=? AND status IN ('blocked','deferred')", (ident,))
            receipt = dict(status='committed', job_id=ident, revision=revision, replayed=False)
            if result_check is not None: result_check(receipt)
            if definition.kind == 'research': self.store.research.bump()
            return self._record_receipt(mutation_key, digest, receipt, now)

    def _cancel_runs(self, job_id, now, reason):
        self.db.execute("UPDATE background_runs SET status='cancelled',finished_at=?,error_code=? WHERE job_id=? AND status='pending'",
                         (now, reason, job_id))
        self.db.execute("UPDATE background_runs SET status='cancel_requested',error_code=? WHERE job_id=? AND status='running'",
                         (reason, job_id))

    def cancel(self, job_id, *, expected_revision, mutation_key, now):
        now = utc(instant(now))
        with self._transaction():
            digest, prior = self._receipt(mutation_key, dict(action='cancel', job_id=job_id,
                                                             expected_revision=expected_revision))
            if prior is not None:
                return prior
            job = self._job(job_id)
            if type(expected_revision) is not int or job['revision'] != expected_revision:
                raise Failure('job_conflict', 'The job changed before cancellation.')
            self._cancel_runs(job_id, now, 'job_cancelled')
            if job['kind'] == 'research': self.store.research.bump()
            self.db.execute('UPDATE background_jobs SET enabled=0,cancelled=1,next_at=NULL,updated_at=? WHERE id=?', (now, job_id))
            return self._record_receipt(mutation_key, digest,
                dict(status='cancelled', job_id=job_id, revision=job['revision'], replayed=False), now)

    def page(self, *, after='', limit=20):
        if after:
            identity(after)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise Failure('job_page', 'Use a bounded job page size.')
        rows = [dict(row) for row in self.db.execute('SELECT * FROM background_jobs WHERE id>? ORDER BY id LIMIT ?', (after, limit + 1))]
        return dict(items=rows[:limit], next_cursor=rows[limit - 1]['id'] if len(rows) > limit else None)

    def cancel_run(self, ident, *, mutation_key, now):
        """Cancel this occurrence, not its schedule or independently committed work."""
        now = utc(instant(now))
        with self._transaction():
            digest, prior = self._receipt(mutation_key, dict(action='cancel_run', run_id=identity(ident)))
            if prior is not None:
                return prior
            run = self.run(ident)
            status = 'unchanged'
            if run['status'] == 'pending':
                self.db.execute("UPDATE background_runs SET status='cancelled',finished_at=?,error_code='owner_cancelled' WHERE id=?",
                                (now, ident))
                status = 'cancelled'
            elif run['status'] == 'running':
                self.db.execute("UPDATE background_runs SET status='cancel_requested',error_code='owner_cancelled' WHERE id=?", (ident,))
                status = 'cancel_requested'
            current = self.run(ident)
            return self._record_receipt(mutation_key, digest, dict(status=status, run_id=ident,
                job_id=run['job_id'], revision=run['revision'], run_status=current['status'], replayed=False), now)

    def poll(self, now, *, limit=20):
        """Materialize at most one pending run/job; disabled by default at creation.

        This method performs no execution. A missed range is one inspectable
        coalesced/skipped run, never a backlog of model calls.
        """
        now = utc(instant(now))
        if type(limit) is not int or not 1 <= limit <= 50:
            raise Failure('job_page', 'Use a bounded scheduler batch size.')
        with self._transaction():
            # Finish an interrupted occurrence before materializing a newer one.
            self.store.research.continuation(now)
            rows = self.db.execute('''SELECT * FROM background_jobs j WHERE enabled=1 AND cancelled=0 AND next_at<=?
                AND NOT EXISTS(SELECT 1 FROM background_runs r WHERE r.job_id=j.id AND r.status IN ('pending','running','cancel_requested'))
                ORDER BY next_at,id LIMIT ?''', (now, limit)).fetchall()
            made = []
            for job in rows:
                definition = self.definition(job['id'])
                due = definition.schedule.advance(job['next_at'], now)
                if due is None:
                    continue
                ident = uuid.uuid4().hex
                state = 'pending' if due.materialize else 'skipped'
                self.db.execute('''INSERT INTO background_runs(id,job_id,revision,scheduled_at,covered_through,created_at,status,finished_at)
                    VALUES(?,?,?,?,?,?,?,?)''', (ident, job['id'], job['revision'], due.scheduled_at or due.first_due,
                    due.covered_through, now, state, None if due.materialize else now))
                self.db.execute('UPDATE background_jobs SET next_at=? WHERE id=?', (due.next_at, job['id']))
                made.append(ident)
            self.store.care.continuation(now)
            return made

    def run(self, ident):
        row = self.db.execute('SELECT * FROM background_runs WHERE id=?', (identity(ident),)).fetchone()
        if row is None:
            raise Failure('job_run_missing', 'The selected background run does not exist.')
        return dict(row)

    def runs(self, job_id, *, before=None, limit=20):
        self._job(job_id)
        if type(limit) is not int or not 1 <= limit <= 50 or before is not None and (type(before) is not int or before < 1):
            raise Failure('job_page', 'Use a bounded run page and exact sequence cursor.')
        rows = [dict(row) for row in self.db.execute('''SELECT * FROM background_runs
            WHERE job_id=? AND (? IS NULL OR sequence<?) ORDER BY sequence DESC LIMIT ?''', (job_id, before, before, limit + 1))]
        return dict(items=rows[:limit], next_cursor=rows[limit - 1]['sequence'] if len(rows) > limit else None)

    def _live(self, run):
        job = self._job(run['job_id'])
        if (job['cancelled'] or not job['enabled'] or job['revision'] != run['revision']
                or run['status'] != 'running'):
            raise Failure('job_cancelled', 'Run permission or definition changed; no further effect is authorized.')
        if self.store.active_turn_id() is not None:
            raise Failure('job_foreground', 'Foreground work takes precedence over background effects.')
        definition = self.definition(job['id'], run['revision'])
        self._sources(definition)
        return definition

    def claim(self, ident, *, now, idle_for):
        now = utc(instant(now))
        if type(idle_for) is not int or not 0 <= idle_for <= 31536000:
            raise Failure('job_activity', 'Use a bounded current application idle observation.')
        with self._transaction():
            row = self.run(ident)
            if (row['status'] != 'pending' or self.store.active_turn_id() is not None
                    or now < row['scheduled_at'] or now < row['created_at']
                    or self.db.execute("SELECT 1 FROM background_runs WHERE status IN ('running','cancel_requested') LIMIT 1").fetchone()):
                return None
            job = self._job(row['job_id'])
            if job['cancelled'] or not job['enabled'] or job['revision'] != row['revision']:
                return None
            definition = self.definition(job['id'], row['revision'])
            self._sources(definition)
            if definition.kind == 'notebook_care':
                care = self.db.execute('SELECT attention,retry_at FROM notebook_care_state WHERE job_id=?', (job['id'],)).fetchone()
                if care and (care['attention'] or care['retry_at'] and now < care['retry_at']):
                    return None
            if idle_for < definition.idle_seconds:
                return None
            if definition.kind not in QUIET_KINDS and self.db.execute(
                    "SELECT 1 FROM background_deliveries WHERE status='pending' LIMIT 1").fetchone():
                return None
            since = utc(instant(now) - timedelta(days=1))
            count, last = self.db.execute('SELECT count(*),max(started_at) FROM background_runs WHERE job_id=? AND started_at>?',
                                         (job['id'], since)).fetchone()
            # Notebook care owns a finite revision queue: its short resumable
            # batches are continuation of one sweep, not additional daily jobs.
            # Per-revision retries and run deadlines still bound failed work.
            if definition.kind == 'research':
                # The daily ceiling counts occurrences, not interrupted attempts.
                count = self.db.execute("""SELECT count(*) FROM background_runs r
                    WHERE r.job_id=? AND r.started_at>? AND NOT EXISTS(
                        SELECT 1 FROM background_run_events e WHERE e.run_id=r.id AND e.kind='research_retry')""",
                    (job['id'], since)).fetchone()[0]
            continuation = definition.kind == 'research' and self.store.research.retry_context(ident)['attempt'] > 0
            if (definition.kind != 'notebook_care' and not continuation and count >= definition.limits.runs_per_day
                    or last and (instant(now) - instant(last)).total_seconds() < definition.limits.cooldown_seconds):
                return None
            self.db.execute("UPDATE background_runs SET status='running',started_at=? WHERE id=?", (now, ident))
            reservation = dict(self.run(ident), definition=definition.record())
            if definition.kind == 'research':
                reservation['execution_limits'] = self.store.research.allowance(row, definition)
            return reservation

    def check_run(self, ident):
        # The application calls this before each provider/tool dispatch. Any
        # state-changing effect must recheck within its own atomic transaction.
        return self._live(self.run(ident))

    def research_waiting(self, ident, waiting):
        """A live run keeps its identity and receipts while its owner waits."""
        with self._transaction():
            definition = self.check_run(ident)
            if definition.kind != 'research' or type(waiting) is not bool:
                raise Failure('job_kind', 'Only active research can wait for foreground work.')
            self.db.execute('UPDATE background_runs SET error_code=? WHERE id=?',
                ('research_paused' if waiting else None, ident))

    def review_conversation(self, ident):
        definition = self.check_run(ident)
        if definition.kind not in REVIEW_KINDS:
            raise Failure('job_kind', 'Notebook review requires its own explicit job kind.')
        return self.store.conversation(definition.conversation_id) if definition.conversation_id else None

    def notebook_grant(self, ident):
        """Independent read authority from one saved, enabled review and live run."""
        definition = self.check_run(ident)
        if definition.kind not in REVIEW_KINDS or definition.conversation_id is not None or not definition.permissions.read_memory:
            raise Failure('job_forbidden', 'This run has no independent notebook permission.')
        bank = self.db.execute('SELECT id FROM memory_banks WHERE slot=1').fetchone()
        return dict(bank_id=bank['id'], enabled=True, epoch=self.run(ident)['revision'], run_id=ident)

    def finish(self, ident, *, status, now, result=None, error_code=None, delivery_snapshot=None, delivery_ledger=None):
        if status not in RUN_TERMINAL or status == 'skipped':
            raise Failure('job_status', 'Use a terminal execution outcome.')
        if (result is not None and not isinstance(result, str)
                or error_code is not None and (not isinstance(error_code, str) or re.fullmatch('[a-z_]{1,64}', error_code) is None)):
            raise Failure('job_result', 'Run result/error exceeds the exact bounded record contract.')
        if status not in ('completed', 'deferred') and result is not None:
            raise Failure('job_result', 'A failed/cancelled run cannot publish a successful result.')
        now = utc(instant(now))
        if delivery_snapshot is not None and delivery_ledger is None:
            raise Failure('delivery_ledger', 'Delivery preparation must retain the originating job ledger.')
        with self._source_work(delivery_ledger) if delivery_snapshot is not None else self._transaction():
            row = self.run(ident)
            definition = self.definition(row['job_id'], row['revision'])
            # Full body-free mutation receipts scale with the saved operation
            # allowance. Keep one exact result, delivered by existing byte pages.
            result_limit = (6 * 32768 + 4096 + definition.limits.operations * self.store.notes.policy.outcome_bytes
                            if definition.kind in REVIEW_KINDS else 131072)
            if result is not None and len(result.encode()) > result_limit:
                raise Failure('job_result', 'Run result exceeds its saved procedure allowance.')
            if row['status'] in RUN_TERMINAL:
                return row  # Receipt lookup, never overwrite a completed outcome.
            if row['status'] not in ('running', 'cancel_requested'):
                raise Failure('job_status', 'Only a reserved run can finish execution.')
            if status in ('completed', 'deferred'):
                try:
                    self._live(row)
                except Failure as error:
                    status, result, error_code = 'cancelled', None, error.code
            if status == 'completed' and definition.kind == 'research':
                self.store.research.publish(row, json.loads(result), now)
            body = self.store._put('background_result', result) if result is not None else None
            if status == 'completed' and delivery_snapshot is not None:
                delivery_id = self.store.deliveries.enqueue(row, self.definition(row['job_id'], row['revision']),
                    delivery_snapshot, body, json.loads(result), now, delivery_ledger)
                if delivery_id is None:
                    # Retain the exact private result, but do not claim it entered
                    # the delivery queue when a global safeguard withheld it.
                    error_code = 'delivery_suppressed'
            self.db.execute('UPDATE background_runs SET status=?,finished_at=?,result_id=?,error_code=? WHERE id=?',
                            (status, now, body, error_code, ident))
            self.store.care.settled(row, status, error_code, now)
            return self.run(ident)

    def result_page(self, ident, *, offset=0, maximum=16384):
        run = self.run(ident)
        if run['result_id'] is None:
            raise Failure('job_result_missing', 'This run has no published result.')
        if type(maximum) is not int or not 1 <= maximum <= 32768:
            raise Failure('job_page', 'Use a bounded result byte page.')
        return exact_page(run['result_id'], self.store._body(run['result_id']), offset, maximum).envelope()

    def recover(self, now):
        """Called inside the store owner's existing restart transaction."""
        now = utc(instant(now))
        for run in self.db.execute("SELECT * FROM background_runs WHERE status IN ('running','cancel_requested')").fetchall():
            self.store.care.settled(dict(run), 'failed' if run['status']=='running' else 'cancelled', 'owner_exit_unknown', now)
        self.db.execute("UPDATE background_runs SET status='interrupted',finished_at=?,error_code='owner_exit_unknown' WHERE status='running'", (now,))
        self.db.execute("UPDATE background_runs SET status='cancelled',finished_at=?,error_code='owner_exit_after_cancel' WHERE status='cancel_requested'", (now,))

    def pending(self, *, after=0, limit=20):
        if type(after) is not int or not 0 <= after < 2**63 or type(limit) is not int or not 1 <= limit <= 50:
            raise Failure('job_page', 'Use a bounded pending-run page.')
        rows = [dict(row) for row in self.db.execute("SELECT * FROM background_runs WHERE status='pending' AND sequence>? ORDER BY sequence LIMIT ?", (after, limit + 1))]
        return dict(items=rows[:limit], next_cursor=rows[limit - 1]['sequence'] if len(rows) > limit else None)

    @contextmanager
    def _source_work(self, ledger):
        if self.db.in_transaction:
            raise Failure('job_busy', 'Source inspection cannot replace another owner transaction or SQL budget.')
        stopped = None
        def progress():
            nonlocal stopped
            try:
                ledger.spend_sql()
                if time.monotonic() >= ledger.deadline:
                    raise Failure('timeout', 'Background source deadline expired.')
            except Failure as error:
                # Preserve soft review/search exhaustion and deadline identity;
                # the caller may close a report but may never bypass revocation.
                stopped = error if error.code in ('recall_search_limit', 'recall_query_limit', 'review_sql_limit',
                    'review_payload_limit', 'review_time_limit', 'timeout') else Failure(
                        'job_source_limit', 'Background source work allowance exhausted.')
            return int(stopped is not None)
        self.db.set_progress_handler(progress, 100)
        try:
            with self._transaction():
                yield
                if progress():
                    raise stopped
        except sqlite3.OperationalError:
            if stopped:
                raise stopped from None
            raise
        finally:
            self.db.set_progress_handler(None, 0)

    def _source_body(self, ident, ledger):
        row = self.db.execute('SELECT kind,sha256,bytes FROM artifacts WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise Failure('job_source_unavailable', 'A selected original is unavailable.')
        ledger.spend_payload(row['bytes'])
        return dict(artifact_id=ident, kind=row['kind'], sha256=row['sha256'], text=self.store._body(ident))

    def source_snapshot(self, ident, ledger):
        """Bounded exact originals, never a fake foreground turn.

        A source item is one complete exchange or one reminder. Selected bodies
        never shrink silently to manufacture admission.
        """
        with self._source_work(ledger):
            definition = self.check_run(ident)
            cid = definition.conversation_id
            if cid is None:
                from ...core.background import NOTEBOOK_CONTEXT
                grant = self.notebook_grant(ident)
                value = dict(system_prompt=NOTEBOOK_CONTEXT, grant=grant, latest_sequence=None,
                    configuration=None, conversation=dict(conversation_id=None,
                        selected_complete_exchanges=[], earlier_exchanges_omitted=False,
                        notebook_read=False, scope='notebook'), reminders=[])
                value['source_key'] = hashlib.sha256(encoded(value).encode()).hexdigest()
                return value
            config = self._source_body(self.store.configuration_id(cid), ledger)
            saved = self.store.conversation(cid)
            grant = dict(self.db.execute('SELECT bank_id,enabled,epoch FROM memory_grants WHERE conversation_id=?', (cid,)).fetchone())
            head = self.db.execute('SELECT max(sequence) FROM recall_turns WHERE conversation_id=?', (cid,)).fetchone()[0]
            notices = []
            if definition.kind == 'reminder_delivery':
                # Another source conversation cannot grant this job access.
                # Manual independent records have no conversation owner.
                rows = self.db.execute('''SELECT o.reminder_id FROM reminder_occurrences o
                    JOIN reminders r ON r.id=o.reminder_id
                    JOIN reminder_versions v ON v.reminder_id=r.id AND v.revision=r.revision
                    WHERE o.status='pending' AND r.enabled=1 AND r.cancelled=0
                    AND NOT EXISTS(SELECT 1 FROM json_each(v.definition,'$.support') s
                        WHERE json_extract(s.value,'$.conversation_id')!=?)
                    ORDER BY o.sequence LIMIT 50''', (cid,)).fetchall()
                for row in rows:
                    reminder = self.store.reminders._reminder(row['reminder_id'])
                    value = self.store.reminders._definition(row['reminder_id'])
                    ledger.spend_payload(len(encoded(value.record()).encode()))
                    if any(source.conversation_id != cid for source in value.support):
                        continue
                    for source in value.support:
                        metadata = self.db.execute('SELECT bytes FROM artifacts WHERE id=?', (source.user_id,)).fetchone()
                        if metadata is not None:
                            ledger.spend_payload(metadata['bytes'])
                    notice = self.store.reminders._notice(reminder, self.store.reminders._pending(row['reminder_id']))
                    if notice['deliverable']:
                        notices.append(notice)
                    if len(notices) >= max(1, definition.limits.source_items // 2):
                        break
            limit = definition.limits.source_items - len(notices)
            rows = self.db.execute("SELECT id,sequence,user_id,answer_id,created_at,finished_at FROM recall_turns WHERE conversation_id=? AND status='completed' ORDER BY sequence DESC LIMIT ?", (cid, limit + 1)).fetchall()
            exchanges = []
            for row in reversed(rows[:limit]):
                user, answer = self._source_body(row['user_id'], ledger), self._source_body(row['answer_id'], ledger)
                if user['kind'] != 'user_message' or answer['kind'] != 'assistant_message':
                    raise Failure('job_source_unavailable', 'Selected exchange origins are inconsistent.')
                exchanges.append(dict(turn_id=row['id'], sequence=row['sequence'], created_at=row['created_at'],
                    finished_at=row['finished_at'], user=user, answer=answer))
                presentations = self.store.deliveries.history_records(cid, row['sequence'], ledger)
                if presentations:
                    exchanges[-1]['background_presentations'] = presentations
            value = dict(system_prompt=saved.system_prompt, grant=grant, latest_sequence=head,
                configuration=dict(artifact_id=config['artifact_id'], sha256=config['sha256']),
                conversation=dict(conversation_id=cid, selected_complete_exchanges=exchanges,
                    earlier_exchanges_omitted=len(rows) > limit, notebook_read=False,
                    scope='bounded_recent_completed_exchanges'), reminders=notices)
            binding = dict(kind=definition.kind, config_id=config['artifact_id'], config_sha256=config['sha256'],
                grant=grant, latest_sequence=head, conversation=value['conversation'], reminders=notices)
            value['source_key'] = hashlib.sha256(encoded(binding).encode()).hexdigest()
            return value

    def check_snapshot(self, ident, snapshot, ledger):
        if self.source_snapshot(ident, ledger)['source_key'] != snapshot['source_key']:
            raise Failure('job_source_changed', 'Selected originals or permissions changed; no draft may be published.')

    def repeated(self, ident, source_key):
        row = self.run(ident)
        prior = self.db.execute("SELECT id FROM background_runs WHERE job_id=? AND status='completed' AND sequence<? ORDER BY sequence DESC LIMIT 1", (row['job_id'], row['sequence'])).fetchone()
        if prior is None:
            return False
        event = self.db.execute("SELECT payload FROM background_run_events WHERE run_id=? AND kind='inputs' ORDER BY sequence LIMIT 1", (prior[0],)).fetchone()
        return event is not None and json.loads(event[0]).get('source_key') == source_key

    def cancel_unseen(self, reason, now):
        if self.store.db is None and reason == 'job_owner_closed':
            # A lost/closed storage owner has no transaction authority. Cleanup
            # must not reopen it; the next owner starts with unknown activity.
            return
        self.store.deliveries.cancel_unseen(reason, now)

    def sweep_deliveries(self, eligible, now):
        self.store.deliveries.sweep(eligible, now)

    def event(self, ident, kind, payload, *, now, draft_text=None):
        if kind not in ('inputs', 'model_started', 'model_observed', 'context_rejected', 'procedure_result', 'error', 'cancelled',
                        'operation_started', 'operation_prepared', 'operation_submitted', 'argument_failure',
                        'report_draft', 'model_interrupted') or not isinstance(payload, dict):
            raise Failure('job_event', 'Use an application-owned event kind and structured payload.')
        body = encoded(payload)
        if len(body.encode()) > 524288:
            raise Failure('job_event_limit', 'Exact event exceeded its record bound; nothing was truncated.')
        with self._transaction():
            row = self.run(ident)
            if row['status'] not in ('running', 'cancel_requested'):
                raise Failure('job_status', 'Only an active run can append execution evidence.')
            definition = self.definition(row['job_id'], row['revision'])
            if kind in ('inputs', 'model_started', 'procedure_result', 'operation_started', 'operation_submitted','report_draft'):
                self._live(row)
            if kind == 'report_draft':
                if definition.kind != 'research' or not isinstance(draft_text,str):
                    raise Failure('job_event','Only a research run can retain a diagnostic report draft.')
                # Body and event share this transaction. No orphan payload may
                # escape task deletion if event validation fails below.
                body=encoded(dict(payload,artifact_id=self.store._put('background_result',draft_text)))
            elif draft_text is not None:
                raise Failure('job_event','Draft bodies belong only to report_draft events.')
            counts = dict(self.db.execute('SELECT kind,count(*) FROM background_run_events WHERE run_id=? GROUP BY kind', (ident,)).fetchall())
            yields = self._research_yields(ident) if definition.kind == 'research' else 0
            calls = definition.limits.calls + yields
            extra = (3 * definition.limits.operations + calls) if definition.kind in QUIET_KINDS else 0
            if (sum(counts.values()) >= 2 * calls + 8 + extra
                    or kind == 'inputs' and counts.get(kind, 0)
                    or kind in ('model_started', 'model_observed') and counts.get(kind, 0) >= calls):
                raise Failure('job_event_limit', 'Run-event allowance exhausted; nothing was overwritten.')
            if kind.startswith('operation_') or kind == 'argument_failure':
                self._review_event(ident, definition, kind, payload, counts)
            cursor = self.db.execute('INSERT INTO background_run_events(run_id,kind,payload,created_at) VALUES(?,?,?,?)',
                (ident, kind, body, utc(instant(now))))
            return cursor.lastrowid

    def _research_yields(self, ident):
        return self.db.execute("""SELECT count(*) FROM background_run_events WHERE run_id=?
            AND kind='model_interrupted' AND json_extract(payload,'$.reason')='foreground_pause'""", (ident,)).fetchone()[0]

    def _review_event(self, ident, definition, kind, payload, counts):
        """Durable operation phases, not a second tool dispatcher or grant owner."""
        ordinal = payload.get('ordinal')
        research = definition.kind == 'research'
        calls = definition.limits.calls + (self._research_yields(ident) if research else 0)
        observed = counts.get('model_started', 0) if research else counts.get('model_observed', 0)
        interrupted = counts.get('model_interrupted', 0) if research else 0
        if definition.kind not in QUIET_KINDS or type(ordinal) is not int or ordinal < 1:
            raise Failure('job_event', 'Only a private review can record notebook operations.')
        if kind == 'argument_failure':
            if (set(payload) != {'ordinal','position','kind','reason','arguments_sha256'}
                    or payload['kind'] not in ('memory','note')
                    or type(payload['position']) is not int or not 1 <= payload['position'] <= definition.limits.operations
                    or not isinstance(payload['reason'], str) or re.fullmatch('[a-z_]{1,64}', payload['reason']) is None
                    or not isinstance(payload['arguments_sha256'], str) or re.fullmatch('[0-9a-f]{64}', payload['arguments_sha256']) is None
                    or ordinal != observed or ordinal > calls
                    or self.db.execute("SELECT 1 FROM background_run_events WHERE run_id=? AND kind=? AND json_extract(payload,'$.ordinal')=?",
                                       (ident, kind, ordinal)).fetchone()):
                raise Failure('job_event_limit', 'Only the first bounded argument diagnostic per observed call is retained.')
            return
        if ordinal > definition.limits.operations:
            raise Failure('job_event_limit', 'Review operation exceeds its saved procedure ceiling.')
        if kind == 'operation_started':
            if definition.kind == 'research':
                from ...core.tools import schemas
                # Receipt vocabulary only; execution checks the run's actual grants.
                allowed={tool['function']['name'] for tool in schemas(history=True,web_only=True,
                    memory=True,hybrid=True,tasks=True,people=True,reminders=True,web=True,mcp=True,skills=True)}
            else:allowed={'search_memory', 'read_memory', 'note_outcome'}
            if definition.permissions.write_notes:
                allowed.add('write_note')
            if (set(payload) != {'ordinal','call_number','call_id','name','arguments'}
                    or payload['name'] not in allowed or not isinstance(payload['arguments'], dict)
                    or not isinstance(payload['call_id'], str) or not 1 <= len(payload['call_id']) <= 256
                    or ordinal != counts.get(kind, 0) + 1
                    or counts.get('operation_prepared', 0) != ordinal - 1
                    or type(payload['call_number']) is not int
                    or payload['call_number'] != observed
                    or not 1 <= payload['call_number'] < calls
                    or counts.get('model_started', 0) != counts.get('model_observed', 0) + interrupted):
                raise Failure('job_operation', 'No sequential review operation was admitted by an observed tool call.')
        else:
            previous = 'operation_started' if kind == 'operation_prepared' else 'operation_prepared'
            records = self.db.execute('''SELECT kind FROM background_run_events WHERE run_id=?
                AND kind IN (?,?,?) AND json_extract(payload,'$.ordinal')=?''',
                (ident, 'operation_started', 'operation_prepared', 'operation_submitted', ordinal)).fetchall()
            phases = {row[0] for row in records}
            if (previous not in phases or kind in phases
                    or set(payload) != ({'ordinal', 'result'} if kind == 'operation_prepared' else {'ordinal'})
                    or kind == 'operation_prepared' and not isinstance(payload['result'], dict)):
                raise Failure('job_operation', 'Review operation has no matching unconsumed prior phase.')

    def events(self, ident, *, after=0, limit=20):
        self.run(ident)
        if type(after) is not int or not 0 <= after < 2**63 or type(limit) is not int or not 1 <= limit <= 50:
            raise Failure('job_page', 'Use a bounded event page.')
        rows = [dict(row) for row in self.db.execute('SELECT sequence,kind,length(CAST(payload AS BLOB)) AS bytes,created_at FROM background_run_events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?', (ident, after, limit + 1))]
        return dict(items=rows[:limit], next_cursor=rows[limit - 1]['sequence'] if len(rows) > limit else None)

    def event_page(self, ident, sequence, *, offset=0, maximum=16384):
        self.run(ident)
        if type(sequence) is not int or not 1 <= sequence < 2**63 or type(maximum) is not int or not 1 <= maximum <= 32768:
            raise Failure('job_page', 'Use an exact event sequence and bounded byte page.')
        row = self.db.execute('SELECT payload FROM background_run_events WHERE run_id=? AND sequence=?', (ident, sequence)).fetchone()
        if row is None:
            raise Failure('job_event_missing', 'The selected run has no such event.')
        return exact_page(ident + ':' + str(sequence), row[0], offset, maximum).envelope()
