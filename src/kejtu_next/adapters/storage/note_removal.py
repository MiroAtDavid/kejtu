"""Explicit, idle-owner whole-note removal; never a model operation.

This first development slice checks a bounded active store, not backups, external
copies or arbitrary paraphrases. Known whole-body copies and explicit copy links
refuse removal until their policy is settled. No semantic judgement is made.
"""
import base64
from ...core.execution import MAX_FOREGROUND_OPERATIONS
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import sqlite3
import time

from ...core.notes import identity
from ...core.records import Failure
from .history import encoded
from .notebook import view_token


REMOVAL_SCHEMA = '''
CREATE TABLE note_removals(operation_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
 bank_id TEXT NOT NULL, record_id TEXT NOT NULL UNIQUE, preview_token TEXT NOT NULL,
 removed_at TEXT NOT NULL);
CREATE TABLE removed_note_ids(kind TEXT NOT NULL, id TEXT NOT NULL,
 PRIMARY KEY(kind,id));
CREATE TABLE removed_note_operations(bank_id TEXT NOT NULL, key_sha256 TEXT NOT NULL,
 PRIMARY KEY(bank_id,key_sha256));
'''
for _table, _kind in (('memory_records', 'record'), ('memory_revisions', 'revision'),
                      ('memory_payloads', 'payload')):
    for _event in ('INSERT', 'UPDATE OF id'):
        REMOVAL_SCHEMA += f'''CREATE TRIGGER {_table}_removed_{_event.split()[0].lower()}
         BEFORE {_event} ON {_table}
         WHEN EXISTS(SELECT 1 FROM removed_note_ids WHERE kind='{_kind}' AND id=NEW.id)
         BEGIN SELECT RAISE(ABORT,'removed note identity'); END;
'''

# Maintenance limits bound the selected note closure and each streamed body.
# Archive scans share the finite SQL/deadline budget, without an aggregate row/byte
# ceiling. Nothing here runs on startup, search or ordinary turns.
MAX_ROWS = 12000
MAX_BYTES = 8 * 1024 * 1024
MAX_STEPS = 5_000_000
SECONDS = 5
TABLES = (
    'notebook_care_state','notebook_care_items','notebook_care_groups','notebook_care_retired','notebook_care_undo',
    'historical_note_imports', 'historical_notes', 'historical_note_revisions',
    'execution_settings', 'turn_execution', 'state_metadata', 'conversations', 'artifacts', 'artifact_payloads', 'artifact_chunks',
    'native_mutations', 'turns', 'model_attempts', 'reference_reads', 'provider_states',
    'history_units', 'history_sources', 'history_admissions', 'history_operations',
    'memory_banks', 'memory_grants', 'memory_payloads', 'memory_chunks', 'memory_records',
    'memory_revisions', 'memory_supersessions', 'memory_sources', 'memory_edges',
    'memory_index', 'note_write_grants', 'note_records', 'note_outcomes', 'note_revisions',
    'note_bases', 'note_metadata', 'note_job_origins', 'recall_segments', 'recall_vectors', 'recall_pending', 'recall_dirty', 'recall_epoch',
    'personal_tasks', 'personal_task_sources', 'personal_task_history',
    'personal_task_operations', 'personal_task_state',
    'background_jobs', 'background_job_versions', 'background_runs',
    'background_run_events', 'background_job_operations',
    'background_deliveries', 'background_delivery_operations',
    'reminders', 'reminder_versions', 'reminder_occurrences',
    'reminder_occurrence_versions', 'reminder_operations',
    'people_ids', 'people_merge_sources', 'people_assertions', 'people_versions', 'people_sources',
    'people_source_withdrawals', 'people_name_terms', 'people_operations',
    'people_grants', 'people_grant_operations', 'people_state',
    'context_events', 'conversation_titles', 'exchange_exclusions', 'exchange_actions',
    'exchange_removed_ids', 'exchange_removed_submissions', 'detached_note_origins',
    'manual_memory_sources', 'excluded_memory_revisions', 'excluded_background_runs', 'exchange_sequence_floor',
    'conversation_imports', 'imported_conversations', 'imported_exchanges', 'imported_messages', 'imported_exclusions',
    'skill_state', 'skill_packages', 'skill_revisions', 'skill_files', 'turn_skill_selections',
    'mcp_calls', 'recall_admissions', 'web_settings', 'web_setting_operations', 'web_snapshots',
)


def key_digest(key):
    return hashlib.sha256(key.encode()).hexdigest()


def strings(value, depth=0):
    """Include decoded JSON leaves; escaped copies must not evade the guard."""
    if depth > 20:
        raise Failure('removal_limit', 'Metadata nesting exceeds the removal check bound.')
    if isinstance(value, bytes):
        try:yield from strings(value.decode('utf-8'),depth+1)
        except UnicodeError:pass
    elif isinstance(value, str):
        yield value
        if value.startswith(('{', '[')):
            try:
                parsed = json.loads(value)
            except (ValueError, RecursionError):
                return
            yield from strings(parsed, depth + 1)
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child, depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from strings(child, depth + 1)


def mentions(value, identities):
    """Inspect opaque receipt references without granting authority to use them."""
    for text in strings(value):
        if any(ident in text for ident in identities):
            return True
        # Old signed handles remain in delivery metadata after their epochs
        # expire. Decode only to remove metadata, never to authorize a read.
        if len(text) <= 512 and '.' in text:
            try:
                packet = text[3:] if text.startswith('R1.') else text.split('.')[0]
                raw = base64.b64decode(packet + '=' * (-len(packet) % 4), altchars=b'-_', validate=True)
                if any(bytes.fromhex(ident) in raw or ident.encode() in raw for ident in identities):
                    return True
            except ValueError:
                pass
    return False


class NoteRemovalStore:
    def __init__(self, store):
        self.store, self.db = store, store.db
        self._charge = None

    def _query(self, sql, parameters=()):
        # Also charge short statements which never reach a progress callback.
        # All manual queries share this transaction's maintenance allowance.
        self._charge()
        return self.db.execute(sql, parameters)

    @contextmanager
    def _transaction(self, *, write=False):
        if self.db.in_transaction:
            raise Failure('removal_busy', 'Removal cannot commit another owner\'s transaction.')
        if not self.store.path.is_file():
            raise Failure('removal_unavailable', 'The selected store is unavailable.')
        deadline, steps = time.monotonic() + SECONDS, 0

        def progress():
            nonlocal steps
            steps += 1000
            return steps > MAX_STEPS or time.monotonic() >= deadline

        def charge():
            nonlocal steps
            # One inspected row/short statement is one maintenance unit. Only
            # SQLite's progress callback represents a batch of 1,000 VM steps;
            # charging that batch for every row made ordinary archives fail
            # after ~5,000 rows, often within milliseconds of a five-second cap.
            steps += 1
            if steps > MAX_STEPS or time.monotonic() >= deadline:
                raise Failure('removal_limit', 'Removal reached its maintenance work bound; nothing changed.')

        self._charge = charge
        self.db.set_progress_handler(progress, 1000)
        committing = False
        try:
            self._query('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield deadline
            if time.monotonic() >= deadline:
                raise Failure('removal_limit', 'Removal reached its maintenance deadline; nothing committed.')
            committing = True
            self.db.commit()
        except BaseException as error:
            uncertain = write and committing and not self.db.in_transaction
            self.db.set_progress_handler(None, 0)
            self.db.rollback()
            if uncertain:
                raise Failure('removal_outcome_unknown', 'Inspect the removal operation ID before a new intent.') from error
            if isinstance(error, sqlite3.Error):
                raise Failure('removal_unavailable', 'Removal storage/check failed; no change committed.') from error
            raise
        finally:
            self.db.set_progress_handler(None, 0)
            self._charge = None

    def _authority(self, conversation):
        if not identity(conversation):
            raise Failure('removal_identity', 'Use the exact conversation identity.')
        row = self._query('''SELECT b.*,g.epoch AS grant_epoch FROM memory_grants g
            JOIN memory_banks b ON b.id=g.bank_id
            WHERE g.conversation_id=? AND g.enabled=1''', (conversation,)).fetchone()
        if row is None:
            raise Failure('removal_forbidden', 'An active explicit bank-read grant is required.')
        # Manual user authorization is separate from model note-write permission.
        return row

    def _idle(self):
        if self._query("SELECT 1 FROM turns WHERE status='running' LIMIT 1").fetchone():
            raise Failure('removal_busy', 'Finish or stop the active turn before manual removal.')
        if self._query("SELECT 1 FROM background_runs WHERE status IN ('running','cancel_requested') LIMIT 1").fetchone():
            raise Failure('removal_busy', 'Finish or stop the active background run before manual removal.')

    def _snapshot(self):
        # The enclosing SQLite read/write transaction supplies a stable snapshot.
        # Stream unrelated rows instead of materializing the entire archive or
        # refusing merely because its aggregate size exceeds a note-work bound.
        owner = self
        class Rows:
            def __init__(self, table):
                self.table = table
            def __iter__(self):
                table = self.table
                columns = [r['name'] for r in owner._query(f'PRAGMA table_info({table})')]
                size = '+'.join(f'coalesce(length(CAST({c} AS BLOB)),0)' for c in columns)
                if owner._query(f'SELECT 1 FROM {table} WHERE ({size})>? LIMIT 1', (MAX_BYTES,)).fetchone():
                    raise Failure('removal_limit', 'An individual retained row exceeds the bounded copy check; nothing changed.')
                for row in owner._query(f'SELECT * FROM {table} ORDER BY rowid'):
                    yield dict(row)
        return {table: Rows(table) for table in TABLES}

    def _payload_text(self, meta):
        if meta['bytes'] > MAX_BYTES:
            raise Failure('removal_limit', 'An individual payload exceeds the bounded copy check; nothing changed.')
        raw, count = bytearray(), 0
        for part in self._query('SELECT * FROM memory_chunks WHERE payload_id=? ORDER BY ordinal', (meta['id'],)):
            if (part['ordinal'] != count or not isinstance(part['body'], bytes)
                    or hashlib.sha256(part['body']).hexdigest() != part['sha256']):
                raise Failure('removal_integrity', 'Payload chunks cannot be verified.')
            count += 1
            if count > MAX_ROWS or len(raw) + len(part['body']) > MAX_BYTES:
                raise Failure('removal_limit', 'An individual payload exceeds the bounded copy check; nothing changed.')
            raw.extend(part['body'])
        if len(raw) != meta['bytes'] or hashlib.sha256(raw).hexdigest() != meta['sha256']:
            raise Failure('removal_integrity', 'Payload cannot be verified.')
        try:
            return raw.decode('utf-8')
        except UnicodeError:
            raise Failure('removal_integrity', 'Payload is not exact UTF-8.') from None

    def _payloads(self, data, selected):
        result, size = {}, 0
        for meta in data['memory_payloads']:
            if meta['id'] in selected:
                text = self._payload_text(meta)
                size += meta['bytes']
                if size > MAX_BYTES:
                    raise Failure('removal_limit', 'The selected note revisions exceed the removal work bound.')
                result[meta['id']] = text
        if self._query('SELECT 1 FROM memory_chunks c LEFT JOIN memory_payloads p ON p.id=c.payload_id WHERE p.id IS NULL LIMIT 1').fetchone():
            raise Failure('removal_integrity', 'Orphan payload chunks prevent removal.')
        return result

    def _digest(self, data):
        digest = hashlib.sha256()
        for table, rows in data.items():
            digest.update(encoded(table).encode())
            for row in rows:
                # Typed, length-framed rows bind the entire stable state without
                # constructing one archive-sized JSON value in memory.
                raw = json.dumps(row, sort_keys=True, ensure_ascii=True,
                    default=lambda value: value.hex(), separators=(',', ':')).encode()
                digest.update(str(len(raw)).encode() + b':' + raw)
        return digest.hexdigest()

    def _plan(self, conversation, note_id, deadline):
        bank = self._authority(conversation)
        self._idle()
        if not identity(note_id):
            raise Failure('removal_identity', 'Use the exact note identity.')
        if bank['epoch'] != bank['index_epoch'] or bank['epoch'] >= 2**63 - MAX_ROWS * 8:
            raise Failure('removal_index_stale', 'Removal cannot certify an already stale index.')
        data = self._snapshot()
        note = self._query('SELECT * FROM memory_records WHERE id=?', (note_id,)).fetchone()
        owned = [r for r in data['note_records'] if r['record_id'] == note_id and r['bank_id'] == bank['id']]
        manual = note and self._query("SELECT 1 FROM memory_revisions WHERE id=? AND record_id=? AND author='user' AND origin='manual_memory'", (note['head_revision'],note_id)).fetchone()
        if (not note or not (owned or manual) or note['bank_id'] != bank['id'] or note['readable'] != 1
                or note['validity'] != 'current'):
            raise Failure('removal_target_unavailable', 'Target is not a current owned note in this bank.')
        revisions = [r for r in data['memory_revisions'] if r['record_id'] == note_id]
        ids = {r['id'] for r in revisions}
        payload_ids = {r['payload_id'] for r in revisions}
        owned_revisions = [r for r in data['note_revisions'] if r['revision_id'] in ids]
        if (not ids or note['head_revision'] not in ids or not manual and len(owned_revisions) != len(ids)
                or any(r['bank_id'] != bank['id'] for r in owned_revisions)):
            raise Failure('removal_integrity', 'The complete retained revision set cannot be verified.')
        if (any(r['payload_id'] in payload_ids and r['id'] not in ids for r in data['memory_revisions'])
                or any(r['payload_id'] in payload_ids for r in data['memory_sources'])
                or any(r['parent_revision'] in ids and r['revision_id'] not in ids for r in data['note_revisions'])
                or any(note_id in (r['old_id'], r['target_id']) for r in data['memory_supersessions'])):
            raise Failure('removal_policy_required', 'Shared payload, revision or supersession needs a separate lifecycle policy.')
        if any(r['source_revision_id'] in ids | {note_id} and r['revision_id'] not in ids
               and r['relation'] in ('quotes', 'contains_sensitive_copy_of') for r in data['memory_edges']):
            raise Failure('removal_copy_policy_required', 'An explicit copy link needs a copy-handling decision; nothing changed.')
        if len(revisions) + len(owned_revisions) + len(payload_ids) > MAX_ROWS:
            raise Failure('removal_limit', 'The selected note revision set exceeds the bounded removal check.')
        payloads = self._payloads(data, payload_ids)
        if any(r['id'] in payload_ids and (r['bank_id'] != bank['id'] or r['kind'] != 'memory')
               for r in data['memory_payloads']):
            raise Failure('removal_integrity', 'A retained revision points outside its owned memory payloads.')
        try:
            texts = {payloads[p] for p in payload_ids}
        except KeyError:
            raise Failure('removal_integrity', 'A retained revision body is missing.') from None
        if '' in texts:
            raise Failure('removal_integrity', 'Empty retained note body cannot be verified.')
        keys = set()
        for row in data['note_outcomes']:
            try:
                outcome = json.loads(row['outcome'])
                if not isinstance(outcome, dict) or key_digest(row['outcome']) != row['outcome_sha256']:
                    raise ValueError('outcome')
            except (ValueError, TypeError, RecursionError):
                raise Failure('removal_integrity', 'A retained outcome cannot be verified.') from None
            if row['bank_id'] == bank['id'] and outcome.get('note_id') == note_id:
                keys.add(row['operation_key'])
        if any(r['operation_key'] not in keys for r in owned_revisions):
            raise Failure('removal_integrity', 'A retained revision has no matching outcome.')
        if any(r['bank_id'] == bank['id'] and r['operation_key'] in keys and r['revision_id'] not in ids
               for r in data['note_revisions']):
            raise Failure('removal_integrity', 'A mutation outcome is shared across notes.')
        identities = ids | payload_ids | {note_id}
        operations = [(r['turn_id'], r['ordinal']) for r in data['history_operations']
                      if mentions(r, identities) or any(k in set(strings(r['arguments'])) for k in keys)]
        # Review operation receipts have the same note-owned lifetime as the
        # foreground receipt pair. Only these typed operation phases are removed;
        # independent run inputs, final accounts and other retained copies stay
        # in the existing copy check. No audit-wide exemption or cascade.
        review_operations = set()
        for row in data['background_run_events']:
            if row['kind'] not in ('operation_started', 'operation_prepared', 'operation_submitted'):
                continue
            try:
                value = json.loads(row['payload'])
                ordinal = value['ordinal']
                if type(ordinal) is not int or not 1 <= ordinal <= MAX_FOREGROUND_OPERATIONS:
                    raise ValueError
            except (ValueError, TypeError, KeyError, RecursionError):
                raise Failure('removal_integrity', 'Review operation receipt identity is invalid.') from None
            pair = (row['run_id'], ordinal)
            if mentions(row, identities) or any(k in set(strings(row['payload'])) for k in keys):
                review_operations.add(pair)
        review_events = {row['sequence'] for row in data['background_run_events']
            if row['kind'] in ('operation_started', 'operation_prepared', 'operation_submitted')
            and (row['run_id'], json.loads(row['payload'])['ordinal']) in review_operations}
        if len(operations) + len(review_events) + len(keys) > MAX_ROWS:
            raise Failure('removal_limit', 'The selected note receipt set exceeds the removal work bound.')
        # Inspect every active body, including source/answer/provider state, plus
        # retained metadata. Do not silently exempt a copy just because it lives
        # in an independently-authored object. Partial/paraphrased copies remain
        # outside this first explicit guard, not a claimed semantic guarantee.
        def candidates():
            for meta in data['memory_payloads']:
                if meta['id'] not in payload_ids:
                    yield self._payload_text(meta)
            for table, rows in data.items():
                if table in ('memory_chunks', 'memory_banks'):
                    continue
                for row in rows:
                    if (table == 'memory_revisions' and row['id'] in ids
                            or table in ('recall_segments','recall_dirty') and row['kind']=='memory' and row['source_id'] in ids
                            or table in ('note_revisions', 'note_bases', 'note_metadata', 'memory_index') and row['revision_id'] in ids
                            or table == 'note_outcomes' and row['bank_id'] == bank['id'] and row['operation_key'] in keys
                            or table == 'background_run_events' and row['sequence'] in review_events
                            or table == 'history_operations' and (row['turn_id'], row['ordinal']) in operations):
                        continue
                    yield from strings(row)
        for text in candidates():
            if time.monotonic() >= deadline:
                raise Failure('removal_limit', 'Removal copy check reached its deadline; nothing changed.')
            if any(body in text for body in texts):
                raise Failure('removal_copy_policy_required', 'A retained whole-body copy needs a copy-handling decision; nothing changed.')
        index = {r['revision_id']: r for r in data['memory_index'] if r['revision_id'] in ids}
        for revision in revisions:
            entry = index.get(revision['id'])
            if entry is None and self._query('SELECT 1 FROM excluded_memory_revisions WHERE revision_id=?', (revision['id'],)).fetchone():
                continue
            if (entry is None or entry['bank_id'] != bank['id'] or entry['sequence'] != revision['sequence']
                    or entry['sha256'] != key_digest(payloads[revision['payload_id']])):
                raise Failure('removal_integrity', 'The retained discovery projection cannot be verified.')
        digest = self._digest(data)
        token = hmac.new(bank['handle_key'], encoded([self.store.state_id, conversation, note_id, digest]).encode(), hashlib.sha256).hexdigest()
        return dict(bank=bank['id'], note_id=note_id, revisions=revisions, payloads=payloads,
                    payload_ids=payload_ids, keys=keys, operations=operations, review_events=review_events, token=token,
                    head=note['head_revision'])

    def preview(self, conversation, note_id):
        with self._transaction() as deadline:
            plan = self._plan(conversation, note_id, deadline)
            return {'status': 'preview', 'note_id': note_id, 'preview_token': plan['token'],
                    'head_revision': plan['head'], 'revisions': [
                        {'revision_id': r['id'], 'content': plan['payloads'][r['payload_id']]}
                        for r in plan['revisions']],
                    'scope': 'whole_note_all_retained_revisions_active_store_only',
                    'copy_check': 'exact_whole_bodies_and_explicit_copy_links_only'}

    def preview_page(self, conversation, note_id, start=0, limit=20, expected_token=None):
        if type(start) is not int or start < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise Failure('removal_identity', 'Removal preview page is invalid.')
        with self._transaction() as deadline:
            plan = self._plan(conversation, note_id, deadline)
            if expected_token is not None and not hmac.compare_digest(plan['token'], expected_token):
                raise Failure('removal_preview_changed', 'Active state changed; request a new removal preview.')
            if start > len(plan['revisions']):
                raise Failure('removal_identity', 'Removal preview offset is outside its revision list.')
            bank = self._authority(conversation)
            return dict(note_id=note_id, head_revision=plan['head'], preview_token=plan['token'],
                view_token=view_token(self.store.memory, conversation, bank),
                total_revisions=len(plan['revisions']),
                total_bytes=sum(len(plan['payloads'][r['payload_id']].encode()) for r in plan['revisions']),
                revisions=[dict(revision_id=r['id'], sha256=key_digest(plan['payloads'][r['payload_id']]),
                    total_bytes=len(plan['payloads'][r['payload_id']].encode()), created_at=r['created_at'])
                    for r in plan['revisions'][start:start + limit]],
                next_offset=start + limit if start + limit < len(plan['revisions']) else None)

    def status(self, conversation, operation_id, *, include_intent=False):
        with self._transaction():
            bank = self._authority(conversation)
            result = self._status(conversation, bank['id'], operation_id)
            if include_intent and result['status'] == 'removed':
                row = self._query('SELECT preview_token FROM note_removals WHERE operation_id=?', (operation_id,)).fetchone()
                result.update(conversation_id=conversation, preview_token=row['preview_token'])
            return result

    def _status(self, conversation, bank, operation_id):
        if not identity(operation_id):
            raise Failure('removal_identity', 'Use an exact 32-lowercase-hex removal operation ID.')
        row = self._query('SELECT * FROM note_removals WHERE operation_id=?', (operation_id,)).fetchone()
        if row is None:
            return {'status': 'not_recorded', 'operation_id': operation_id}
        if row['conversation_id'] != conversation or row['bank_id'] != bank:
            raise Failure('removal_forbidden', 'This removal operation is outside the selected scope.')
        if (not identity(row['record_id'])
                or not self._query("SELECT 1 FROM removed_note_ids WHERE kind='record' AND id=?", (row['record_id'],)).fetchone()
                or self._query('SELECT 1 FROM memory_records WHERE id=?', (row['record_id'],)).fetchone()):
            raise Failure('removal_outcome_unknown', 'Removal receipt and active record identity disagree; no success can be confirmed.')
        return {'status': 'removed', 'operation_id': operation_id, 'note_id': row['record_id'],
                'removed_at': row['removed_at']}

    def apply(self, conversation, note_id, preview_token, operation_id):
        if (not identity(note_id) or not isinstance(preview_token, str) or len(preview_token) != 64
                or any(c not in '0123456789abcdef' for c in preview_token)):
            raise Failure('removal_identity', 'An exact note identity and unchanged preview token are required.')
        with self._transaction(write=True) as deadline:
            bank = self._authority(conversation)
            prior = self._status(conversation, bank['id'], operation_id)
            if prior['status'] == 'removed':
                row = self._query('SELECT record_id,preview_token FROM note_removals WHERE operation_id=?', (operation_id,)).fetchone()
                if row['record_id'] != note_id or not hmac.compare_digest(row['preview_token'], preview_token):
                    raise Failure('removal_intent_conflict', 'The operation ID already belongs to another removal intent.')
                return prior  # Delivery failure must not repeat the deletion.
            plan = self._plan(conversation, note_id, deadline)
            if not hmac.compare_digest(plan['token'], preview_token):
                raise Failure('removal_preview_changed', 'Active state changed since preview; inspect a new preview before removal.')
            self._query('PRAGMA defer_foreign_keys=ON')
            self._query('INSERT INTO note_removals VALUES(?,?,?,?,?,?)',
                (operation_id, conversation, bank['id'], note_id, preview_token, datetime.now(timezone.utc).isoformat()))
            self._query("INSERT INTO removed_note_ids VALUES('record',?)", (note_id,))
            # Cleanup stores identities only. Erasure invalidates recovery for
            # every affected group rather than retaining a route to restoration.
            for group in self._query('SELECT id,manifest,outcome FROM notebook_care_groups').fetchall():
                if mentions(dict(group), {note_id}):
                    self._query("UPDATE notebook_care_groups SET manifest='[]',outcome=NULL,status='source_removed' WHERE id=?", (group['id'],))
            self._query('DELETE FROM notebook_care_retired WHERE record_id=?', (note_id,))
            self._query('DELETE FROM notebook_care_items WHERE record_id=?', (note_id,))
            for revision in plan['revisions']:
                rid = revision['id']
                self._query("INSERT INTO removed_note_ids VALUES('revision',?)", (rid,))
                if self._query('SELECT 1 FROM memory_index WHERE revision_id=?', (rid,)).fetchone():
                    self._query("INSERT INTO memory_fts(memory_fts,rowid,scope,body) VALUES('delete',?,?,?)",
                        (revision['sequence'], 'b' + bank['id'], plan['payloads'][revision['payload_id']]))
                self._query('DELETE FROM memory_index WHERE revision_id=?', (rid,))
                self._query('DELETE FROM detached_note_origins WHERE revision_id=?', (rid,))
                self._query('DELETE FROM excluded_memory_revisions WHERE revision_id=?', (rid,))
                self._query('DELETE FROM note_bases WHERE revision_id=?', (rid,))
                self._query('DELETE FROM memory_edges WHERE revision_id=? OR source_revision_id=?', (rid, rid))
                self._query('DELETE FROM note_revisions WHERE revision_id=?', (rid,))
                self._query('DELETE FROM memory_revisions WHERE id=?', (rid,))
            for payload in plan['payload_ids']:
                self._query("INSERT INTO removed_note_ids VALUES('payload',?)", (payload,))
                self._query('DELETE FROM memory_chunks WHERE payload_id=?', (payload,))
                self._query('DELETE FROM memory_payloads WHERE id=?', (payload,))
            for key in plan['keys']:
                self._query('INSERT INTO removed_note_operations VALUES(?,?)', (bank['id'], key_digest(key)))
                self._query('DELETE FROM note_outcomes WHERE bank_id=? AND operation_key=?', (bank['id'], key))
            for turn, ordinal in plan['operations']:
                self._query('DELETE FROM history_operations WHERE turn_id=? AND ordinal=?', (turn, ordinal))
            for sequence in plan['review_events']:
                self._query('DELETE FROM background_run_events WHERE sequence=?', (sequence,))
            self._query('DELETE FROM memory_edges WHERE source_revision_id=?', (note_id,))
            self._query('DELETE FROM note_records WHERE record_id=?', (note_id,))
            self._query('DELETE FROM memory_records WHERE id=?', (note_id,))
            # Manual source copies have no independent lifetime once the last
            # supporting edge is gone; never retain an orphaned question copy.
            for source in self._query('''SELECT s.id,s.payload_id FROM memory_sources s JOIN manual_memory_sources m ON m.source_id=s.id
                    WHERE NOT EXISTS(SELECT 1 FROM memory_edges e WHERE e.source_revision_id=s.id)''').fetchall():
                self._query('DELETE FROM manual_memory_sources WHERE source_id=?', (source['id'],))
                self._query('DELETE FROM memory_sources WHERE id=?', (source['id'],))
                self._query('DELETE FROM memory_chunks WHERE payload_id=?', (source['payload_id'],))
                self._query('DELETE FROM memory_payloads WHERE id=?', (source['payload_id'],))
            self._query('UPDATE memory_banks SET index_epoch=epoch WHERE id=?', (bank['id'],))
            return self._status(conversation, bank['id'], operation_id)
