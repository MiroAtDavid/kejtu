"""Incremental conversation projection over the single exact SQLite source store.

This adapter owns indexes and bounded reads, not admission or model orchestration.
The contentless FTS index stores no second copy of source bodies.
"""
from contextlib import contextmanager
import base64
import hashlib
import json
import re
import sqlite3
import time

from ...core.budget import message_cost
from ...core.history import HistorySnapshot, HistoryUnit
from ...core.records import Failure, Message
from ...core.reference import exact_page
from ...core.notes import intent_digest, note_arguments, operation_key

HISTORY_SCHEMA = '''
CREATE TABLE history_units(turn_id TEXT PRIMARY KEY REFERENCES turns(id),
 conversation_id TEXT NOT NULL, sequence INTEGER NOT NULL, ordinal INTEGER NOT NULL,
 cost INTEGER, UNIQUE(conversation_id,ordinal));
CREATE INDEX unfinished_history ON history_units(turn_id) WHERE cost IS NULL;
CREATE TABLE history_sources(id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL,
 turn_id TEXT NOT NULL REFERENCES turns(id), sequence INTEGER NOT NULL,
 artifact_id TEXT NOT NULL UNIQUE REFERENCES artifacts(id), role TEXT NOT NULL);
CREATE INDEX scoped_sources ON history_sources(conversation_id,id);
CREATE VIRTUAL TABLE history_fts USING fts5(scope,body,content='',tokenize='unicode61 remove_diacritics 0');
CREATE TABLE history_admissions(turn_id TEXT PRIMARY KEY REFERENCES turns(id), record TEXT NOT NULL);
CREATE TABLE history_operations(turn_id TEXT NOT NULL REFERENCES turns(id), ordinal INTEGER NOT NULL,
 call_id TEXT NOT NULL, name TEXT NOT NULL, arguments TEXT NOT NULL, state TEXT NOT NULL,
 result TEXT, PRIMARY KEY(turn_id,ordinal));
CREATE INDEX unfinished_operations ON history_operations(state) WHERE state='started';
'''


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def failure_marker(row):
    return Message('system', f"Application record: preceding request ended {row['status']} ({row['reason']}); no completed assistant answer was delivered.")


class HistoryStore:
    def __init__(self, store):
        self.store = store
        self.db = store.db

    @contextmanager
    def bounded(self, ledger):
        # Reserve the partial quanta of at most four metadata statements in the
        # existing source/state owner. Our own statements each reserve a quantum
        # below: SQLite's callback alone can miss every short statement's tail.
        ledger.spend_sql(400)
        error = None
        def progress():
            nonlocal error
            try:
                ledger.spend_sql()
                if time.monotonic() >= ledger.deadline:
                    raise Failure('timeout', 'History access reached the inherited deadline.')
            except Failure as caught:
                error = caught
                return 1
            return 0
        if progress():
            raise error
        self.db.set_progress_handler(progress, 100)
        try:
            yield
        except sqlite3.DatabaseError:
            if error:
                raise error from None
            raise Failure('history_index_unavailable', 'History index access failed; no scan fallback ran.') from None
        finally:
            self.db.set_progress_handler(None, 0)
        if time.monotonic() >= ledger.deadline:
            raise Failure('timeout', 'History access reached the inherited deadline; no result was delivered.')

    def query(self, ledger, sql, parameters=()):
        ledger.spend_sql()  # Final partial VM quantum, even for indexed point reads.
        return self.db.execute(sql, parameters)

    def start(self, turn):
        previous = self.db.execute('SELECT ordinal FROM history_units WHERE conversation_id=? ORDER BY ordinal DESC LIMIT 1',
                                   (turn['conversation_id'],)).fetchone()
        self.db.execute('INSERT INTO history_units VALUES(?,?,?,?,NULL)',
                        (turn['id'], turn['conversation_id'], turn['sequence'], 1 + (previous[0] if previous else 0)))

    def finish(self, turn, reply=None):
        """Same commit as the source outcome; index only this newly terminal unit.

        Cost uses ingress metadata and the already bounded reply, not old bodies.
        Terminal durability/cleanup remains possible after the execution deadline.
        """
        meta = self.db.execute('SELECT * FROM history_units WHERE turn_id=?', (turn['id'],)).fetchone()
        if meta is None:
            return
        if meta['cost'] is not None:
            raise Failure('store_integrity', 'A terminal history unit cannot be indexed twice.')
        size = self.db.execute('SELECT bytes FROM artifacts WHERE id=?', (turn['user_id'],)).fetchone()[0]
        unit = HistoryUnit(turn['id'], turn['sequence'], meta['ordinal'], 0, turn['created_at'])
        cost = message_cost(unit.cue()) + size + 32
        if turn['status'] == 'completed':
            conv = self.store.conversation(turn['conversation_id'])
            cost += message_cost(Message('assistant', reply.text,
                reasoning=reply.reasoning if conv.needs_reasoning_history else None))
        else:
            cost += message_cost(failure_marker(turn))
        self.db.execute('UPDATE history_units SET cost=? WHERE turn_id=?', (cost, turn['id']))
        sources = [(turn['user_id'], 'user')]
        if turn['status'] == 'completed':
            sources.append((turn['answer_id'], 'assistant'))
        for aid, role in sources:
            source_id = self.db.execute('INSERT INTO history_sources(conversation_id,turn_id,sequence,artifact_id,role) VALUES(?,?,?,?,?)',
                (turn['conversation_id'], turn['id'], turn['sequence'], aid, role)).lastrowid
            self.db.execute('INSERT INTO history_fts(rowid,scope,body) SELECT ?,?,body FROM artifact_payloads WHERE artifact_id=?',
                            (source_id, 'c' + turn['conversation_id'], aid))

    def recover(self):
        # Partial indexes visit unfinished rows only; no startup archive rebuild.
        for row in self.db.execute('SELECT t.* FROM history_units h JOIN turns t ON t.id=h.turn_id WHERE h.cost IS NULL').fetchall():
            if row['status'] != 'running':
                self.finish(row)
        self.db.execute("UPDATE history_operations SET state='interrupted' WHERE state='started'")

    def snapshot(self, turn_id, ledger):
        with self.bounded(ledger):
            turn = self.store._row(turn_id)
            row = self.query(ledger, 'SELECT ordinal FROM history_units WHERE turn_id=?', (turn_id,)).fetchone()
            if row is None or turn['status'] != 'running':
                raise Failure('history_unavailable', 'No running history snapshot is available.')
            ceiling = self.query(ledger, 'SELECT id FROM history_sources WHERE conversation_id=? AND sequence<? ORDER BY id DESC LIMIT 1',
                (turn['conversation_id'], turn['sequence'])).fetchone()
            return HistorySnapshot(turn_id, turn['conversation_id'], turn['sequence'], row[0] - 1, ceiling[0] if ceiling else 0)

    def unit(self, snapshot, ledger, *, ordinal=None, turn_id=None):
        with self.bounded(ledger):
            key, value = ('h.ordinal', ordinal) if ordinal is not None else ('h.turn_id', turn_id)
            row = self.query(ledger, f'''SELECT h.*,t.created_at FROM history_units h JOIN turns t ON t.id=h.turn_id
                WHERE h.conversation_id=? AND h.sequence<? AND {key}=?''',
                (snapshot.conversation_id, snapshot.before_sequence, value)).fetchone()
            if row is None or type(row['cost']) is not int or row['cost'] < 1:
                raise Failure('history_unavailable', 'Selected history identity is missing, foreign or not terminal.')
            return HistoryUnit(row['turn_id'], row['sequence'], row['ordinal'], row['cost'], row['created_at'],
                deliveries=self.store.deliveries.history_messages(snapshot.conversation_id, row['sequence'], ledger))

    def body(self, aid, kind, ledger, maximum):
        if self.query(ledger, "SELECT 1 FROM imported_messages WHERE artifact_id=? AND excluded=1", (aid,)).fetchone():
            raise Failure("history_source_unavailable", "This original was excluded from recall before import.")
        # Start from exclusions: an empty/small exclusion set must not scan
        # every historical turn for each original-body read.
        if self.query(ledger, '''SELECT 1 FROM exchange_exclusions e CROSS JOIN turns t ON t.id=e.turn_id
                WHERE ? IN (t.user_id,t.answer_id,t.partial_id)''', (aid,)).fetchone():
            raise Failure('history_source_unavailable', 'This original is excluded from recall.')
        row = self.query(ledger, '''SELECT a.*,p.rowid AS payload_rowid,typeof(p.body) AS payload_type
            FROM artifacts a LEFT JOIN artifact_payloads p ON p.artifact_id=a.id WHERE a.id=?''', (aid,)).fetchone()
        if (row is None or row['kind'] != kind or row['payload_type'] != 'text'
                or type(row['bytes']) is not int or not 0 <= row['bytes'] <= maximum):
            raise Failure('store_integrity', 'Historical source identity or bounded envelope is invalid.')
        ledger.spend_payload(row['bytes'])
        with self.db.blobopen('artifact_payloads', 'body', row['payload_rowid'], readonly=True) as blob:
            if len(blob) != row['bytes']:
                raise Failure('store_integrity', 'Historical source size is invalid.')
            raw = blob.read(row['bytes'])
        if hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise Failure('store_integrity', 'Historical source hash is invalid.')
        try:
            return raw.decode('utf-8')
        except UnicodeError:
            raise Failure('store_integrity', 'Historical source is not valid UTF-8.') from None

    def load(self, snapshot, unit, ledger):
        with self.bounded(ledger):
            row = self.store._row(unit.turn_id)
            if row['conversation_id'] != snapshot.conversation_id or row['sequence'] >= snapshot.before_sequence:
                raise Failure('history_unavailable', 'Historical unit is outside this snapshot.')
            user = self.body(row['user_id'], 'user_message', ledger, 4 * ledger.profile.context_limit)
            messages = [unit.cue(), Message('user', user)]
            if row['status'] == 'completed':
                answer = self.body(row['answer_id'], 'assistant_message', ledger, ledger.response_bytes)
                conv = self.store.conversation(snapshot.conversation_id)
                state = self.store._reasoning(row, conv, unit.cost, ledger=ledger) if conv.needs_reasoning_history else None
                messages.append(Message('assistant', answer, reasoning=state))
            else:
                messages.append(failure_marker(row))
            if sum(message_cost(m) for m in messages) != unit.cost:
                raise Failure('store_integrity', 'Historical unit cost does not match its exact projection.')
            return tuple(messages)

    def save_admission(self, snapshot, record):
        with self.db:
            self.db.execute('INSERT INTO history_admissions VALUES(?,?)', (snapshot.turn_id, encoded(record)))

    def admission(self, turn_id):
        row = self.db.execute('SELECT record FROM history_admissions WHERE turn_id=?', (turn_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def _cursor(self, snapshot, query, before):
        return base64.urlsafe_b64encode(encoded([snapshot.turn_id, hashlib.sha256(query.encode()).hexdigest(), before]).encode()).decode()

    def search(self, snapshot, args, ledger, maximum, *, budget=None):
        def fits(page):
            return len(encoded(page).encode()) <= maximum and (budget is None or budget.fits(page))
        query, cursor = args['query'], args['cursor']
        before = snapshot.source_ceiling + 1
        if cursor:
            try:
                snap, digest, before = json.loads(base64.b64decode(cursor, altchars=b'-_', validate=True))
                if (snap != snapshot.turn_id or digest != hashlib.sha256(query.encode()).hexdigest()
                        or type(before) is not int or not 1 <= before <= snapshot.source_ceiling + 1):
                    raise ValueError('scope')
            except (ValueError, TypeError, UnicodeError):
                raise Failure('invalid_history_cursor', 'Cursor does not belong to this query and snapshot.') from None
        result = {'status': 'complete', 'snapshot': snapshot.turn_id, 'order': 'newest_first',
                  'previews_only': True, 'results': [], 'next_cursor': None}
        with self.bounded(ledger):
            if query.strip():
                # Double quotes turn user input into one literal phrase, never syntax.
                match = 'scope:"c' + snapshot.conversation_id + '" AND body:"' + query.replace('"', '""') + '"'
                rows = self.query(ledger, 'SELECT rowid FROM history_fts WHERE history_fts MATCH ? AND rowid<? ORDER BY rowid DESC LIMIT 9',
                                       (match, before)).fetchall()
            else:
                rows = self.query(ledger, 'SELECT id FROM history_sources WHERE conversation_id=? AND id<? ORDER BY id DESC LIMIT 9',
                                       (snapshot.conversation_id, before)).fetchall()
            previous, delivered_positions = before, []
            for index, hit in enumerate(rows):
                if index == 8:
                    result['status'] = 'page'
                    result['next_cursor'] = self._cursor(snapshot, query, previous)
                    break
                source = self.source(snapshot, hit[0], ledger, by_id=True)
                body = self.body(source['artifact_id'], self.source_kind(source),
                                 ledger, max(4 * ledger.profile.context_limit, ledger.response_bytes))
                literal = re.search(re.escape(query), body, re.IGNORECASE) if query.strip() else None
                offset = len(body[:literal.start()].encode()) if literal else 0
                preview = exact_page(source['artifact_id'], body, offset, 256).envelope()
                entry = dict(source)
                entry.update(sha256=preview['sha256'], total_bytes=preview['total_bytes'],
                             preview=preview['content'], preview_offset=offset, preview_end=preview['end'],
                             preview_kind='matched_literal' if literal else 'leading')
                more = index + 1 < len(rows)
                candidate = dict(result, results=result['results'] + [entry], status='page' if more else 'complete',
                                 next_cursor=self._cursor(snapshot, query, hit[0]) if more else None)
                # Smaller pages/previews are explicit. Never label a preview a
                # full read or skip a result just to fit cheaper later matches.
                while not fits(candidate) and len(entry['preview'].encode()) > 16:
                    page = exact_page(source['artifact_id'], body, offset, len(entry['preview'].encode()) // 2)
                    entry.update(preview=page.content, preview_end=page.end)
                if not fits(candidate):
                    result.update(status='page' if result['results'] else 'not_delivered',
                        next_cursor=self._cursor(snapshot, query, previous), reason='result_capacity')
                    break
                result['results'].append(entry)
                delivered_positions.append(hit[0])
                previous = hit[0]
            # The final reason/cursor is part of the same result envelope. A
            # removed whole hit must remain reachable through the continuation.
            while result['results'] and not fits(result):
                result['results'].pop()
                delivered_positions.pop()
                result.update(status='page' if result['results'] else 'not_delivered', reason='result_capacity',
                    next_cursor=self._cursor(snapshot, query, delivered_positions[-1] if delivered_positions else before))
            return result

    def source(self, snapshot, ident, ledger, *, by_id=False):
        field = 's.id' if by_id else 's.artifact_id'
        row = self.query(ledger, f'''SELECT s.id,s.turn_id,s.artifact_id,s.role,t.status,t.created_at
            FROM history_sources s JOIN turns t ON t.id=s.turn_id
            WHERE s.conversation_id=? AND s.sequence<? AND s.id<=? AND {field}=?
              AND t.conversation_id=s.conversation_id AND t.sequence=s.sequence
              AND ((s.role='user' AND s.artifact_id=t.user_id)
                   OR (s.role='assistant' AND t.status='completed' AND s.artifact_id=t.answer_id)
                   OR (s.role='background' AND EXISTS(SELECT 1 FROM background_deliveries d
                       WHERE d.result_id=s.artifact_id AND d.conversation_id=s.conversation_id
                       AND d.after_sequence=s.sequence AND d.status='presented')))''',
            (snapshot.conversation_id, snapshot.before_sequence, snapshot.source_ceiling, ident)).fetchone()
        if row is None:
            raise Failure('history_source_unavailable', 'Source is not a visible original in this conversation snapshot.')
        return row

    @staticmethod
    def source_kind(source):
        return {'user':'user_message', 'assistant':'assistant_message', 'background':'background_result'}[source['role']]

    def read(self, snapshot, args, ledger, maximum):
        with self.bounded(ledger):
            source = self.source(snapshot, args['artifact_id'], ledger)
            body = self.body(source['artifact_id'], self.source_kind(source),
                             ledger, max(4 * ledger.profile.context_limit, ledger.response_bytes))
            result = exact_page(source['artifact_id'], body, args['offset'], min(args['max_bytes'], maximum)).envelope()
            result['artifact_id'] = result.pop('reference_id')
            result.update(turn_id=source['turn_id'], role=source['role'], created_at=source['created_at'])
            return result

    def reference(self, snapshot, args, ledger, maximum):
        with self.bounded(ledger):
            conv = self.store.conversation(snapshot.conversation_id)
            if not conv.reference_id:
                raise Failure('unsupported_tool', 'No reference was granted.')
            body = self.body(conv.reference_id, 'reference', ledger, 4 * ledger.profile.context_limit)
            return exact_page(conv.reference_id, body, args[0], min(args[1], maximum)).envelope()

    def start_operation(self, snapshot, ordinal, call, *, preflight_rejected=False):
        turn = self.store._row(snapshot.turn_id)
        conv = self.store.conversation(turn['conversation_id'])
        limits = self.store.execution.for_turn(snapshot.turn_id)
        previous = self.db.execute('SELECT count(*),coalesce(max(ordinal),0) FROM history_operations WHERE turn_id=?',
                                   (snapshot.turn_id,)).fetchone()
        unfinished = self.db.execute("SELECT 1 FROM history_operations WHERE turn_id=? AND state NOT IN ('prepared','submitted') LIMIT 1",
                                     (snapshot.turn_id,)).fetchone()
        # Model calls and operation ordinals are independent for batches. Keep
        # durable sequencing and saved-policy checks nonetheless. The ledger
        # still requires the model attempt to be settled before each operation.
        if (not conv.history_enabled or turn['status'] != 'running'
                or not 1 <= turn['calls'] < limits.calls
                or turn['conversation_id'] != snapshot.conversation_id
                or not conv.history_enabled or type(ordinal) is not int
                or not 1 <= ordinal <= limits.operations
                or previous[0] != previous[1] or ordinal != previous[1] + 1
                or unfinished):
            raise Failure('tool_limit', 'No operation is permitted under the saved execution policy.')
        try:
            with self.db:
                arguments = call.arguments
                if preflight_rejected:
                    # A dispatch receipt for a NOT-executed batch, not a note
                    # intent/outcome. No duplicate malformed body or valid
                    # neighbour's text is persisted by this feedback path.
                    arguments = encoded({'preflight_rejected': True,
                        'arguments_sha256': hashlib.sha256(call.arguments.encode('utf-8')).hexdigest()})
                elif call.name == 'write_note':
                    args = note_arguments(call)
                    # Mutation bodies have their own exact source/revisions. Do
                    # not persist another copy in native delivery receipts.
                    arguments = encoded({'operation_key': operation_key(snapshot.turn_id, args),
                                         'intent_sha256': intent_digest(args)})
                elif call.name in ('people','university','media'):
                    arguments = encoded({'arguments_sha256': hashlib.sha256(call.arguments.encode()).hexdigest()})
                elif call.name == 'reminders':
                    from ...core.reminder_tools import arguments as reminder_arguments, mutation_key
                    args = reminder_arguments(call)
                    record = {'arguments_sha256': hashlib.sha256(call.arguments.encode()).hexdigest()}
                    if args['action'] in ('save', 'cancel', 'dismiss'):
                        record['mutation_key'] = mutation_key(self.store.state_id, snapshot.turn_id, args)
                    arguments = encoded(record)
                self.db.execute('INSERT INTO history_operations VALUES(?,?,?,?,?,?,NULL)',
                    (snapshot.turn_id, ordinal, call.id, call.name, arguments, 'started'))
        except sqlite3.IntegrityError:
            raise Failure('tool_limit', 'An operation was already reserved; nothing was replayed.') from None

    def prepared(self, snapshot, ordinal, result):
        # Result bodies/previews stay in the exact source, not duplicated in receipts.
        from ...core.tools import result_receipt
        record = result_receipt(result)
        with self.db:
            if self.db.execute("UPDATE history_operations SET state='prepared',result=? WHERE turn_id=? AND ordinal=? AND state='started'",
                (encoded(record), snapshot.turn_id, ordinal)).rowcount != 1:
                raise Failure('turn_state', 'Operation was not started.')

    def submitted(self, snapshot, ordinal):
        with self.db:
            if self.db.execute("UPDATE history_operations SET state='submitted' WHERE turn_id=? AND ordinal=? AND state='prepared'",
                               (snapshot.turn_id, ordinal)).rowcount != 1:
                raise Failure('turn_state', 'Operation was not prepared for intended submission.')

    def operations(self, turn_id):
        return tuple(dict(row) for row in self.db.execute('SELECT * FROM history_operations WHERE turn_id=? ORDER BY ordinal', (turn_id,)))
