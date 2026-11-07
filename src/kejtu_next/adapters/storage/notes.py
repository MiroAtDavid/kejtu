"""Atomic authored-note revisions in the existing memory bank.

No extraction, publication as fact/skill, deletion, automatic retries or eviction.
Successful receipts survive turn failure. This store does not claim model delivery.
"""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import time
import uuid

from ...core.notes import ACTOR, NotePolicy, NoteResult, NoteScope, edited_bytes, identity, intent_digest, note_arguments, operation_key
from ...core.jobs import REVIEW_KINDS
from ...core.records import Failure
from ...core.reference import exact_page
from .history import encoded
from .memory import CHUNK_BYTES
from .note_removal import key_digest
from . import note_metadata

NOTES_SCHEMA = '''
CREATE TABLE note_write_grants(conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
 bank_id TEXT NOT NULL REFERENCES memory_banks(id), enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
 epoch INTEGER NOT NULL DEFAULT 0 CHECK(epoch>=0));
CREATE TRIGGER note_grant_changed AFTER UPDATE OF enabled,bank_id ON note_write_grants
 BEGIN
 UPDATE note_write_grants SET epoch=OLD.epoch+1 WHERE conversation_id=NEW.conversation_id;
 UPDATE memory_grants SET epoch=epoch+1 WHERE conversation_id=NEW.conversation_id;
 END;
CREATE TRIGGER note_grant_added AFTER INSERT ON note_write_grants
 BEGIN UPDATE memory_grants SET epoch=epoch+1 WHERE conversation_id=NEW.conversation_id; END;
CREATE TRIGGER note_grant_removed AFTER DELETE ON note_write_grants
 BEGIN UPDATE memory_grants SET epoch=epoch+1 WHERE conversation_id=OLD.conversation_id; END;
CREATE TABLE note_records(record_id TEXT PRIMARY KEY REFERENCES memory_records(id),
 bank_id TEXT NOT NULL REFERENCES memory_banks(id), actor TEXT NOT NULL CHECK(actor='assistant'));
CREATE INDEX note_bank ON note_records(bank_id);
CREATE TABLE note_outcomes(bank_id TEXT NOT NULL REFERENCES memory_banks(id),
 actor TEXT NOT NULL CHECK(actor='assistant'), operation_key TEXT NOT NULL,
 intent_sha256 TEXT NOT NULL, turn_id TEXT NOT NULL REFERENCES turns(id),
 outcome TEXT NOT NULL, outcome_sha256 TEXT NOT NULL,
 PRIMARY KEY(bank_id,actor,operation_key));
CREATE INDEX note_outcome_turn ON note_outcomes(turn_id);
CREATE TABLE note_revisions(revision_id TEXT PRIMARY KEY REFERENCES memory_revisions(id),
 bank_id TEXT NOT NULL REFERENCES memory_banks(id), actor TEXT NOT NULL CHECK(actor='assistant'),
 operation_key TEXT NOT NULL, parent_revision TEXT REFERENCES memory_revisions(id),
 turn_id TEXT NOT NULL REFERENCES turns(id), user_id TEXT NOT NULL REFERENCES artifacts(id),
 basis_sha256 TEXT NOT NULL, reason TEXT NOT NULL,
 FOREIGN KEY(bank_id,actor,operation_key) REFERENCES note_outcomes(bank_id,actor,operation_key)
 DEFERRABLE INITIALLY DEFERRED);
CREATE INDEX note_revision_bank ON note_revisions(bank_id);
CREATE INDEX note_revision_outcome ON note_revisions(bank_id,actor,operation_key);
CREATE TABLE note_bases(revision_id TEXT NOT NULL REFERENCES note_revisions(revision_id),
 ordinal INTEGER NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('m','s','n')),
 record_id TEXT NOT NULL, memory_revision TEXT NOT NULL, source_revision TEXT,
 sha256 TEXT NOT NULL, PRIMARY KEY(revision_id,ordinal));
'''
for _table, _bank in (
    ('note_records', '{row}.bank_id'),
    ('note_revisions', '{row}.bank_id'),
    ('note_bases', '(SELECT bank_id FROM note_revisions WHERE revision_id={row}.revision_id)'),
):
    for _event, _rows in (('INSERT', ('NEW',)), ('UPDATE', ('OLD', 'NEW')), ('DELETE', ('OLD',))):
        _banks = ','.join(_bank.format(row=row) for row in _rows)
        NOTES_SCHEMA += f'''CREATE TRIGGER {_table}_{_event.lower()}_epoch AFTER {_event} ON {_table}
         BEGIN UPDATE memory_banks SET epoch=epoch+1 WHERE id IN ({_banks}); END;\n'''

# Retain exact historical DDL for explicit verified upgrades. Foreground rows
# keep every original column/value; NULL origins are exclusively job-owned.
FOREGROUND_NOTES_SCHEMA = NOTES_SCHEMA
NOTES_SCHEMA = NOTES_SCHEMA.replace('turn_id TEXT NOT NULL REFERENCES turns(id)',
                                    'turn_id TEXT REFERENCES turns(id)').replace(
    'user_id TEXT NOT NULL REFERENCES artifacts(id)', 'user_id TEXT REFERENCES artifacts(id)').replace(
    'basis_sha256 TEXT NOT NULL, reason TEXT NOT NULL,',
    'basis_sha256 TEXT NOT NULL, reason TEXT NOT NULL,\n CHECK((turn_id IS NULL)=(user_id IS NULL)),')
NOTE_JOB_SCHEMA = '''
CREATE TABLE note_job_origins(bank_id TEXT NOT NULL, actor TEXT NOT NULL CHECK(actor='assistant'),
 operation_key TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES background_runs(id),
 event_sequence INTEGER NOT NULL REFERENCES background_run_events(sequence),
 PRIMARY KEY(bank_id,actor,operation_key),
 FOREIGN KEY(bank_id,actor,operation_key) REFERENCES note_outcomes(bank_id,actor,operation_key) ON DELETE CASCADE);
CREATE INDEX note_job_run ON note_job_origins(run_id);
'''
NOTES_SCHEMA += NOTE_JOB_SCHEMA


class NoteStore:
    def metadata(self, snapshot, revision, ledger):
        return note_metadata.load(self,snapshot,revision,ledger)

    def metadata_summary(self, snapshot, revision, ledger):
        return note_metadata.summary(self.metadata(snapshot,revision,ledger))

    def metadata_page(self, snapshot, row, offset, maximum, ledger):
        value=self.metadata(snapshot,row['id'],ledger)
        page=exact_page(row['id'],encoded(value),offset,maximum).envelope()
        return dict(page,kind='note_details',record_id=row['record_id'],revision_id=row['id'])

    def __init__(self, store, policy=NotePolicy()):
        self.store, self.db, self.memory, self.policy = store, store.db, store.memory, policy

    def grant(self, conversation_id, enabled):
        """Explicit user control; never a tool or inferred permission."""
        if type(enabled) is not bool or not identity(conversation_id):
            raise ValueError('invalid note grant')
        if self.db.in_transaction:
            raise Failure('note_transaction_busy', 'Grant changes require an independent transaction.')
        with self.db:
            read = self.db.execute('SELECT bank_id FROM memory_grants WHERE conversation_id=? AND enabled=1',
                                   (conversation_id,)).fetchone()
            if read is None:
                raise Failure('note_forbidden', 'An explicit active bank-read grant is required.')
            self.db.execute('''INSERT INTO note_write_grants VALUES(?,?,?,0)
                ON CONFLICT(conversation_id) DO UPDATE SET bank_id=excluded.bank_id,enabled=excluded.enabled''',
                (conversation_id, read[0], int(enabled)))

    def _query(self, ledger, sql, args=()):
        return self.memory.query(ledger, sql, args)

    @contextmanager
    def _transaction(self, ledger, *, write=False):
        if self.db.in_transaction:
            raise Failure('note_transaction_busy', 'A note operation cannot commit another owner\'s transaction.')
        # A live connection to an unlinked file must not certify an absent store.
        if not self.store.path.is_file():
            raise Failure('note_outcome_unknown', 'Note state is unavailable; no outcome can be established.')
        committed = False
        commit_attempted = False
        try:
            with self.memory.bounded(ledger):
                self._query(ledger, 'BEGIN IMMEDIATE' if write else 'BEGIN')
                yield
                if time.monotonic() >= ledger.deadline:
                    raise Failure('timeout', 'Note work reached the inherited deadline before commit.')
                commit_attempted = True
                self._query(ledger, 'COMMIT')
                committed = True
        except BaseException as error:
            uncertain_commit = write and commit_attempted and not self.db.in_transaction
            # Cleanup must not be cancelled by an exhausted SQL/deadline handler.
            self.db.set_progress_handler(None, 0)
            self.db.rollback()
            if write and (committed or uncertain_commit):
                raise Failure('note_outcome_unknown', 'Commit/result delivery could not be confirmed; inspect the operation key before a new intent.') from error
            if isinstance(error, Failure) and error.code == 'memory_index_unavailable':
                raise Failure('note_store_unavailable', 'Note storage failed; inspect the operation key before any new intent.') from error
            raise

    def _authority(self, snapshot, ledger, *, write_epoch=None):
        bank = self.memory._check(snapshot, ledger)
        if write_epoch is not None and snapshot.job_run_id is not None:
            definition = self.store.jobs.check_run(snapshot.job_run_id)
            if not definition.permissions.write_notes or write_epoch != snapshot.grant_epoch:
                raise Failure('note_forbidden', 'This review has no matching note-write permission.')
        elif write_epoch is not None:
            row = self._query(ledger, '''SELECT epoch FROM note_write_grants
                WHERE conversation_id=? AND bank_id=? AND enabled=1''',
                (snapshot.conversation_id, snapshot.bank_id)).fetchone()
            if row is None or row[0] != write_epoch:
                raise Failure('note_forbidden', 'No matching active note-write grant.')
        return bank

    def scope(self, snapshot, turn_id, ledger):
        if not identity(turn_id):
            raise Failure('note_origin_invalid', 'An application-owned origin turn is required.')
        with self._transaction(ledger):
            self._authority(snapshot, ledger)
            row = self._query(ledger, '''SELECT epoch FROM note_write_grants
                WHERE conversation_id=? AND bank_id=? AND enabled=1''',
                (snapshot.conversation_id, snapshot.bank_id)).fetchone()
            if row is None:
                raise Failure('note_forbidden', 'No active explicit note-write grant.')
            self._origin(snapshot, turn_id, ledger)
            return NoteScope(snapshot, turn_id, row[0])

    def _origin(self, snapshot, turn_id, ledger):
        row = self._query(ledger, '''SELECT t.user_id,a.sha256,a.bytes FROM turns t
            JOIN artifacts a ON a.id=t.user_id WHERE t.id=? AND t.conversation_id=?
            AND t.status='running' AND a.kind='user_message' ''',
            (turn_id, snapshot.conversation_id)).fetchone()
        if row is None or not identity(row['user_id']):
            raise Failure('note_origin_invalid', 'The origin is not this conversation\'s running turn.')
        # Bind to the exact existing source, not a model-authored provenance label.
        self.store._history.body(row['user_id'], 'user_message', ledger, 4 * ledger.profile.context_limit)
        return row

    def scope_job(self, snapshot, run_id, ledger):
        """Genuine private-review origin; never a surrogate foreground turn."""
        if not identity(run_id):
            raise Failure('note_origin_invalid', 'An exact background run is required.')
        with self._transaction(ledger):
            self._authority(snapshot, ledger)
            if snapshot.job_run_id is not None:
                if snapshot.job_run_id != run_id:
                    raise Failure('note_forbidden', 'A review cannot borrow another run notebook authority.')
                write_epoch = snapshot.grant_epoch
                self._authority(snapshot, ledger, write_epoch=write_epoch)
            else:
                row = self._query(ledger, '''SELECT epoch FROM note_write_grants
                    WHERE conversation_id=? AND bank_id=? AND enabled=1''',
                    (snapshot.conversation_id, snapshot.bank_id)).fetchone()
                if row is None:
                    raise Failure('note_forbidden', 'No active explicit note-write grant.')
                write_epoch = row[0]
            self._job_origin(snapshot, run_id, ledger)
            return NoteScope(snapshot, None, write_epoch, run_id)

    def _job_origin(self, snapshot, run_id, ledger):
        definition = self.store.jobs.check_run(run_id)
        if (definition.kind not in REVIEW_KINDS or not definition.permissions.write_notes
                or definition.conversation_id != snapshot.conversation_id
                or snapshot.job_run_id is not None and snapshot.job_run_id != run_id):
            raise Failure('note_forbidden', 'Only an explicitly permitted private review can author background notes.')
        limits = ledger.execution_limits
        if (limits is None or ledger.profile != definition.profile or not ledger.notes_write
                or not 0 < ledger.max_operations <= definition.limits.operations
                or limits.calls > definition.limits.calls or limits.seconds > definition.limits.seconds):
            raise Failure('note_forbidden', 'Review writes require the saved job profile and bounded shared procedure ledger.')
        events = self._query(ledger, "SELECT sequence FROM background_run_events WHERE run_id=? AND kind='inputs' LIMIT 2",
                             (run_id,)).fetchall()
        if len(events) != 1:
            raise Failure('note_origin_invalid', 'A review requires exactly one retained source-input record.')
        body = self._job_origin_body(run_id, events[0][0], ledger)
        try:
            inputs = json.loads(body)
            if snapshot.job_run_id is not None:
                grant = self.store.jobs.notebook_grant(run_id)
                latest = None
            else:
                grant = dict(self._query(ledger, 'SELECT bank_id,enabled,epoch FROM memory_grants WHERE conversation_id=?',
                                        (snapshot.conversation_id,)).fetchone())
                latest = self._query(ledger, 'SELECT max(sequence) FROM turns WHERE conversation_id=?',
                                     (snapshot.conversation_id,)).fetchone()[0]
            if (inputs['conversation']['conversation_id'] != snapshot.conversation_id
                    or inputs['grant'] != grant or inputs['latest_sequence'] != latest):
                raise ValueError
            # Exact selected originals remain authoritative. A completed new
            # turn or source edit cannot borrow an earlier review's origin.
            for exchange in inputs['conversation']['selected_complete_exchanges']:
                for role in ('user', 'answer'):
                    source = exchange[role]
                    if self.store.jobs._source_body(source['artifact_id'], ledger) != source:
                        raise ValueError
        except (ValueError, KeyError, TypeError):
            raise Failure('note_origin_invalid', 'Review originals or source permissions changed.') from None
        return dict(user_id=None, sha256=hashlib.sha256(body.encode()).hexdigest(),
                    run_id=run_id, event_sequence=events[0][0], procedure_kind=definition.kind)

    def _job_origin_body(self, run_id, sequence, ledger):
        size = self._query(ledger, '''SELECT length(CAST(payload AS BLOB)) FROM background_run_events
            WHERE run_id=? AND sequence=? AND kind='inputs' ''', (run_id, sequence)).fetchone()
        if size is None:
            raise Failure('memory_source_unavailable', 'The original review input is unavailable.')
        if not 0 < size[0] <= min(524288, 4 * ledger.profile.context_limit):
            raise Failure('note_origin_size', 'Review origin exceeds the bounded exact-source allowance.')
        ledger.spend_payload(size[0])
        return self._query(ledger, 'SELECT payload FROM background_run_events WHERE sequence=?', (sequence,)).fetchone()[0]

    def _scope_origin(self, scope, ledger):
        if scope.job_run_id is not None and scope.turn_id is None:
            return self._job_origin(scope.memory, scope.job_run_id, ledger)
        if scope.job_run_id is None and identity(scope.turn_id):
            return self._origin(scope.memory, scope.turn_id, ledger)
        raise Failure('note_origin_invalid', 'Choose one exact foreground or background origin.')

    def inspect_run(self, snapshot, run_id, ledger, *, limit=20, after=''):
        """Body-free durable outcomes, including after failure or interruption."""
        if not identity(run_id) or type(limit) is not int or not 1 <= limit <= 50 or not isinstance(after, str) or len(after) > 64:
            raise ValueError('invalid review outcome page')
        with self._transaction(ledger):
            self._authority(snapshot, ledger)
            run = self.store.jobs.run(run_id)
            if self.store.jobs.definition(run['job_id'], run['revision']).conversation_id != snapshot.conversation_id:
                raise Failure('note_forbidden', 'Review is outside this inspection scope.')
            rows = self._query(ledger, '''SELECT operation_key FROM note_job_origins
                WHERE bank_id=? AND run_id=? AND operation_key>? ORDER BY operation_key LIMIT ?''',
                (snapshot.bank_id, run_id, after, limit + 1)).fetchall()
            return dict(outcomes=[self._receipt(snapshot, row[0], ledger)[1] for row in rows[:limit]],
                        next_cursor=rows[limit-1][0] if len(rows) > limit else None)

    def _fit(self, outcome, maximum, budget):
        if (type(maximum) is not int or maximum < 0
                or len(encoded(outcome).encode()) > min(maximum, self.policy.outcome_bytes)
                or budget is not None and not budget.fits(outcome)):
            raise Failure('note_result_capacity', 'The complete mutation receipt does not fit; no new mutation committed.')

    def _receipt(self, snapshot, key, ledger):
        if self._query(ledger, 'SELECT 1 FROM removed_note_operations WHERE bank_id=? AND key_sha256=?',
                       (snapshot.bank_id, key_digest(key))).fetchone():
            # Removing ordinary receipts must not make an old create operation
            # eligible to run again with fresh identities after a lost answer.
            raise Failure('note_operation_removed', 'This operation is no longer available; no operation was repeated.')
        row = self._query(ledger, '''SELECT intent_sha256,length(CAST(outcome AS BLOB)) AS bytes,typeof(outcome) AS kind,
            outcome_sha256 FROM note_outcomes WHERE bank_id=? AND actor=? AND operation_key=?''',
            (snapshot.bank_id, ACTOR, key)).fetchone()
        if row is None:
            orphan = self._query(ledger, '''SELECT 1 FROM note_revisions
                WHERE bank_id=? AND actor=? AND operation_key=? LIMIT 1''', (snapshot.bank_id, ACTOR, key)).fetchone()
            if orphan:
                raise Failure('note_outcome_unknown', 'A retained revision has lost its outcome record.')
            return None
        if (row['kind'] != 'text' or type(row['bytes']) is not int or not 0 < row['bytes'] <= self.policy.outcome_bytes
                or not isinstance(row['intent_sha256'], str) or len(row['intent_sha256']) != 64
                or any(c not in '0123456789abcdef' for c in row['intent_sha256'])):
            raise Failure('note_outcome_unknown', 'The retained outcome envelope is invalid.')
        ledger.spend_payload(row['bytes'])
        body = self._query(ledger, 'SELECT outcome FROM note_outcomes WHERE bank_id=? AND actor=? AND operation_key=?',
                           (snapshot.bank_id, ACTOR, key)).fetchone()[0]
        try:
            outcome = json.loads(body)
            if (hashlib.sha256(body.encode()).hexdigest() != row['outcome_sha256']
                    or not isinstance(outcome, dict) or outcome.get('operation_key') != key
                    or outcome.get('status') not in ('committed', 'unchanged', 'rejected')):
                raise ValueError('corrupt receipt')
        except (ValueError, TypeError, UnicodeError):
            raise Failure('note_outcome_unknown', 'The retained outcome is corrupt; success cannot be established.') from None
        if outcome['status'] in ('committed', 'unchanged'):
            if not identity(outcome.get('note_id')) or not identity(outcome.get('revision_id')):
                raise Failure('note_outcome_unknown', 'The retained outcome identity is invalid.')
            # Bank permission alone must not disclose a now-ineligible note.
            selected = self.memory._revision(snapshot, outcome['note_id'], outcome['revision_id'], ledger)
            meta = self.memory._meta(snapshot, selected['payload_id'], 'memory', ledger)
            if selected['owned_note'] is None or meta['sha256'] != outcome.get('sha256'):
                raise Failure('note_outcome_unknown', 'The retained outcome no longer matches its revision.')
        return row['intent_sha256'], outcome

    def outcome(self, snapshot, call, ledger, maximum, *, budget=None):
        if call.name != 'note_outcome':
            raise Failure('invalid_tool_arguments', 'Expected an outcome lookup.')
        args = note_arguments(call, self.policy)
        with self._transaction(ledger):
            self._authority(snapshot, ledger)  # Read authority, not write authority.
            found = self._receipt(snapshot, args['operation_key'], ledger)
            result = found[1] if found else {'status': 'not_recorded', 'operation_key': args['operation_key']}
            self._fit(result, maximum, budget)
            return result

    def inspect_turn(self, snapshot, turn_id, ledger, *, limit):
        """Explicit CLI inspection: bounded metadata only, never note bodies."""
        if not identity(turn_id) or type(limit) is not int or not 1 <= limit <= self.policy.outcomes:
            raise ValueError('invalid note inspection bound')
        with self._transaction(ledger):
            self._authority(snapshot, ledger)
            owner = self._query(ledger, 'SELECT conversation_id FROM turns WHERE id=?', (turn_id,)).fetchone()
            if owner is None or owner[0] != snapshot.conversation_id:
                raise Failure('note_forbidden', 'Turn is outside this inspection scope.')
            rows = self._query(ledger, '''SELECT operation_key FROM note_outcomes
                WHERE bank_id=? AND actor=? AND turn_id=? ORDER BY operation_key LIMIT ?''',
                (snapshot.bank_id, ACTOR, turn_id, limit + 1)).fetchall()
            keys = {row[0] for row in rows}
            attempts = self._query(ledger, '''SELECT ordinal,length(CAST(arguments AS BLOB)) AS bytes
                FROM history_operations WHERE turn_id=? AND name='write_note'
                ORDER BY ordinal LIMIT ?''', (turn_id, limit + 1)).fetchall()
            for attempt in attempts[:limit]:
                if not 0 < attempt['bytes'] <= self.policy.argument_bytes:
                    raise Failure('note_outcome_unknown', 'Mutation delivery envelope is invalid.')
                ledger.spend_payload(attempt['bytes'])
                try:
                    arguments = self._query(ledger, 'SELECT arguments FROM history_operations WHERE turn_id=? AND ordinal=?',
                                            (turn_id, attempt['ordinal'])).fetchone()[0]
                    envelope = json.loads(arguments)
                    if (set(envelope) == {'preflight_rejected', 'arguments_sha256'}
                            and envelope['preflight_rejected'] is True
                            and isinstance(envelope['arguments_sha256'], str)
                            and len(envelope['arguments_sha256']) == 64
                            and all(c in '0123456789abcdef' for c in envelope['arguments_sha256'])):
                        # This call was never admitted as a mutation. Its
                        # pairing lives in operation receipts, not note outcomes.
                        # Actual committed outcomes were collected above.
                        continue
                    key = envelope['operation_key']
                    if not isinstance(key, str) or len(key) > self.policy.key_bytes:
                        raise ValueError('invalid receipt key')
                    keys.add(key)
                except (ValueError, TypeError, KeyError):
                    raise Failure('note_outcome_unknown', 'Mutation delivery identity is invalid.') from None
            more = len(keys) > limit or len(attempts) > limit
            result = {'status': 'page' if more else 'complete', 'outcomes': []}
            for key in sorted(keys)[:limit]:
                found = self._receipt(snapshot, key, ledger)
                if found is None:
                    if key in {row[0] for row in rows}:
                        raise Failure('note_outcome_unknown', 'An indexed outcome is missing.')
                    result['outcomes'].append({'status': 'not_recorded', 'operation_key': key})
                else:
                    result['outcomes'].append(found[1])
            if more:
                result['reason'] = 'inspection_limit_use_operation_key_lookup'
            return result

    def history_actions(self, snapshot, turn_id, ledger):
        """Body-free historical evidence, not a new operation or note read.

        Derive from retained authoritative rows on each admission: removal can
        delete receipts without a cached history copy resurrecting their IDs.
        Never turn a missing/withheld outcome into a claim that no save happened.
        """
        # This validates the shared historical operation log, not just note
        # operations or currently granted tools. Task use must remain a valid
        # identity on later turns (including after its grant is disabled).
        # Web actions remain valid history even after web access is switched off.
        # Task effects/read authority still belong to the task owner; they do
        # not become note outcomes merely by passing this fixed-name check.
        # Optional workspace tools remain valid history after access is disabled.
        # Their effects belong to workspace receipts, never note outcomes.
        # Earlier work keeps its admission-time bounds when current settings change.
        limits = self.store.execution.for_turn(turn_id)
        names = {'write_note', 'note_outcome', 'search_memory', 'read_memory',
                 'search_history', 'read_history', 'read_reference', 'personal_tasks', 'research_tasks', 'reminders', 'people', 'web_search', 'web_read', 'mcp_tools', 'mcp_call', 'mcp_read','list_skills','read_skill','skill_files',
                 'university', 'media'}
        deliveries = {'started', 'prepared', 'submitted', 'failed', 'interrupted'}
        statuses = {'complete', 'page', 'not_delivered', 'not_executed', 'committed',
                    'unchanged', 'rejected', 'unknown', 'not_recorded', 'delivery_unknown', 'preview'}
        with self._transaction(ledger):
            self._authority(snapshot, ledger)
            turn = self._query(ledger, '''SELECT status,calls FROM turns
                WHERE id=? AND conversation_id=?''', (turn_id, snapshot.conversation_id)).fetchone()
            if turn is None or turn['status'] == 'running':
                raise Failure('note_forbidden', 'Historical actions require an earlier terminal turn in this conversation.')
            attempts = self._query(ledger, '''SELECT state,finish_reason FROM model_attempts
                WHERE turn_id=? ORDER BY ordinal LIMIT ?''', (turn_id, limits.calls + 1)).fetchall()
            rows = self._query(ledger, '''SELECT ordinal,name,state,
                CASE WHEN json_valid(result) THEN json_extract(result,'$.status') END AS result_status
                FROM history_operations WHERE turn_id=? ORDER BY ordinal LIMIT ?''',
                (turn_id, limits.operations + 1)).fetchall()
            if len(attempts) > limits.calls or len(rows) > limits.operations:
                raise Failure('note_outcome_unknown', 'Historical action metadata exceeds its saved execution bound.')
            operations = []
            for row in rows:
                if (row['name'] not in names or row['state'] not in deliveries
                        or type(row['ordinal']) is not int or not 1 <= row['ordinal'] <= limits.operations):
                    raise Failure('note_outcome_unknown', 'Historical operation identity is invalid.')
                # Only fixed application vocabulary enters the system record;
                # queries, arguments, diagnostics and arbitrary result text do not.
                status = row['result_status'] if row['result_status'] in statuses else 'unknown'
                operations.append(dict(ordinal=row['ordinal'], tool=row['name'],
                    delivery=row['state'], result=status))
            requested = ('recorded' if any(a['finish_reason'] == 'tool_calls' for a in attempts)
                else 'none' if attempts and len(attempts) == turn['calls'] and all(
                    a['state'] == 'received' and a['finish_reason'] in ('stop', 'length') for a in attempts)
                and not rows else 'unknown')
        try:
            # Reuse the existing read grant, receipt hash/identity and revision
            # eligibility checks. Do not create a new ledger or trust delivery
            # status as proof of a commit (or of failure after a commit).
            inspected = self.inspect_turn(snapshot, turn_id, ledger, limit=limits.operations)
        except Failure as error:
            if error.code in {'history_work_limit', 'timeout', 'memory_source_changed'}:
                raise
            inspected = {'status': 'unavailable', 'outcomes': []}
        # Group by the fixed disposition instead of repeating framing/status
        # per item. Admission may explicitly choose verified counts without IDs
        # if the complete identity projection cannot fit the shared ledger.
        result = {}
        for receipt in inspected['outcomes']:
            state = receipt['status']
            if state in ('committed', 'unchanged'):
                result.setdefault(state, []).append(dict(note_id=receipt['note_id'], revision_id=receipt['revision_id']))
            else:
                key = 'unknown' if state == 'not_recorded' else state
                result[key] = result.get(key, 0) + 1
        # Permission/index epoch is rechecked after both bounded reads; the turn
        # checks this same snapshot again immediately before provider dispatch.
        self.memory.check(snapshot, ledger)
        if not result:
            # No retained receipt is not evidence of no action: only a settled
            # no-tools provider history permits that positive assertion.
            result['tool_requests'] = requested
        if inspected['status'] != 'complete':
            result['note_outcomes_status'] = inspected['status']
        refused = sum(o['result'] == 'not_executed' for o in operations if o['tool'] == 'write_note')
        if refused:
            result['note_saves_not_executed'] = refused
        return result

    def _provenance_row(self, snapshot, row, ledger):
        # Check variable metadata length before materializing it. This is source
        # provenance, not authority over another record or a claim of truth.
        meta = self._query(ledger, '''SELECT n.*,t.conversation_id,o.run_id,o.event_sequence FROM note_revisions n
            LEFT JOIN turns t ON t.id=n.turn_id
            LEFT JOIN note_job_origins o ON o.bank_id=n.bank_id AND o.actor=n.actor AND o.operation_key=n.operation_key
            WHERE n.revision_id=? AND n.bank_id=?
            AND length(CAST(n.reason AS BLOB))<=?''',
            (row['id'], snapshot.bank_id, self.policy.reason_bytes)).fetchone()
        if meta is None or row['owned_note'] is None:
            raise Failure('memory_integrity', 'Authored-note provenance is missing or invalid.')
        meta = dict(meta)
        if self._query(ledger, 'SELECT 1 FROM detached_note_origins WHERE revision_id=?', (row['id'],)).fetchone():
            # The independent note survives. No replacement origin is invented.
            meta['detached'] = True
            return meta
        if meta['turn_id'] is None:
            if meta['user_id'] is not None or not identity(meta['run_id']):
                raise Failure('memory_integrity', 'Background note provenance is incomplete.')
            run = self.store.jobs.run(meta['run_id'])
            definition = self.store.jobs.definition(run['job_id'], run['revision'])
            if definition.kind not in REVIEW_KINDS:
                raise Failure('memory_integrity', 'Background note origin is not a private review.')
            meta['conversation_id'] = definition.conversation_id
            meta['job_kind'] = definition.kind
        elif meta['run_id'] is not None or not identity(meta['user_id']) or meta['conversation_id'] is None:
            raise Failure('memory_integrity', 'Foreground note provenance is inconsistent.')
        ledger.spend_payload(len(meta['reason'].encode()))
        return meta

    def origin_page(self, snapshot, row, offset, maximum, ledger):
        meta = self._provenance_row(snapshot, row, ledger)
        if meta.get('detached'):
            raise Failure('memory_source_unavailable', 'The original is unavailable.')
        if meta['run_id'] is not None:
            # Review inputs may contain more than one exchange. They require
            # that origin conversation's current read grant, not just the note's
            # independent lifetime. Losing this support never deletes the note.
            if meta['conversation_id'] is not None and not self._query(ledger, '''SELECT 1 FROM memory_grants
                    WHERE conversation_id=? AND bank_id=? AND enabled=1''',
                    (meta['conversation_id'], snapshot.bank_id)).fetchone():
                raise Failure('memory_source_unavailable', 'Review input access has been revoked.')
            body = self._job_origin_body(meta['run_id'], meta['event_sequence'], ledger)
            if hashlib.sha256(body.encode()).hexdigest() != meta['basis_sha256']:
                raise Failure('memory_integrity', 'The exact review-input digest changed.')
            result = exact_page(meta['run_id'] + ':' + str(meta['event_sequence']), body, offset, maximum).envelope()
            result.update(kind='source', source_kind='background_review_inputs', record_id=row['record_id'],
                          revision_id=row['id'], run_id=meta['run_id'])
            return result
        # Reuse the exact archive payload/integrity owner. The note grants only
        # this linked original, never arbitrary other conversation artifacts.
        origin = self._query(ledger, 'SELECT user_id FROM turns WHERE id=?', (meta['turn_id'],)).fetchone()
        if origin is None or origin[0] != meta['user_id']:
            raise Failure('memory_integrity', 'Note origin no longer matches its exact user source.')
        size = self._query(ledger, 'SELECT bytes FROM artifacts WHERE id=?', (meta['user_id'],)).fetchone()
        if size is not None and size[0] > 4 * ledger.profile.context_limit:
            raise Failure('note_origin_size', 'Origin exceeds this bounded archive-read allowance; no source body was delivered.')
        body = self.store._history.body(meta['user_id'], 'user_message', ledger, 4 * ledger.profile.context_limit)
        if hashlib.sha256(body.encode()).hexdigest() != meta['basis_sha256']:
            raise Failure('memory_integrity', 'Note origin digest changed.')
        result = exact_page(meta['user_id'], body, offset, maximum).envelope()
        result.update(kind='source', source_kind='note_origin', record_id=row['record_id'],
                      revision_id=row['id'], turn_id=meta['turn_id'])
        return result

    def provenance(self, snapshot, row, mode, key, offset, ledger, maximum):
        meta = self._provenance_row(snapshot, row, ledger)
        entries = [{'edge_id': 1, 'relation': 'authored_from_turn', 'author': ACTOR,
                    'reason': meta['reason'], 'turn_id': meta['turn_id'],
                    'conversation_id': meta['conversation_id'], 'artifact_id': meta['user_id'],
                    'sha256': meta['basis_sha256'], 'parent_revision': meta['parent_revision'],
                    'read_ref': self.memory._reference(snapshot, key, 'n', mode, row)}]
        if meta.get('detached'):
            entries = []
        if meta['run_id'] is not None:
            entries[0].pop('turn_id')
            entries[0].pop('artifact_id')
            entries[0].update(relation='maintained_during_notebook_review' if meta['job_kind'] == 'notebook_maintenance'
                              else 'authored_during_idle_review', run_id=meta['run_id'],
                              input_event=meta['event_sequence'])
        bases = self._query(ledger, 'SELECT * FROM note_bases WHERE revision_id=? ORDER BY ordinal LIMIT ?',
                            (row['id'], self.policy.basis_refs + 1)).fetchall()
        if len(bases) > self.policy.basis_refs:
            raise Failure('memory_integrity', 'Note basis exceeds its bounded envelope.')
        for ordinal, basis in enumerate(bases):
            if basis['ordinal'] != ordinal:
                raise Failure('memory_integrity', 'Note basis ordering is invalid.')
            if self._query(ledger, "SELECT 1 FROM removed_note_ids WHERE kind='record' AND id=?",
                           (basis['record_id'],)).fetchone():
                # Independent authorship survives removal of its basis. Keep
                # internal ordering, but project no hole, placeholder or bridge.
                continue
            try:
                selected = self.memory._revision(snapshot, basis['record_id'], basis['memory_revision'], ledger)
                if basis['kind'] == 's':
                    source = self.memory._source(snapshot, selected, basis['source_revision'], ledger)
                    digest = self.memory._meta(snapshot, source['payload_id'], 'source', ledger)['sha256']
                elif basis['kind'] == 'n':
                    digest = self._provenance_row(snapshot, selected, ledger)['basis_sha256']
                else:
                    digest = self.memory._meta(snapshot, selected['payload_id'], 'memory', ledger)['sha256']
                if digest != basis['sha256']:
                    raise Failure('memory_integrity', 'Note basis digest changed.')
                entry = {'edge_id': ordinal + 2, 'relation': 'deliberate_basis',
                         'read_ref': self.memory._reference(snapshot, key, basis['kind'], 'historical', selected, basis['source_revision'])}
                if basis['kind']=='m':
                    current=self.memory._resolve(snapshot,selected,ledger)
                    if current['id']!=selected['id']:
                        entry.update(scope='context_dependency',source_changed=True,
                            current_ref=self.memory._reference(snapshot,key,'m','current',current))
            except Failure as error:
                if error.code not in ('memory_unavailable', 'memory_source_unavailable'):
                    raise
                entry = {'edge_id': ordinal + 2, 'status': 'support_unavailable'}
            entries.append(entry)
        remaining = [entry for entry in entries if entry['edge_id'] > offset]
        result = {'status': 'complete', 'kind': 'provenance', 'record_id': row['record_id'],
                  'revision_id': row['id'], 'offset_unit': 'edge_id', 'offset': offset,
                  'results': [], 'delivered_count': 0, 'next_offset': None}
        for index, entry in enumerate(remaining):
            more = index + 1 < len(remaining)
            candidate = dict(result, results=result['results'] + [entry],
                             delivered_count=len(result['results']) + 1, status='page' if more else 'complete',
                             next_offset=entry['edge_id'] if more else None)
            if len(encoded(candidate).encode()) > maximum:
                result.update(status='page' if result['results'] else 'not_delivered', reason='result_capacity',
                              next_offset=result['results'][-1]['edge_id'] if result['results'] else offset)
                break
            result = candidate
        while result['results'] and len(encoded(result).encode()) > maximum:
            result['results'].pop()
            result.update(status='page' if result['results'] else 'not_delivered', reason='result_capacity',
                          delivered_count=len(result['results']),
                          next_offset=result['results'][-1]['edge_id'] if result['results'] else offset)
        return result

    def _basis(self, snapshot, refs, bank, ledger):
        result = []
        for ref in refs:
            value = self.memory._decode(snapshot, bank['handle_key'], ref)
            if (not isinstance(value, list) or len(value) != 5 or value[0] not in ('m', 's', 'n')
                    or value[1] not in ('current', 'historical') or not all(identity(v) for v in value[2:4])
                    or value[0] in ('m', 'n') and value[4] is not None
                    or value[0] == 's' and not identity(value[4])):
                raise Failure('note_basis_invalid', 'Basis requires an authorized exact memory/source revision.')
            kind, mode, rid, revision, source = value
            row = self.memory._revision(snapshot, rid, revision, ledger)
            current = self.memory._resolve(snapshot, row, ledger)
            if mode == 'current' and (current['record_id'], current['id']) != (rid, revision):
                raise Failure('note_basis_invalid', 'Current basis changed; rediscover it.')
            if kind == 'n':
                meta = self.origin_page(snapshot, row, 0, 0, ledger)
            else:
                selected = self.memory._source(snapshot, row, source, ledger) if kind == 's' else row
                meta = self.memory._meta(snapshot, selected['payload_id'], 'source' if kind == 's' else 'memory', ledger)
            result.append((kind, rid, revision, source, meta['sha256']))
        return result

    def _cited(self, snapshot, ref, bank, ledger):
        """One of the user's own earlier messages, as quote evidence.

        Only a current conversation reference from this archive epoch, to a
        user-role message in a conversation granted to this bank. The exact
        body is read here; the quote is matched against it by the caller.
        """
        value = self.memory._decode(snapshot, bank['handle_key'], ref)
        if (not isinstance(value, list) or len(value) != 5 or value[0] != 'h'
                or value[1] not in ('current', 'historical') or value[4] != snapshot.archive_epoch):
            raise Failure('note_quote_invalid', 'source_ref must be a current reference to one of the user\'s own messages; rediscover it.')
        source = self.store.recall._source(snapshot, 'message', value[3], ledger)
        if not source or source['conversation_id'] != value[2] or source['role'] != 'user':
            raise Failure('note_quote_invalid', 'source_ref must name one of the user\'s own messages in a granted conversation.')
        body = self.store._history.body(value[3], 'user_message', ledger, 4 * ledger.profile.context_limit)
        return dict(artifact_id=value[3], body=body)

    def _target(self, snapshot, args, ledger):
        owned = self._query(ledger, '''SELECT 1 FROM note_records
            WHERE record_id=? AND bank_id=? AND actor=?''', (args['note_id'], snapshot.bank_id, ACTOR)).fetchone()
        if owned is None:
            raise Failure('note_target_unavailable', 'Target is not an owned note in this bank.')
        row = self.memory._head(snapshot, args['note_id'], ledger)
        resolved = self.memory._resolve(snapshot, row, ledger)
        if row['validity'] != 'current' or resolved['id'] != row['id']:
            raise Failure('note_target_unavailable', 'Target is not an eligible independent note.')
        if row['id'] != args['expected_revision']:
            raise Failure('note_revision_conflict', 'The expected revision is no longer current.')
        meta = self.memory._meta(snapshot, row['payload_id'], 'memory', ledger)
        if meta['bytes'] > self.policy.revision_bytes:
            raise Failure('note_quota', 'Existing note exceeds the revision allowance.')
        raw = self.memory._bytes(meta, 0, meta['bytes'], ledger)
        if hashlib.sha256(raw).hexdigest() != meta['sha256']:
            raise Failure('memory_integrity', 'Exact note digest differs from its envelope.')
        try:
            raw.decode('utf-8')
        except UnicodeError:
            raise Failure('memory_integrity', 'Existing note is not valid UTF-8.') from None
        return raw, meta

    def _quota(self, snapshot, size, create, ledger):
        count, retained = self._query(ledger, '''SELECT count(*),coalesce(sum(p.bytes),0)
            FROM note_revisions n JOIN memory_revisions v ON v.id=n.revision_id
            JOIN memory_payloads p ON p.id=v.payload_id WHERE n.bank_id=?''', (snapshot.bank_id,)).fetchone()
        notes = self._query(ledger, 'SELECT count(*) FROM note_records WHERE bank_id=?', (snapshot.bank_id,)).fetchone()[0]
        if count >= self.policy.revisions or retained + size > self.policy.retained_bytes or create and notes >= self.policy.notes:
            raise Failure('note_quota', 'Retained note allowance exhausted; reads remain available.')

    def write(self, scope, call, ledger, maximum, *, budget=None):
        if call.name != 'write_note':
            raise Failure('invalid_tool_arguments', 'Expected a note mutation.')
        args = note_arguments(call, self.policy)
        if 'operation' not in args:
            raise Failure('invalid_tool_arguments', 'An authored save requires application-bound read context.')
        return self._write(scope, call, args, ledger, maximum, budget=budget)

    def save(self, scope, call, target, ledger, maximum, *, budget=None):
        args = note_arguments(call, self.policy)
        metadata = 'text' not in args
        if call.name != 'write_note' or 'operation' in args or not metadata and ('read_ref' in args) != (target is not None):
            raise Failure('invalid_tool_arguments', 'Authored save target does not match its read context.')
        if target is not None and (not isinstance(target, tuple) or len(target) != 2 or not all(identity(v) for v in target)):
            raise Failure('invalid_tool_arguments', 'Authored save requires an exact application-owned target.')
        # No model-supplied revision, actor, key or fabricated verification basis.
        # The current origin and exact parent remain durable provenance, not truth.
        internal = dict(operation='metadata' if metadata else 'create' if target is None else 'revise',
            operation_key=operation_key(scope.turn_id, args, job_run_id=scope.job_run_id), reason='Deliberate authored note',
            basis_refs=args.get('basis_refs',[]), fields=args,
            inherit_basis='basis_refs' not in args)
        if metadata:
            internal.update(read_ref=args['read_ref'],reason='Metadata update; exact body retained')
        else:
            internal['text']=args['text']
        if target is not None:
            internal.update(note_id=target[0], expected_revision=target[1])
        return self._write(scope, call, internal, ledger, maximum, budget=budget, digest=intent_digest(args))

    def _write(self, scope, call, args, ledger, maximum, *, budget=None, digest=None):
        snapshot, key = scope.memory, args['operation_key']
        # Delivery and mutation receipts identify the SAME canonical request,
        # not two different hashes of public and application-expanded arguments.
        digest = intent_digest(args) if digest is None else digest
        with self._transaction(ledger, write=True):
            bank = self._authority(snapshot, ledger, write_epoch=scope.write_epoch)
            # Job authority is checked even for a replay through the write API.
            # Read-only outcome lookup remains available after cancellation.
            if scope.job_run_id is not None:
                self._scope_origin(scope, ledger)
            found = self._receipt(snapshot, key, ledger)
            if found is not None:
                result = found[1] if found[0] == digest else {'status': 'rejected', 'operation_key': key, 'reason': 'note_key_conflict'}
                self._fit(result, maximum, budget)
                return NoteResult(result, snapshot)
            origin = self._scope_origin(scope, ledger)
            if scope.job_run_id is not None:
                count = self._query(ledger, 'SELECT count(*) FROM note_job_origins WHERE run_id=?',
                                     (scope.job_run_id,)).fetchone()[0]
                if count >= ledger.max_operations:
                    raise Failure('job_operation_limit', 'No new note mutation fits the saved review-operation limit.')
            count = self._query(ledger, 'SELECT count(*) FROM note_outcomes WHERE bank_id=?', (snapshot.bank_id,)).fetchone()[0]
            if count >= self.policy.outcomes:
                raise Failure('note_quota', 'No mutation outcome slot remains; intent was not admitted.')
            if bank['index_epoch'] != snapshot.epoch or not 0 <= snapshot.epoch < 2**63 - 512:
                raise Failure('note_index_stale', 'No write may certify an already stale discovery index.')
            if scope.job_run_id is not None and self.store.jobs.check_run(scope.job_run_id).kind == 'notebook_maintenance':
                # No new note, legacy patch or deletion path may hide behind maintenance.
                # Full-read admission belongs to NoteSession; this durable owner
                # also rejects direct attempts to expand maintenance authority.
                supplied = note_arguments(call, self.policy)
                if 'flags' in supplied:
                    raise Failure('maintenance_flags_preserved', 'Maintenance preserves rejection and coverage flags.')
                if (args['operation'] not in ('revise', 'metadata') or 'operation' in supplied
                        or not supplied.get('read_ref')):
                    raise Failure('maintenance_existing_note_required', 'Maintenance can only revise an existing note.')
            create = args['operation'] == 'create'
            rid, revision = (uuid.uuid4().hex if create else args.get('note_id','0'*32)), uuid.uuid4().hex
            result = {'status': 'committed', 'operation_key': key, 'note_id': rid,
                      'revision_id': revision, 'sha256': '0' * 64, 'bank_epoch': 2**63 - 1,
                      'read_ref': 'R1.' + 'A' * 66}
            # Reserve the worst success envelope before any note/index/outcome effect.
            # Recheck the actual result before COMMIT; no post-effect truncation.
            self._fit(result, maximum, budget)
            try:
                if args['operation']=='metadata':
                    # Resolve under the same write transaction, AFTER receipt
                    # lookup so replay does not need a now-stale prewrite handle.
                    value=self.memory._decode(snapshot,bank['handle_key'],args['read_ref'])
                    if (not isinstance(value,list) or len(value)!=5 or value[0] not in ('m','d')
                            or value[1]!='current' or not all(identity(v) for v in value[2:4])
                            or value[4] is not None):
                        raise Failure('note_target_unavailable','Metadata needs an exact current note reference, not a historical/source reference.')
                    rid=value[2]
                    args=dict(args,note_id=rid,expected_revision=value[3])
                    result['note_id']=rid
                bases = self._basis(snapshot, args['basis_refs'], bank, ledger)
                if args.get('fields',{}).get('source_ref'):
                    args = dict(args, cited=self._cited(snapshot, args['fields']['source_ref'], bank, ledger))
                previous, meta = (b'', None) if create else self._target(snapshot, args, ledger)
                previous_bases=[] if create else [tuple(r) for r in self._query(ledger,
                    'SELECT kind,record_id,memory_revision,source_revision,sha256 FROM note_bases WHERE revision_id=? ORDER BY ordinal',
                    (args['expected_revision'],)).fetchall()]
                if not create and args.get('inherit_basis'):
                    bases=previous_bases
                fields,fields_changed=note_metadata.prepare(self,snapshot,args,origin,ledger)
                metadata_changed=fields_changed or bases!=previous_bases
                noop = (not create and (args['operation'] == 'metadata'
                        or args['operation'] == 'append' and args['text'] == ''
                        or args['operation'] == 'revise' and args['text'].encode() == previous
                        or args['operation'] == 'replace' and args['expected_text'] == args['replacement']))
                noop = noop and not metadata_changed
                size = (len(previous) if args['operation']=='metadata' else
                        len(args['text'].encode()) if args['operation'] == 'revise' else
                        len(previous) + len(args['text'].encode()) if args['operation'] != 'replace'
                        else len(previous) - len(args['expected_text'].encode()) + len(args['replacement'].encode()))
                if size > self.policy.revision_bytes:
                    raise Failure('note_quota', 'The exact revision exceeds the development byte allowance.')
                if not noop:
                    self._quota(snapshot, max(0, size), create, ledger)
                # Charge bounded construction/UTF-8/index copies before allocation.
                ledger.spend_payload(4 * (len(previous) + len(call.arguments.encode())))
                raw = edited_bytes(previous, args, self.policy)
                if not create and raw == previous and not metadata_changed:
                    result.update(status='unchanged', revision_id=args['expected_revision'],
                                  sha256=meta['sha256'], bank_epoch=snapshot.epoch)
                else:
                    result['sha256'] = hashlib.sha256(raw).hexdigest()
            except Failure as error:
                if error.code not in ('note_span_conflict', 'note_whole_replace', 'note_quota', 'note_basis_invalid',
                                      'invalid_memory_reference', 'note_target_unavailable', 'note_revision_conflict',
                                      'memory_unavailable', 'memory_source_unavailable', 'note_origin_size',
                                      'note_fields_invalid', 'note_thread_status_invalid', 'note_quote_invalid'):
                    raise
                result = {'status': 'rejected', 'operation_key': key, 'reason': error.code}
            if result['status'] == 'committed':
                self._revision(snapshot, scope.turn_id, args, origin, rid, revision, raw, bases, ledger)
                note_metadata.save(self,snapshot,revision,fields,ledger)
                epoch = self._query(ledger, 'SELECT epoch FROM memory_banks WHERE id=?', (snapshot.bank_id,)).fetchone()[0]
                self._query(ledger, 'UPDATE memory_banks SET index_epoch=? WHERE id=?', (epoch, snapshot.bank_id))
                result['bank_epoch'] = epoch
                advanced = replace(snapshot, epoch=epoch)
            else:
                advanced = snapshot
            if result['status'] in ('committed', 'unchanged'):
                selected = self.memory._revision(advanced, rid, result['revision_id'], ledger)
                # Navigation to the exact own-commit version; no body duplication
                # and no authority refresh. Replayed receipts keep their original
                # handle, which may require rediscovery in a later view.
                result['read_ref'] = self.memory._reference(advanced, bank['handle_key'], 'm', 'current', selected)
            self._fit(result, maximum, budget)
            body = encoded(result)
            ledger.spend_payload(len(body.encode()))
            self._query(ledger, 'INSERT INTO note_outcomes VALUES(?,?,?,?,?,?,?)',
                        (snapshot.bank_id, ACTOR, key, digest, scope.turn_id, body, hashlib.sha256(body.encode()).hexdigest()))
            if scope.job_run_id is not None:
                self._query(ledger, 'INSERT INTO note_job_origins VALUES(?,?,?,?,?)',
                    (snapshot.bank_id, ACTOR, key, scope.job_run_id, origin['event_sequence']))
                self._scope_origin(replace(scope, memory=advanced), ledger)
            self._authority(advanced, ledger, write_epoch=scope.write_epoch)
            return NoteResult(result, advanced)

    def _revision(self, snapshot, turn_id, args, origin, rid, revision, raw, bases, ledger):
        payload, digest = uuid.uuid4().hex, hashlib.sha256(raw).hexdigest()
        q = lambda sql, values=(): self._query(ledger, sql, values)
        q('INSERT INTO memory_payloads VALUES(?,?,?,?,?)', (payload, snapshot.bank_id, 'memory', len(raw), digest))
        for position in range(0, len(raw), CHUNK_BYTES):
            chunk = raw[position:position + CHUNK_BYTES]
            q('INSERT INTO memory_chunks VALUES(?,?,?,?)', (payload, position // CHUNK_BYTES, hashlib.sha256(chunk).hexdigest(), chunk))
        if args['operation'] == 'create':
            q('INSERT INTO memory_records VALUES(?,?,?,?,1)', (rid, snapshot.bank_id, revision, 'current'))
            q('INSERT INTO note_records VALUES(?,?,?)', (rid, snapshot.bank_id, ACTOR))
        else:
            q('UPDATE memory_records SET head_revision=? WHERE id=?', (revision, rid))
        sequence = q('''INSERT INTO memory_revisions(id,record_id,payload_id,author,origin,created_at,readable)
            VALUES(?,?,?,?,?,?,1)''', (revision, rid, payload, ACTOR,
                'notebook_care_note' if turn_id is None and origin['procedure_kind'] == 'notebook_care'
                else 'notebook_maintenance_note' if turn_id is None and origin['procedure_kind'] == 'notebook_maintenance'
                else 'idle_review_note' if turn_id is None else 'deliberate_note', datetime.now(timezone.utc).isoformat())).lastrowid
        q('INSERT INTO note_revisions VALUES(?,?,?,?,?,?,?,?,?)', (revision, snapshot.bank_id, ACTOR,
            args['operation_key'], args.get('expected_revision'), turn_id, origin['user_id'], origin['sha256'], args['reason']))
        for ordinal, basis in enumerate(bases):
            q('INSERT INTO note_bases VALUES(?,?,?,?,?,?,?)', (revision, ordinal, *basis))
        q('INSERT INTO memory_index VALUES(?,?,?,?)', (revision, snapshot.bank_id, sequence, digest))
        q('INSERT INTO memory_fts(rowid,scope,body) VALUES(?,?,?)', (sequence, 'b' + snapshot.bank_id, raw.decode('utf-8')))
