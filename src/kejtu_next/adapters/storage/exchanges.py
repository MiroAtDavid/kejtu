"""Manual exchange actions, committed with their receipts and recall invalidation.

No model may invoke this owner. Exchange deletion removes the selected originals
and their owned records; later replies and independent notes/copies survive.
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone

from ...core.records import Failure
from .history import encoded
from .memory import CHUNK_BYTES
from .note_removal import NoteRemovalStore, TABLES, MAX_BYTES, MAX_ROWS, mentions


EXCHANGE_SCHEMA = '''
CREATE TABLE exchange_exclusions(turn_id TEXT PRIMARY KEY, root_id TEXT NOT NULL);
CREATE TABLE excluded_memory_revisions(revision_id TEXT PRIMARY KEY);
CREATE TABLE excluded_background_runs(run_id TEXT PRIMARY KEY);
CREATE TABLE exchange_sequence_floor(singleton INTEGER PRIMARY KEY CHECK(singleton=1), sequence INTEGER NOT NULL);
INSERT INTO exchange_sequence_floor VALUES(1,0);
CREATE VIEW recall_turns AS SELECT t.* FROM turns t
 WHERE NOT EXISTS(SELECT 1 FROM exchange_exclusions e WHERE e.turn_id=t.id);
CREATE TABLE exchange_actions(mutation_key TEXT PRIMARY KEY, intent_sha256 TEXT NOT NULL, receipt TEXT NOT NULL);
CREATE TABLE recall_admissions(turn_id TEXT PRIMARY KEY REFERENCES turns(id), record TEXT NOT NULL);
CREATE TABLE exchange_removed_ids(id TEXT PRIMARY KEY, kind TEXT NOT NULL);
CREATE TABLE exchange_removed_submissions(mutation_key TEXT PRIMARY KEY, intent_sha256 TEXT NOT NULL,
 conversation_id TEXT NOT NULL, turn_id TEXT NOT NULL);
CREATE TABLE detached_note_origins(revision_id TEXT PRIMARY KEY REFERENCES note_revisions(revision_id));
CREATE TABLE manual_memory_sources(source_id TEXT PRIMARY KEY REFERENCES memory_sources(id),
 artifact_id TEXT NOT NULL, turn_id TEXT NOT NULL);
'''
for _table, _kind in (('turns', 'turn'), ('artifacts', 'artifact')):
    for _event in ('INSERT', 'UPDATE OF id'):
        EXCHANGE_SCHEMA += f'''CREATE TRIGGER {_table}_removed_exchange_{_event.split()[0].lower()}
         BEFORE {_event} ON {_table}
         WHEN EXISTS(SELECT 1 FROM exchange_removed_ids WHERE id=NEW.id)
         BEGIN SELECT RAISE(ABORT,'removed exchange identity'); END;
'''


def intent(args):
    return hashlib.sha256(json.dumps({key: args[key] for key in
        ('conversation_id', 'turn_id', 'action', 'text')}, ensure_ascii=False,
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class ExchangeStore(NoteRemovalStore):
    def lookup(self, key):
        row = self.db.execute('SELECT receipt FROM exchange_actions WHERE mutation_key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else {'status': 'absent'}

    def apply(self, args):
        cid, tid, key = (args.get(k) for k in ('conversation_id', 'turn_id', 'mutation_key'))
        action, text = args.get('action'), args.get('text')
        if (not all(self.store._identity(v) for v in (cid, tid, key))
                or action not in ('save_memory', 'exclude', 'delete')
                or not isinstance(text, str) or len(text.encode()) > 8192
                or (action == 'save_memory' and not text.strip())
                or (action != 'save_memory' and text)):
            raise Failure('exchange_arguments', 'Choose an exact exchange and supported action.')
        digest = intent(args)
        self.store.recall.queries.clear()
        with self._transaction(write=True):
            previous = self.lookup(key)
            if previous['status'] != 'absent':
                if previous['intent_sha256'] != digest:
                    raise Failure('mutation_conflict', 'This operation identity belongs to a different change.')
                return previous
            self._idle()
            fresh_banks = [r[0] for r in self._query('SELECT id FROM memory_banks WHERE epoch=index_epoch')]
            turn = self._query('SELECT * FROM turns WHERE id=? AND conversation_id=?', (tid, cid)).fetchone()
            if turn is None:
                raise Failure('missing_turn', 'The selected exchange is no longer available.')
            result = dict(status='accepted', kind='exchange.action', conversation_id=cid,
                turn_id=tid, mutation_key=key, intent_sha256=digest, action=action,
                memory_id=None, excluded_count=0)
            if action == 'save_memory':
                result['memory_id'] = self._save(turn, text)
            elif action == 'exclude':
                result['excluded_count'] = self._exclude(tid)
            else:
                self._remove(turn)
            self._query('INSERT INTO exchange_actions VALUES(?,?,?)', (key, digest, encoded(result)))
            # Every issued memory handle and asynchronous projection observes the
            # changed authority. The source stores, never the cache, decide access.
            self._query('UPDATE memory_banks SET epoch=epoch+1')
            for bank in fresh_banks:
                self._query('UPDATE memory_banks SET index_epoch=epoch WHERE id=?', (bank,))
            self._query('UPDATE recall_epoch SET epoch=epoch+1')
            self._query('UPDATE people_state SET epoch=epoch+1')
            self._query('UPDATE personal_task_state SET epoch=epoch+1')
        self.store.recall.queries.clear()
        return result

    def _payload(self, bank, kind, text):
        pid, raw = uuid.uuid4().hex, text.encode()
        digest = hashlib.sha256(raw).hexdigest()
        self._query('INSERT INTO memory_payloads VALUES(?,?,?,?,?)', (pid, bank, kind, len(raw), digest))
        for ordinal, start in enumerate(range(0, len(raw), CHUNK_BYTES)):
            part = raw[start:start + CHUNK_BYTES]
            self._query('INSERT INTO memory_chunks VALUES(?,?,?,?)',
                        (pid, ordinal, hashlib.sha256(part).hexdigest(), part))
        return pid, digest

    def _save(self, turn, text):
        bank = self._authority(turn['conversation_id'])['id']
        if (self._query('SELECT 1 FROM exchange_exclusions WHERE turn_id=?', (turn['id'],)).fetchone()
                or self._query('SELECT 1 FROM imported_messages WHERE excluded=1 AND artifact_id IN (?,?,?)',
                    (turn['user_id'],turn['answer_id'],turn['partial_id'])).fetchone()):
            raise Failure('exchange_excluded', 'This exchange is excluded from recall.')
        if self._query('SELECT count(*) FROM memory_records WHERE bank_id=?', (bank,)).fetchone()[0] >= MAX_ROWS:
            raise Failure('memory_quota', 'The manual memory allowance is full.')
        original = self.store._body(turn['user_id'])
        if len(original.encode()) > MAX_BYTES:
            raise Failure('memory_source_size', 'The exact source exceeds the manual save bound.')
        rid, revision, source = (uuid.uuid4().hex for _ in range(3))
        payload, sha = self._payload(bank, 'memory', text)
        source_payload, _ = self._payload(bank, 'source', original)
        self._query('INSERT INTO memory_records VALUES(?,?,?,\'current\',1)', (rid, bank, revision))
        sequence = self._query('''INSERT INTO memory_revisions(id,record_id,payload_id,author,origin,created_at,readable)
            VALUES(?,?,?,'user','manual_memory',?,1)''', (revision, rid, payload, datetime.now(timezone.utc).isoformat())).lastrowid
        self._query('INSERT INTO memory_sources VALUES(?,?,?,?,?,?,1)',
            (source, turn['user_id'], bank, source_payload, 'message', turn['created_at']))
        self._query('INSERT INTO manual_memory_sources VALUES(?,?,?)', (source, turn['user_id'], turn['id']))
        self._query('INSERT INTO memory_edges(revision_id,source_revision_id,relation) VALUES(?,?,?)',
                    (revision, source, 'user_saved_from_exchange'))
        self._query('INSERT INTO memory_index VALUES(?,?,?,?)', (revision, bank, sequence, sha))
        self._query('INSERT INTO memory_fts(rowid,scope,body) VALUES(?,?,?)', (sequence, 'b' + bank, text))
        return rid

    def _unindex(self, tid):
        for source in self._query('SELECT * FROM history_sources WHERE turn_id=?', (tid,)).fetchall():
            body = self.store._body(source['artifact_id'])
            self._query("INSERT INTO history_fts(history_fts,rowid,scope,body) VALUES('delete',?,?,?)",
                        (source['id'], 'c' + source['conversation_id'], body))
        self._query('DELETE FROM history_sources WHERE turn_id=?', (tid,))
        self._query("DELETE FROM recall_segments WHERE kind='operation' AND source_id LIKE ?", (tid + ':%',))
        self._query("DELETE FROM recall_dirty WHERE kind='operation' AND source_id LIKE ?", (tid + ':%',))
        self._query('DELETE FROM history_units WHERE turn_id=?', (tid,))

    def _renumber(self):
        # Ordinals are a dense admission index, not source/provenance positions.
        # Stable turn sequence, identities, timestamps and exact text never move.
        rows = self._query('SELECT turn_id,conversation_id FROM history_units ORDER BY conversation_id,sequence').fetchall()
        if len(rows) > MAX_ROWS:
            raise Failure('removal_limit', 'History maintenance exceeds its work bound.')
        self._query('UPDATE history_units SET ordinal=-ordinal')
        cid, ordinal = None, 0
        for row in rows:
            ordinal = ordinal + 1 if cid == row['conversation_id'] else 1
            cid = row['conversation_id']
            self._query('UPDATE history_units SET ordinal=? WHERE turn_id=?', (ordinal, row['turn_id']))

    def _exclude(self, tid):
        selected = {tid}
        # Exact recorded admissions/tool receipts establish dependencies. No
        # keyword guessing, and later unrelated conversations remain eligible.
        records = []
        for table, column in (('history_admissions', 'record'), ('history_operations', 'result'), ('recall_admissions','record')):
            records.extend((r['turn_id'], r[column]) for r in self._query(f'SELECT turn_id,{column} FROM {table}'))
        if len(records) > MAX_ROWS:
            raise Failure('removal_limit', 'Dependency maintenance exceeds its work bound.')
        runs = set()
        while True:
            previous_runs = set(runs)
            identities = set(selected) | runs
            for row in self._query('SELECT id,user_id,answer_id FROM turns'):
                if row['id'] in selected:
                    identities.update(v for v in (row['user_id'], row['answer_id']) if v)
            for row in self._query('''SELECT s.turn_id,e.revision_id,v.record_id FROM manual_memory_sources s
                    JOIN memory_edges e ON e.source_revision_id=s.source_id JOIN memory_revisions v ON v.id=e.revision_id'''):
                if row['turn_id'] in selected:
                    identities.update((row['revision_id'],row['record_id']))
            for row in self._query('SELECT run_id,payload FROM background_run_events'):
                if mentions(row['payload'], identities):
                    runs.add(row['run_id'])
            for row in self._query('SELECT id AS run_id,result_id FROM background_runs'):
                if row['run_id'] in runs and row['result_id']:
                    identities.add(row['result_id'])
            for row in self._query('''SELECT n.revision_id,v.record_id,n.turn_id,o.run_id FROM note_revisions n
                    JOIN memory_revisions v ON v.id=n.revision_id LEFT JOIN note_job_origins o
                    ON o.bank_id=n.bank_id AND o.actor=n.actor AND o.operation_key=n.operation_key'''):
                if row['turn_id'] in selected or row['run_id'] in runs:
                    identities.update((row['revision_id'], row['record_id']))
            while True:
                before = len(identities)
                for row in self._query('''SELECT b.memory_revision,b.revision_id,v.record_id FROM note_bases b
                        JOIN memory_revisions v ON v.id=b.revision_id'''):
                    if row['memory_revision'] in identities:
                        identities.update((row['revision_id'],row['record_id']))
                if len(identities) == before:
                    break
            additions = {owner for owner, record in records if owner not in selected and mentions(record, identities)}
            if not additions and runs == previous_runs:
                break
            selected.update(additions)
        for ident in selected:
            self._query('INSERT OR IGNORE INTO exchange_exclusions VALUES(?,?)', (ident, tid))
            self._query('DELETE FROM conversation_titles WHERE conversation_id=(SELECT conversation_id FROM turns WHERE id=?)', (ident,))
            self._unindex(ident)
            self._query('''UPDATE memory_sources SET readable=0 WHERE id IN
                (SELECT source_id FROM manual_memory_sources WHERE turn_id=?)''', (ident,))
            self._query('''INSERT OR IGNORE INTO excluded_memory_revisions SELECT revision_id FROM note_revisions WHERE turn_id=?
                UNION SELECT e.revision_id FROM memory_edges e JOIN manual_memory_sources s ON s.source_id=e.source_revision_id
                WHERE s.turn_id=?''', (ident, ident))
        for run in runs:
            self._query('INSERT OR IGNORE INTO excluded_background_runs VALUES(?)', (run,))
            self._query('''INSERT OR IGNORE INTO excluded_memory_revisions SELECT n.revision_id FROM note_revisions n
                JOIN note_job_origins o ON o.bank_id=n.bank_id AND o.actor=n.actor AND o.operation_key=n.operation_key WHERE o.run_id=?''', (run,))
            for source in self._query('''SELECT h.* FROM history_sources h JOIN background_deliveries d ON d.result_id=h.artifact_id
                    WHERE d.run_id=?''', (run,)).fetchall():
                body = self.store._body(source['artifact_id'])
                self._query("INSERT INTO history_fts(history_fts,rowid,scope,body) VALUES('delete',?,?,?)",
                            (source['id'], 'c' + source['conversation_id'], body))
                self._query('DELETE FROM history_sources WHERE id=?', (source['id'],))
        while self._query('''INSERT OR IGNORE INTO excluded_memory_revisions SELECT b.revision_id FROM note_bases b
                JOIN excluded_memory_revisions e ON e.revision_id=b.memory_revision''').rowcount:
            pass
        for row in self._query('''SELECT i.sequence,i.bank_id,v.payload_id,i.revision_id FROM memory_index i
                JOIN memory_revisions v ON v.id=i.revision_id JOIN excluded_memory_revisions e ON e.revision_id=i.revision_id''').fetchall():
            meta = self._query('SELECT * FROM memory_payloads WHERE id=?', (row['payload_id'],)).fetchone()
            body = self._payload_text(meta)
            self._query("INSERT INTO memory_fts(memory_fts,rowid,scope,body) VALUES('delete',?,?,?)", (row['sequence'], 'b' + row['bank_id'], body))
            self._query('DELETE FROM memory_index WHERE revision_id=?', (row['revision_id'],))
        self._renumber()
        return len(selected)

    def _remove(self, turn):
        tid, cid = turn['id'], turn['conversation_id']
        ids = {turn[k] for k in ('user_id', 'answer_id', 'partial_id') if turn[k]}
        ids.update(r[0] for r in self._query('SELECT reasoning_id FROM provider_states WHERE turn_id=?', (tid,)))
        ids.update(r[0] for r in self._query('SELECT result_id FROM mcp_calls WHERE turn_id=? AND result_id IS NOT NULL', (tid,)))
        for ident in ids:
            meta = self._query('SELECT bytes FROM artifacts WHERE id=?', (ident,)).fetchone()
            if meta is None or not 0 <= meta['bytes'] <= MAX_BYTES:
                raise Failure('removal_limit', 'A selected original exceeds the bounded removal check.')
        sources = self._query('''SELECT s.id,s.payload_id FROM memory_sources s
            JOIN manual_memory_sources m ON m.source_id=s.id WHERE m.turn_id=?''', (tid,)).fetchall()
        # This is exchange deletion, not archive-wide erasure. Independent
        # records keep their text; source reads still validate the removed IDs.
        self._unindex(tid)
        for source in sources:
            self._query('DELETE FROM memory_edges WHERE source_revision_id=?', (source['id'],))
            self._query('DELETE FROM manual_memory_sources WHERE source_id=?', (source['id'],))
            self._query('DELETE FROM memory_sources WHERE id=?', (source['id'],))
            self._query('DELETE FROM memory_chunks WHERE payload_id=?', (source['payload_id'],))
            self._query('DELETE FROM memory_payloads WHERE id=?', (source['payload_id'],))
        self._query('INSERT INTO detached_note_origins SELECT revision_id FROM note_revisions WHERE turn_id=?', (tid,))
        self._query('UPDATE note_revisions SET turn_id=NULL,user_id=NULL WHERE turn_id=?', (tid,))
        self._query('UPDATE note_outcomes SET turn_id=NULL WHERE turn_id=?', (tid,))
        self._query('''INSERT INTO exchange_removed_submissions SELECT mutation_key,intent_sha256,conversation_id,turn_id
            FROM native_mutations WHERE kind='turn.submit' AND turn_id=?''', (tid,))
        self._query('DELETE FROM native_mutations WHERE turn_id=?', (tid,))
        for table in ('context_events', 'model_attempts', 'reference_reads', 'provider_states',
                      'history_admissions', 'history_operations', 'recall_admissions', 'web_snapshots', 'mcp_calls', 'turn_skill_selections'):
            self._query(f'DELETE FROM {table} WHERE turn_id=?', (tid,))
        self._query('DELETE FROM conversation_titles WHERE conversation_id=?', (cid,))
        self._query("UPDATE imported_conversations SET title='' WHERE conversation_id=?", (cid,))
        self._query('UPDATE imported_exchanges SET error=NULL WHERE turn_id=?', (tid,))
        for ident in ids:
            self._query("UPDATE imported_messages SET provenance='{}' WHERE artifact_id=?", (ident,))
        self._query('UPDATE exchange_sequence_floor SET sequence=max(sequence,?) WHERE singleton=1', (turn['sequence'],))
        self._query('DELETE FROM turns WHERE id=?', (tid,))
        self._query('INSERT INTO exchange_removed_ids VALUES(?,\'turn\')', (tid,))
        for ident in ids:
            self._query('DELETE FROM artifact_chunks WHERE artifact_id=?', (ident,))
            self._query('DELETE FROM artifact_payloads WHERE artifact_id=?', (ident,))
            self._query('DELETE FROM artifacts WHERE id=?', (ident,))
            self._query('INSERT INTO exchange_removed_ids VALUES(?,\'artifact\')', (ident,))
        self._renumber()
