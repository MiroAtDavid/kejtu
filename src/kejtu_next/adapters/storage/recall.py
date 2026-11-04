"""Incremental hybrid projections; originals and grants remain authoritative.

Source commits enqueue exact identities. Only queued bodies are segmented; search
never enumerates original bodies to discover changes. Embeddings are replaceable
derived values with source hashes rechecked after asynchronous inference.
"""
import asyncio
from collections import OrderedDict
import hashlib
import heapq
import json
import time
import weakref

from ...core.recall import CONTRACT, EmbeddingNotReady, document_text, query_text, fuse, keywords, needs_context, query_for, segments, unpack_vector, vector_bytes
from ...core.records import Failure
from ...core.reference import exact_page
from ...core.note_fields import tag_key
from ...core.preferences import applies, MAX_PREFERENCES, MAX_PREFERENCE_BYTES
from .history import encoded
from .conversation_import import source_info

RECALL_SCHEMA = '''
CREATE TABLE recall_epoch(singleton INTEGER PRIMARY KEY CHECK(singleton=1), epoch INTEGER NOT NULL);
INSERT INTO recall_epoch VALUES(1,0);
CREATE TABLE recall_dirty(kind TEXT NOT NULL,source_id TEXT NOT NULL,PRIMARY KEY(kind,source_id));
CREATE TABLE recall_segments(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,source_id TEXT NOT NULL,
 record_id TEXT NOT NULL,bank_id TEXT,conversation_id TEXT,source_hash TEXT NOT NULL,
 offset INTEGER NOT NULL,body TEXT NOT NULL,role TEXT,created_at TEXT,label TEXT NOT NULL DEFAULT '');
CREATE INDEX recall_source ON recall_segments(kind,source_id);
CREATE INDEX recall_bank ON recall_segments(bank_id,kind);
CREATE INDEX recall_conversation ON recall_segments(conversation_id,kind);
CREATE VIRTUAL TABLE recall_fts USING fts5(body,label,content='recall_segments',content_rowid='id',tokenize='unicode61');
CREATE TABLE recall_vectors(segment_id INTEGER PRIMARY KEY REFERENCES recall_segments(id) ON DELETE CASCADE,
 source_hash TEXT NOT NULL,contract TEXT NOT NULL,vector BLOB NOT NULL CHECK(length(vector)=3072));
CREATE TABLE recall_pending(segment_id INTEGER PRIMARY KEY REFERENCES recall_segments(id) ON DELETE CASCADE);
CREATE TRIGGER recall_segment_insert AFTER INSERT ON recall_segments BEGIN
 INSERT INTO recall_fts(rowid,body,label) VALUES(NEW.id,NEW.body,NEW.label);
 INSERT INTO recall_pending VALUES(NEW.id);
END;
CREATE TRIGGER recall_segment_delete AFTER DELETE ON recall_segments BEGIN
 INSERT INTO recall_fts(recall_fts,rowid,body,label) VALUES('delete',OLD.id,OLD.body,OLD.label);
END;
CREATE TRIGGER recall_memory_insert AFTER INSERT ON memory_index BEGIN
 INSERT OR IGNORE INTO recall_dirty VALUES('memory',NEW.revision_id);
END;
CREATE TRIGGER recall_memory_index_delete AFTER DELETE ON memory_index BEGIN
 DELETE FROM recall_segments WHERE kind='memory' AND source_id=OLD.revision_id;
 DELETE FROM recall_dirty WHERE kind='memory' AND source_id=OLD.revision_id;
END;
CREATE TRIGGER recall_history_insert AFTER INSERT ON history_sources BEGIN
 INSERT OR IGNORE INTO recall_dirty VALUES('message',NEW.artifact_id);
 UPDATE recall_epoch SET epoch=epoch+1;
END;
CREATE TRIGGER recall_history_delete AFTER DELETE ON history_sources BEGIN
 DELETE FROM recall_segments WHERE kind='message' AND source_id=OLD.artifact_id;
 DELETE FROM recall_dirty WHERE kind='message' AND source_id=OLD.artifact_id;
 UPDATE recall_epoch SET epoch=epoch+1;
END;
CREATE TRIGGER recall_operations_terminal AFTER UPDATE OF cost ON history_units
 WHEN NEW.cost IS NOT NULL AND OLD.cost IS NULL BEGIN
 INSERT OR IGNORE INTO recall_dirty SELECT 'operation',turn_id||':'||ordinal
 FROM history_operations WHERE turn_id=NEW.turn_id;
END;
CREATE TRIGGER recall_operation_delete AFTER DELETE ON history_operations BEGIN
 DELETE FROM recall_segments WHERE kind='operation' AND source_id=OLD.turn_id||':'||OLD.ordinal;
 DELETE FROM recall_dirty WHERE kind='operation' AND source_id=OLD.turn_id||':'||OLD.ordinal;
 UPDATE recall_epoch SET epoch=epoch+1;
END;
CREATE INDEX recall_revision_payload ON memory_revisions(payload_id);
CREATE TRIGGER recall_chunk_update AFTER UPDATE ON memory_chunks BEGIN
 DELETE FROM recall_segments WHERE kind='memory' AND source_id IN
  (SELECT id FROM memory_revisions WHERE payload_id IN (OLD.payload_id,NEW.payload_id));
 INSERT OR IGNORE INTO recall_dirty SELECT 'memory',id FROM memory_revisions WHERE payload_id IN (OLD.payload_id,NEW.payload_id);
END;
CREATE TRIGGER recall_chunk_delete AFTER DELETE ON memory_chunks BEGIN
 DELETE FROM recall_segments WHERE kind='memory' AND source_id IN
  (SELECT id FROM memory_revisions WHERE payload_id=OLD.payload_id);
 INSERT OR IGNORE INTO recall_dirty SELECT 'memory',id FROM memory_revisions WHERE payload_id=OLD.payload_id;
END;
CREATE TRIGGER recall_revision_update AFTER UPDATE ON memory_revisions BEGIN
 DELETE FROM recall_segments WHERE kind='memory' AND source_id=OLD.id;
 INSERT OR IGNORE INTO recall_dirty VALUES('memory',NEW.id);
END;
CREATE TRIGGER recall_record_update AFTER UPDATE OF readable,validity ON memory_records BEGIN
 DELETE FROM recall_segments WHERE kind='memory' AND record_id=OLD.id;
 INSERT OR IGNORE INTO recall_dirty SELECT 'memory',id FROM memory_revisions WHERE record_id=NEW.id;
END;
CREATE TRIGGER recall_memory_payload_update AFTER UPDATE ON memory_payloads BEGIN
 DELETE FROM recall_segments WHERE kind='memory' AND source_id IN
  (SELECT id FROM memory_revisions WHERE payload_id=OLD.id);
 INSERT OR IGNORE INTO recall_dirty SELECT 'memory',id FROM memory_revisions WHERE payload_id=NEW.id;
END;
CREATE TRIGGER recall_payload_update AFTER UPDATE ON artifact_payloads BEGIN
 DELETE FROM recall_segments WHERE kind='message' AND source_id=OLD.artifact_id;
 INSERT OR IGNORE INTO recall_dirty SELECT 'message',artifact_id FROM history_sources WHERE artifact_id=NEW.artifact_id;
 UPDATE recall_epoch SET epoch=epoch+1;
END;
CREATE TRIGGER recall_grant_update AFTER UPDATE ON memory_grants BEGIN
 UPDATE recall_epoch SET epoch=epoch+1;
END;
CREATE TRIGGER recall_grant_delete AFTER DELETE ON memory_grants BEGIN
 UPDATE recall_epoch SET epoch=epoch+1;
END;
'''

class RecallStore:
    VECTOR_CACHE_BYTES = 32 * 1024 * 1024

    def __init__(self, store, embedding=None):
        self.store, self.db, self.embedding = store, store.db, embedding
        # Old vectors are derived data. Requeue their identities on reopen,
        # without reading originals or allowing cross-model similarity scores.
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO recall_pending SELECT segment_id FROM recall_vectors WHERE contract!=?",(CONTRACT,))
        self.queries = OrderedDict()
        self.retry_at = 0.
        self.unavailable = False
        self.metrics = dict(source_reads=0, segmented_sources=0, embedded_segments=0, query_embeddings=0)
        self._pool_ledger = None
        self._pool_context = None
        self._vector_pools = OrderedDict()
        self._pool_bytes = 0
        self._rankings = OrderedDict()
        # Connection-local invalidation covers every projection mutation, including
        # direct SQL and rolled-back writes. Nothing is added to the durable schema.
        # Other connections are covered by data_version below; source/grant epochs
        # are still checked by MemoryStore before and after every tool delivery.
        self.db.create_function('recall_projection_changed', 0, self._clear_candidates)
        for table in ('recall_segments', 'recall_vectors'):
            for event in ('INSERT', 'UPDATE', 'DELETE'):
                self.db.execute(f'''CREATE TEMP TRIGGER recall_cache_{table}_{event}
                    AFTER {event} ON main.{table} BEGIN SELECT recall_projection_changed(); END''')

    def _clear_candidates(self):
        self._vector_pools.clear()
        self._pool_bytes = 0
        self._rankings.clear()

    def _candidate_context(self, snapshot, ledger):
        version = self.store.memory.query(ledger, 'PRAGMA data_version').fetchone()[0]
        context = (snapshot, version)
        # Never reuse uncommitted candidates: rollback does not fire triggers.
        if self.db.in_transaction or self._pool_ledger is None or self._pool_ledger() is not ledger or self._pool_context != context:
            self._clear_candidates()
            self._pool_ledger, self._pool_context = weakref.ref(ledger), context

    def _vectors(self, branches, ledger):
        """Reuse only a checked eligibility predicate within this one turn view."""
        if len(branches)>1:
            # A later notebook/conversation lookup can reuse its own completed
            # branch without retaining a second combined archive-sized pool.
            for branch in branches:
                yield from self._vectors([branch],ledger)
            return
        key = tuple((where, tuple(params)) for where, params in branches)
        if key in self._vector_pools:
            self._vector_pools.move_to_end(key)
            yield from self._vector_pools[key][0]
            return
        # Disjoint memory/history branches avoid SQLite's multi-index OR plan
        # reevaluating both owners' eligibility predicates for every vector.
        sql = ' UNION ALL '.join('''SELECT s.id,v.vector FROM recall_segments s
            JOIN recall_vectors v ON v.segment_id=s.id WHERE ('''+where+''')
            AND v.contract=? AND v.source_hash=s.source_hash''' for where, _ in branches)
        params = tuple(value for _, values in branches for value in (*values, CONTRACT))
        retained, size = [], 0
        ceiling = self.VECTOR_CACHE_BYTES
        cursor = self.store.memory.query(ledger, sql, params)
        try:
            for row in cursor:
                if time.monotonic() >= ledger.deadline:
                    raise Failure('timeout', 'Recall ranking reached the turn deadline.')
                try:
                    item = (row['id'], unpack_vector(row['vector']))
                except ValueError:
                    raise Failure('memory_index_unavailable', 'Invalid derived recall vector; no scan fallback ran.') from None
                size += len(row['vector']) + 192  # Vector plus conservative per-entry overhead.
                if size <= ceiling:
                    retained.append(item)
                else:
                    retained.clear()  # Oversized pools stream exactly, without retention.
                yield item
        finally:
            cursor.close()
        # Publish only a completely read pool, never a budget-interrupted prefix.
        if 0 < size <= ceiling and not self.db.in_transaction:
            while self._vector_pools and (len(self._vector_pools) >= 4 or self._pool_bytes + size > ceiling):
                _, (_, removed) = self._vector_pools.popitem(last=False)
                self._pool_bytes -= removed
            self._vector_pools[key] = (retained, size)
            self._pool_bytes += size

    def _ranked_rows(self, pairs, ledger, score_name):
        if not pairs:
            return []
        rows = self.store.memory.query(ledger, 'SELECT * FROM recall_segments WHERE id IN ('+
            ','.join('?' for _ in pairs)+')', tuple(ident for _, ident in pairs)).fetchall()
        if len(rows) != len(pairs):
            raise Failure('memory_index_unavailable', 'Recall candidates changed before resolution.')
        by_id = {row['id']: dict(row) for row in rows}
        return [dict(by_id[ident], **{score_name: score}) for score, ident in pairs]

    def _access(self, alias='s', *, exclude_running=True):
        # Both candidate retrieval routes use this exact grant predicate. A
        # conversation grant is not permission to read a different bank. The IN
        # set is materialized once, not a correlated lookup for every vector.
        access = f'''(({alias}.kind='memory' AND {alias}.bank_id=?) OR ({alias}.kind IN ('message','operation') AND {alias}.conversation_id IN
            (SELECT g.conversation_id FROM memory_grants g WHERE g.bank_id=? AND g.enabled=1)))'''
        return access + (f" AND ({alias}.kind!='message' OR {alias}.source_id NOT IN (SELECT user_id FROM recall_turns WHERE status='running'))" if exclude_running else '')

    def automatic_query(self, snapshot, question, ledger):
        previous=[]
        if needs_context(question):
            mem=self.store.memory
            with mem.bounded(ledger):
                mem._check(snapshot,ledger)
                rows=mem.query(ledger,'''SELECT h.artifact_id FROM history_sources h JOIN recall_turns t ON t.id=h.turn_id
                    WHERE h.conversation_id=? AND h.role='user' AND t.status!='running'
                    ORDER BY h.id DESC LIMIT 2''',(snapshot.conversation_id,)).fetchall()
                for row in rows:
                    source=self._source(snapshot,'message',row[0],ledger)
                    if source:
                        previous.append(self._body(snapshot,source,ledger))
        return query_for(question,previous)

    def _source(self, snapshot, kind, ident, ledger):
        mem = self.store.memory
        if kind=='operation':
            parts=ident.split(':')
            if len(parts)!=2 or len(parts[0])!=32 or not parts[1].isdecimal():
                return None
            row=mem.query(ledger,'''SELECT o.*,t.conversation_id,t.created_at FROM history_operations o
                JOIN recall_turns t ON t.id=o.turn_id JOIN memory_grants g ON g.conversation_id=t.conversation_id
                WHERE o.turn_id=? AND o.ordinal=? AND t.status!='running' AND g.bank_id=? AND g.enabled=1''',
                (parts[0],int(parts[1]),snapshot.bank_id)).fetchone()
            if row is None: return None
            body=encoded(dict(row))
            return dict(kind=kind,source_id=ident,record_id=ident,bank_id=None,conversation_id=row['conversation_id'],
                source_hash=hashlib.sha256(body.encode()).hexdigest(),role='tool',created_at=row['created_at'],
                body=body,bytes=len(body.encode()))
        if kind == 'memory':
            entry = mem.query(ledger, 'SELECT record_id FROM memory_revisions WHERE id=?', (ident,)).fetchone()
            if entry is None:
                return None
            row = mem._revision(snapshot, entry[0], ident, ledger)
            meta = mem._meta(snapshot, row['payload_id'], 'memory', ledger)
            fields=self.store.notes.metadata(snapshot,ident,ledger) if row['owned_note'] is not None else {}
            if row['historical_note'] is not None:
                fields=self.store.historical_notes.metadata(snapshot,ident,ledger) or {}
            return dict(kind=kind, source_id=ident, record_id=row['record_id'], bank_id=snapshot.bank_id,
                conversation_id=None, source_hash=meta['sha256'], role=None, created_at=row['created_at'], meta=meta,
                label=' '.join([fields.get('title',''),*fields.get('tags',[]),fields.get('preference_scope','')]).strip())
        row = mem.query(ledger, '''SELECT h.*,a.sha256,a.bytes,a.kind FROM history_sources h
            JOIN artifacts a ON a.id=h.artifact_id JOIN recall_turns t ON t.id=h.turn_id
            JOIN memory_grants g ON g.conversation_id=h.conversation_id
            WHERE h.artifact_id=? AND g.bank_id=? AND g.enabled=1
            AND h.conversation_id=t.conversation_id AND h.sequence=t.sequence
            AND (h.role='user' AND a.kind='user_message' AND h.artifact_id=t.user_id OR
                 h.role='assistant' AND a.kind='assistant_message' AND t.status='completed' AND h.artifact_id=t.answer_id OR
                 h.role='background' AND a.kind='background_result' AND EXISTS(
                     SELECT 1 FROM background_deliveries d WHERE d.result_id=h.artifact_id
                     AND d.conversation_id=h.conversation_id AND d.after_sequence=h.sequence AND d.status='presented'))''',
            (ident,snapshot.bank_id)).fetchone()
        if row is None:
            return None
        return dict(kind='message', source_id=ident, record_id=ident, bank_id=None,
            conversation_id=row['conversation_id'], source_hash=row['sha256'], role=row['role'],
            created_at=mem.query(ledger,'SELECT created_at FROM artifacts WHERE id=?',(ident,)).fetchone()[0] or None,
            bytes=row['bytes'], artifact_kind=row['kind'])

    def _body(self, snapshot, source, ledger):
        self.metrics['source_reads'] += 1
        if source['kind']=='operation':
            ledger.spend_payload(source['bytes'])
            return source['body']
        if source['kind'] == 'memory':
            meta = source['meta']
            raw = self.store.memory._bytes(meta,0,meta['bytes'],ledger)
            if hashlib.sha256(raw).hexdigest() != source['source_hash']:
                raise Failure('memory_integrity','Changed memory cannot be indexed.')
            return raw.decode()
        return self.store._history.body(source['source_id'],source['artifact_kind'],ledger,
            max(4*ledger.profile.context_limit,ledger.response_bytes))

    def refresh_changed(self, snapshot, ledger, limit=16):
        """Consume a bounded durable queue, never scan/hash unchanged sources."""
        mem = self.store.memory
        with mem.bounded(ledger):
            mem._check(snapshot,ledger)
            rows = mem.query(ledger, '''SELECT d.* FROM recall_dirty d WHERE
                d.kind='memory' AND EXISTS(SELECT 1 FROM memory_revisions v JOIN memory_records r ON r.id=v.record_id
                    WHERE v.id=d.source_id AND r.bank_id=?) OR
                d.kind='message' AND EXISTS(SELECT 1 FROM history_sources h JOIN memory_grants g ON g.conversation_id=h.conversation_id
                    WHERE h.artifact_id=d.source_id AND g.bank_id=? AND g.enabled=1)
                OR d.kind='operation' AND EXISTS(SELECT 1 FROM recall_turns t JOIN memory_grants g ON g.conversation_id=t.conversation_id
                    WHERE t.id=substr(d.source_id,1,32) AND g.bank_id=? AND g.enabled=1)
                ORDER BY d.kind,d.source_id LIMIT ?''',(snapshot.bank_id,snapshot.bank_id,snapshot.bank_id,limit)).fetchall()
            with self.db:
                for item in rows:
                    try:
                        source = self._source(snapshot,item['kind'],item['source_id'],ledger)
                    except Failure as error:
                        if error.code != 'memory_unavailable':
                            raise
                        source = None
                    mem.query(ledger,'DELETE FROM recall_segments WHERE kind=? AND source_id=?',tuple(item))
                    if source:
                        body = self._body(snapshot,source,ledger)
                        for offset,part in segments(body):
                            mem.query(ledger,'''INSERT INTO recall_segments
                                (kind,source_id,record_id,bank_id,conversation_id,source_hash,offset,body,role,created_at,label)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (source['kind'],source['source_id'],source['record_id'],
                                source['bank_id'],source['conversation_id'],source['source_hash'],offset,part,source['role'],source['created_at'],source.get('label','')))
                        self.metrics['segmented_sources'] += 1
                        if source['kind']=='operation':
                            mem.query(ledger,"DELETE FROM recall_pending WHERE segment_id IN (SELECT id FROM recall_segments WHERE kind='operation' AND source_id=?)",(source['source_id'],))
                    mem.query(ledger,'DELETE FROM recall_dirty WHERE kind=? AND source_id=?',tuple(item))

    async def prepare(self, snapshot, query, ledger):
        self.refresh_changed(snapshot,ledger)
        if not self.embedding or time.monotonic() < self.retry_at:
            return
        mem = self.store.memory
        with mem.bounded(ledger):
            # Queue first is intentional: SQLite may otherwise scan every
            # segment to discover that the pending queue is empty.
            rows = mem.query(ledger,'''SELECT s.* FROM recall_pending p CROSS JOIN recall_segments s ON s.id=p.segment_id
                WHERE '''+self._access()+''' ORDER BY s.id LIMIT 15''',(snapshot.bank_id,snapshot.bank_id)).fetchall()
            available = bool(rows) or bool(mem.query(ledger,'SELECT EXISTS(SELECT 1 FROM recall_segments s WHERE '+
                self._access()+')',(snapshot.bank_id,snapshot.bank_id)).fetchone()[0])
        key = query_text(query)
        query_needed = available and bool(query.strip()) and key not in self.queries
        texts = [document_text(row['body'], row['label']) for row in rows]
        if query_needed:
            texts.append(key)
        if not texts:
            return
        try:
            values = await self.embedding.embed(texts, deadline=ledger.deadline)
            if len(values) != len(texts):
                raise ValueError('Embedding count mismatch')
            vectors = [vector_bytes(value) for value in values]
        except EmbeddingNotReady:
            self.unavailable = True
            self.retry_at = time.monotonic()+1
            return  # Idle initialization owns the slow work; no chat retry.
        except (ValueError,OSError,TimeoutError):
            self.unavailable = True
            self.retry_at = time.monotonic()+60
            return  # Lexical retrieval still works; cancellation is not swallowed.
        self.unavailable = False
        with mem.bounded(ledger):
            mem._check(snapshot,ledger)  # No transaction held over inference.
            with self.db:
                for row,vector in zip(rows,vectors):
                    live = mem.query(ledger,'SELECT source_hash FROM recall_segments WHERE id=?',(row['id'],)).fetchone()
                    source = self._source(snapshot,row['kind'],row['source_id'],ledger)
                    if live is None or not source or live[0] != row['source_hash'] or source['source_hash'] != row['source_hash']:
                        continue  # Changed/deleted sources cannot publish old vectors.
                    mem.query(ledger,'INSERT OR REPLACE INTO recall_vectors VALUES(?,?,?,?)',
                        (row['id'],row['source_hash'],CONTRACT,vector))
                    mem.query(ledger,'DELETE FROM recall_pending WHERE segment_id=?',(row['id'],))
                    self.metrics['embedded_segments'] += 1
        if query_needed:
            self.queries[key] = vectors[-1]
            self.metrics['query_embeddings'] += 1
            while len(self.queries)>128:
                self.queries.popitem(last=False)

    def _candidates(self,snapshot,args,ledger):
        mem = self.store.memory
        self._candidate_context(snapshot, ledger)
        vector = self.queries.get(query_text(args['query']))
        ranking_key = (encoded({k:args[k] for k in sorted(args) if k!='cursor'}), vector)
        if ranking_key in self._rankings:
            self._rankings.move_to_end(ranking_key)
            return self._rankings[ranking_key]
        scope = args.get('scope','all')
        memory_where, memory_params = "s.bank_id=? AND s.kind='memory'", [snapshot.bank_id]
        history_where = """s.kind IN ('message','operation') AND s.conversation_id IN
            (SELECT g.conversation_id FROM memory_grants g WHERE g.bank_id=? AND g.enabled=1)"""
        history_params = [snapshot.bank_id]
        # Normal current messages have no derived segment yet. Avoid charging
        # a per-vector exclusion lookup when the running set has no candidates.
        running = [] if scope in ('notebook','memories','tools') else mem.query(ledger,"""SELECT t.user_id FROM recall_turns t WHERE t.status='running'
            AND EXISTS(SELECT 1 FROM recall_segments live WHERE live.kind='message' AND live.source_id=t.user_id)""").fetchall()
        if running:
            history_where += " AND (s.kind!='message' OR s.source_id NOT IN ("+','.join('?' for _ in running)+'))'
            history_params.extend(row[0] for row in running)
        # Resolve eligibility once per indexed revision, not again for every
        # segment/vector. IN's uncorrelated subquery builds a compact identity
        # set; originals still verify every returned result in _item/_revision.
        # This matters for imported prose: retained old revisions have no index
        # entry and cannot inflate this ordinary discovery inventory.
        eligible = '''SELECT v.id FROM memory_index i
            JOIN memory_revisions v ON v.id=i.revision_id
            JOIN memory_records r ON r.id=v.record_id
            WHERE i.bank_id=? AND r.bank_id=i.bank_id AND v.readable=1
            AND r.readable=1 AND r.validity IN ('current','superseded')'''
        if scope=='notebook':
            eligible += ' AND (EXISTS(SELECT 1 FROM note_records n WHERE n.record_id=r.id) OR EXISTS(SELECT 1 FROM historical_notes h WHERE h.record_id=r.id))'
            memory_where += ' AND (EXISTS(SELECT 1 FROM note_records n WHERE n.record_id=s.record_id) OR EXISTS(SELECT 1 FROM historical_notes h WHERE h.record_id=s.record_id))'
        preference_mode = args.get('_preference_mode')
        if preference_mode:
            # Filter before the candidate cap. Otherwise unrelated notes can
            # crowd preferences out, or ordinary recall can bypass their gate.
            predicate = "EXISTS(SELECT 1 FROM note_metadata p WHERE p.revision_id=r.head_revision AND COALESCE(json_extract(p.body,'$.preference_scope'),'')!='')"
            test = predicate if preference_mode=='only' else 'NOT '+predicate
            eligible += ' AND '+test
            memory_where += ' AND EXISTS(SELECT 1 FROM memory_records r WHERE r.id=s.record_id AND '+test+')'
            if preference_mode=='only':
                eligible += " AND r.validity='current' AND v.id=r.head_revision"
                memory_where += " AND EXISTS(SELECT 1 FROM memory_records r WHERE r.id=s.record_id AND r.validity='current' AND r.head_revision=s.source_id)"
        if args.get('collection') in ('current','historical'):
            membership = 'NOT EXISTS' if args['collection']=='current' else 'EXISTS'
            eligible += ' AND '+membership+'(SELECT 1 FROM historical_notes h WHERE h.record_id=r.id)'
            memory_where += ' AND '+membership+'(SELECT 1 FROM historical_notes h WHERE h.record_id=s.record_id)'
        if scope=='conversations':
            history_where += " AND s.kind='message'"
        elif scope=='tools':
            history_where += " AND s.kind='operation'"
        if args['mode']=='current' and not (args.get('include_rejected',False) or args.get('flag')=='user-rejected'):
            eligible += ' AND NOT EXISTS(SELECT 1 FROM historical_notes h WHERE h.record_id=r.id AND h.rejected=1)'
            memory_where += ' AND NOT EXISTS(SELECT 1 FROM historical_notes h WHERE h.record_id=s.record_id AND h.rejected=1)'
            # Current head controls eligibility for EVERY retained revision's
            # candidate; an old unflagged body must not reintroduce a rejected note.
            eligible += """ AND NOT EXISTS(SELECT 1 FROM note_metadata reject_meta,
                json_each(reject_meta.body,'$.flags') flag
                WHERE reject_meta.revision_id=r.head_revision AND flag.value='user-rejected')"""
            memory_where += """ AND NOT EXISTS(SELECT 1 FROM memory_records rejected
                JOIN note_metadata reject_meta ON reject_meta.revision_id=rejected.head_revision,
                json_each(reject_meta.body,'$.flags') flag WHERE rejected.id=s.record_id AND flag.value='user-rejected')"""
        # Memory eligibility belongs inside the memory branch. Conversation and
        # tool candidates need only their grant, not per-segment note predicates.
        # Keyword candidates are already narrow; materializing the entire note
        # inventory there would cost more than checking each matching identity.
        memory_where += ''' AND EXISTS(SELECT 1 FROM memory_revisions v
            JOIN memory_records r ON r.id=v.record_id WHERE v.id=s.source_id AND v.readable=1
            AND r.readable=1 AND r.validity IN ('current','superseded'))'''
        vector_memory="s.bank_id=? AND s.kind='memory' AND s.source_id IN ("+eligible+')'
        vector_memory_params=(snapshot.bank_id,snapshot.bank_id)
        if scope in ('notebook','memories'):
            where,params = memory_where,memory_params
            branches = [(vector_memory,vector_memory_params)]
            chronological = [(memory_where,tuple(memory_params))]
        elif scope in ('conversations','tools'):
            where,params = history_where,history_params
            branches = [(history_where, tuple(history_params))]
            chronological = [(history_where,tuple(history_params))]
        else:
            where,params = '('+memory_where+') OR ('+history_where+')',memory_params+history_params
            chronological = [(memory_where,tuple(memory_params)),(history_where,tuple(history_params))]
            # Operation receipts are keyword-only: refresh_changed never queues
            # their embeddings. Do not scan that entire archive for absent vectors.
            branches = [(vector_memory,vector_memory_params),
                (history_where+" AND s.kind='message'",tuple(history_params))]
        where = '('+where+')'
        base_where, base_params = where, len(params)
        if any(k in args for k in ('purpose','tag','tags','thread_status','flag')):
            # Filter the current head's organisation in ordinary mode. An old
            # revision cannot make a closed thread appear open again.
            revision='s.source_id' if args['mode']=='historical' else 'r.head_revision'
            filter_start=len(params)
            predicates=[]
            for field in ('purpose','thread_status'):
                if field in args:
                    predicates.append("json_extract(n.body,'$."+field+"')=?")
                    params.append(args[field])
            if 'tag' in args:
                predicates.append("EXISTS(SELECT 1 FROM json_each(n.body,'$.tags') tag WHERE note_tag_key(tag.value)=note_tag_key(?))")
                params.append(args['tag'])
            if 'tags' in args:
                tests=["EXISTS(SELECT 1 FROM json_each(n.body,'$.tags') tag WHERE note_tag_key(tag.value)=note_tag_key(?))" for _ in args['tags']]
                predicates.append('('+(' AND ' if args.get('tag_mode','any')=='all' else ' OR ').join(tests)+')')
                params.extend(args['tags'])
            if 'flag' in args:
                predicates.append("EXISTS(SELECT 1 FROM json_each(n.body,'$.flags') flag WHERE flag.value=?)")
                params.append(args['flag'])
            ordinary="EXISTS(SELECT 1 FROM memory_records r JOIN note_metadata n ON n.revision_id="+revision+" WHERE r.id=s.record_id AND "+' AND '.join(predicates)+')'
            historical="EXISTS(SELECT 1 FROM memory_records r JOIN historical_note_revisions n ON n.revision_id="+revision+" WHERE r.id=s.record_id AND "+' AND '.join(predicates).replace('n.body','n.metadata').replace("'$.flags'","'$.tags'")+')'
            params.extend(params[filter_start:])
            where += " AND s.kind='memory' AND ("+ordinary+' OR '+historical+')'
        if args.get('conversation_id'):
            where += ''' AND (s.conversation_id=? OR s.kind='memory' AND EXISTS
                (SELECT 1 FROM note_revisions n LEFT JOIN recall_turns t ON t.id=n.turn_id
                 LEFT JOIN note_job_origins o ON o.bank_id=n.bank_id AND o.actor=n.actor AND o.operation_key=n.operation_key
                 LEFT JOIN background_runs run ON run.id=o.run_id
                 LEFT JOIN background_job_versions j ON j.job_id=run.job_id AND j.revision=run.revision
                 WHERE n.revision_id=s.source_id AND
                 (t.conversation_id=? OR n.turn_id IS NULL AND json_extract(j.definition,'$.conversation_id')=?)))'''
            params.extend([args['conversation_id']]*3)
        for column in ('role',):
            if args.get(column):
                where += ' AND s.'+column+'=?'; params.append(args[column])
        for column,op in (('since','>='),('until','<')):
            if args.get(column):
                where += ' AND s.created_at'+op+'?'; params.append(args[column])
        phrase = keywords(args['query'])
        limit = max(40,args.get('limit',8)*4)
        preference_ids = None
        if preference_mode == 'only':
            # Start at current preference heads, then use the source index to
            # locate their segments. The full eligibility predicate still applies.
            # Materialization prevents the planner starting at the global FTS
            # matches or all historical note segments before rejecting them.
            preference_ids = '''WITH heads AS MATERIALIZED (
                SELECT r.head_revision FROM memory_records r
                JOIN note_metadata p ON p.revision_id=r.head_revision
                WHERE r.bank_id=? AND r.validity='current'
                AND COALESCE(json_extract(p.body,'$.preference_scope'),'')!='')
                SELECT s.id FROM heads h CROSS JOIN recall_segments s INDEXED BY recall_source
                ON s.kind='memory' AND s.source_id=h.head_revision WHERE '''+where
            preference_params = (snapshot.bank_id,*params)
        if phrase and preference_ids is not None:
            # Probe the SAME FTS index by exact id, keeping its global BM25
            # statistics and the complete (score,id) ordering, including ties.
            rows = mem.query(ledger, 'WITH eligible AS MATERIALIZED ('+preference_ids+''')
                SELECT s.id,f.rank AS distance FROM eligible s CROSS JOIN recall_fts f
                ON f.rowid=s.id WHERE recall_fts MATCH ? ORDER BY distance,s.id LIMIT ?''',
                (*preference_params,phrase,limit)).fetchall()
            lexical = self._ranked_rows([(row['distance'],row['id']) for row in rows], ledger, 'distance')
        elif phrase:
            cursor = mem.query(ledger,'''SELECT s.id,f.rank AS distance FROM recall_segments s
                JOIN recall_fts f ON f.rowid=s.id WHERE '''+where+''' AND recall_fts MATCH ?
                ORDER BY f.rank''',(*params,phrase))
            def prefix():
                cutoff, count = None, 0
                try:
                    for row in cursor:
                        if cutoff is not None and row['distance'] > cutoff:
                            break
                        yield row['distance'], row['id']
                        count += 1
                        if count == limit:
                            cutoff = row['distance']
                finally:
                    cursor.close()
            # FTS5 streams by rank. Include the entire boundary tie so the old
            # (bm25,id) ordering is exact; retain only bounded winning identities.
            lexical = self._ranked_rows(heapq.nsmallest(limit, prefix()), ledger, 'distance')
        else:
            # Stream compact identities through a bounded heap. Splitting the
            # disjoint owners avoids testing note metadata on every history row;
            # only winning identities are retained before their bodies are read.
            if preference_ids is not None:
                chronological = [('s.id IN ('+preference_ids+')',preference_params)]
            else:
                chronological = [(part+where[len(base_where):],(*values,*params[base_params:]))
                    for part,values in chronological]
            def dated():
                for part,values in chronological:
                    cursor = mem.query(ledger,'SELECT s.id,s.created_at FROM recall_segments s WHERE ('+part+')',values)
                    try:
                        for row in cursor:
                            if time.monotonic() >= ledger.deadline:
                                raise Failure('timeout', 'Recall ranking reached the turn deadline.')
                            yield row
                    finally:
                        cursor.close()
            ascending = args.get('order')=='asc'
            # Match SQLite's NULL/text order, reversing the date but not the id.
            key = lambda row: (row['created_at'] is not None,row['created_at'] or '',
                row['id'] if ascending else -row['id'])
            selected = (heapq.nsmallest if ascending else heapq.nlargest)(limit,dated(),key=key)
            lexical = self._ranked_rows([(None,row['id']) for row in selected],ledger,'distance')
        semantic = []
        if scope!='tools' and args['query'].strip() and args.get('order','relevance')=='relevance' and vector is not None:
            if preference_ids is not None:
                branches = [('s.id IN ('+preference_ids+')',preference_params)]
            else:
                branches = [(part+where[len(base_where):], (*values, *params[base_params:])) for part, values in branches]
            try:
                query_vector = unpack_vector(vector)
            except ValueError:
                raise Failure('memory_index_unavailable', 'Invalid query vector; no scan fallback ran.') from None
            def scores():
                for ident, values in self._vectors(branches, ledger):
                    if time.monotonic() >= ledger.deadline:
                        raise Failure('timeout', 'Recall ranking reached the turn deadline.')
                    # Same float32 values and Python summation as similarity(),
                    # without unpacking/revalidating both vectors per comparison.
                    score = sum(x*y for x,y in zip(values,query_vector))
                    yield -score, ident
            winners = heapq.nsmallest(limit, scores())
            semantic = self._ranked_rows([(-score, ident) for score, ident in winners], ledger, 'score')
        ranked = fuse(lexical,semantic,historical=args['mode']=='historical')
        if preference_mode=='only':
            scores = {row['source_id']:row['score'] for row in semantic}
            for item in ranked:
                item['semantic_score'] = scores.get(item['row']['source_id'])
        if args.get('order','relevance') != 'relevance' or not args['query'].strip():
            ranked.sort(key=lambda r:(r['row']['created_at'] or '',r['row']['source_id']),reverse=args.get('order')!='asc')
        result = ranked,bool(semantic)
        if not self.db.in_transaction:
            self._rankings[ranking_key] = result
            while len(self._rankings) > 8:
                self._rankings.popitem(last=False)
        return result

    def _item(self,snapshot,row,mode,ledger,key,complete_messages=None):
        mem = self.store.memory
        source = self._source(snapshot,row['kind'],row['source_id'],ledger)
        if source is None:
            return None
        if source['source_hash'] != row['source_hash']:
            raise Failure('memory_index_unavailable','Recall projection is stale; no scan fallback ran.')
        offset = row['offset']
        if row['kind']=='memory':
            original = mem._revision(snapshot,row['record_id'],row['source_id'],ledger)
            current = mem._resolve(snapshot,original,ledger)
            target = current if mode=='current' else original
            if target['id'] != original['id']:
                offset = 0
            meta = mem._meta(snapshot,target['payload_id'],'memory',ledger)
            page = mem._page(meta,offset,128,ledger)
            item = dict(record_id=target['record_id'],revision_id=target['id'],
                read_ref=mem._reference(snapshot,key,'m',mode,target),sha256=meta['sha256'],total_bytes=meta['bytes'],
                preview=page['content'],preview_offset=offset,preview_end=page['end'])
            if target['owned_note'] is not None:
                item['record_kind']='authored_note'
                details=self.store.notes.metadata_summary(snapshot,target['id'],ledger)
                if details: item['note']=details
            if target['historical_note'] is not None:
                item.update(record_kind='historical_note',historical_note=self.store.historical_notes.summary(snapshot,target['id'],ledger))
        else:
            body = self._body(snapshot,source,ledger)
            page = exact_page(source['source_id'],body,offset,128).envelope()
            item = dict(record_id=source['record_id'],revision_id=source['source_id'],
                read_ref=mem._token(snapshot,key,['o' if row['kind']=='operation' else 'h',mode,source['conversation_id'],source['source_id'],snapshot.archive_epoch]),
                sha256=source['source_hash'],total_bytes=source['bytes'],preview=page['content'],
                preview_offset=offset,preview_end=page['end'],record_kind='tool_record' if row['kind']=='operation' else 'conversation_message',
                conversation_id=source['conversation_id'],role=source['role'],created_at=source['created_at'],
                **source_info(self.store,source['source_id'],ledger))
            # Keep the original discovery page while selecting hits. If this
            # indexed segment is the whole message, retain its already-verified
            # original for optional expansion after selection. No new source IO,
            # ranking change, or extra context/window allowance is introduced.
            if (complete_messages is not None and row['kind']=='message'
                    and offset==0 and source['bytes']==len(row['body'].encode())
                    and page['end']<source['bytes']):
                complete_messages[(item['record_id'],item['revision_id'])] = dict(
                    item, preview=body, preview_end=source['bytes'])
        return item

    @staticmethod
    def _expand_messages(result, complete_messages, fits):
        # Expansion must not displace another discovered hit or change its
        # cursor. Admit only whole originals through the same result budget.
        # A message that does not fit keeps its exact, visibly partial preview.
        for index, item in enumerate(result['results']):
            complete = complete_messages.get((item['record_id'],item['revision_id']))
            if complete is None:
                continue
            items = list(result['results'])
            items[index] = complete
            candidate = dict(result, results=items)
            if fits(candidate):
                result = candidate
        return result

    def automatic(self, snapshot, args, ledger, maximum):
        """Two eligibility lanes, one existing automatic-recall allowance.

        No new inference call or standing preference list. Whole preference
        notes get first consideration; ordinary discovery uses remaining room.
        Explicit searches never call this policy and keep their normal cursors.
        """
        strength = self.store.memory.preference_settings.read()['strength']
        base = {k:v for k,v in args.items() if not k.startswith('_')}
        preference_args = dict(base, scope='notebook', collection='current', limit=MAX_PREFERENCES,
            _preference_mode='only', _preference_strength=strength,
            _preference_question=args['_preference_question'])
        selected = []
        if strength != 'off':
            preferences = self.search(snapshot, preference_args, ledger, maximum)
            selected = preferences['results']
        complete_messages = {}
        ordinary = self.search(snapshot, dict(base, _preference_mode='exclude'), ledger, maximum,
            complete_messages=complete_messages)
        result = dict(ordinary, results=[], delivered_count=0, next_cursor=None, status='complete')
        result.pop('reason', None)
        result['retrieval'] = dict(ordinary['retrieval'], preference_strength=strength,
            preference_count=0, selection='bounded_automatic')
        # These internal searches are not publicly continuable. Missing items
        # can be rediscovered through the ordinary explicit search tool.
        for item in selected + ordinary['results']:
            candidate = dict(result, results=result['results']+[item], delivered_count=result['delivered_count']+1,
                retrieval=dict(result['retrieval'], preference_count=result['retrieval']['preference_count']+
                    int(item.get('complete_preference',False))))
            if len(encoded(candidate).encode()) <= maximum:
                result = candidate
        if not result['results'] and ordinary.get('reason'):
            result.update(status='not_delivered', reason=ordinary['reason'])
        # Preference/ordinary selection retains its existing priority and
        # breadth. Only unused room in the final combined result is expandable.
        result = self._expand_messages(result, complete_messages,
            lambda candidate: len(encoded(candidate).encode())<=maximum)
        self.store.memory.check(snapshot, ledger)
        return result

    def _preference(self, snapshot, item, ranked, args, ledger):
        details = item.get('note', {})
        scope = details.get('preference_scope','')
        if (not scope or item['revision_id'] != ranked['row']['source_id']
                or item['total_bytes'] > MAX_PREFERENCE_BYTES
                or details.get('purpose') != 'about_user'
                or details.get('basis') not in ('user_statement','user_summary')
                or not applies(args['_preference_question'], scope, args['_preference_strength'], ranked.get('semantic_score'))):
            return None
        mem = self.store.memory
        fields = self.store.notes.metadata(snapshot, item['revision_id'], ledger)
        if not fields.get('attribution'):
            return None
        original = mem._revision(snapshot, item['record_id'], item['revision_id'], ledger)
        meta = mem._meta(snapshot, original['payload_id'], 'memory', ledger)
        page = mem._page(meta, 0, MAX_PREFERENCE_BYTES, ledger)
        if page['status'] != 'complete':
            return None
        # Reuse the exact source preview field: audit receipts omit its body,
        # removal keeps one body owner, and this grants no note-edit authority.
        return dict(item, preview=page['content'], preview_offset=0, preview_end=page['end'], complete_preference=True)

    def search(self,snapshot,args,ledger,maximum,*,budget=None,complete_messages=None):
        mem = self.store.memory
        expand = complete_messages is None
        if complete_messages is None:
            complete_messages = {}
        def fits(result):
            return len(encoded(result).encode())<=maximum and (budget is None or budget.fits(result))
        with mem.bounded(ledger):
            bank=mem._check(snapshot,ledger)
            if bank['index_epoch'] != snapshot.epoch:
                raise Failure('memory_index_unavailable','Memory index is stale; no scan fallback ran.')
            # Bind query values, not the model's JSON object-key order; keep
            # authority, ranking and exact cursor authentication unchanged.
            query_hash=hashlib.sha256(encoded({k:args[k] for k in sorted(args) if k!='cursor'}).encode()).hexdigest()
            # Authenticate paging before ranking. A fabricated/stale cursor
            # cannot justify spending the run's discovery allowance.
            value = None
            if args['cursor']:
                value=mem._decode(snapshot,bank['handle_key'],args['cursor'])
                if (not isinstance(value,list) or len(value)!=5 or value[0:2]!=['r',query_hash]
                        or value[3]!=snapshot.archive_epoch or type(value[4]) is not int or value[4]<0):
                    raise Failure('invalid_memory_reference','Recall cursor changed; rediscover rather than skipping results.')
            ranked,semantic = self._candidates(snapshot,args,ledger)
            ranking_hash=hashlib.sha256(encoded([(i['row']['id'],i['score']) for i in ranked]).encode()).hexdigest()
            position=0
            if args['cursor']:
                if (not isinstance(value,list) or len(value)!=5 or value[:4]!=['r',query_hash,ranking_hash,snapshot.archive_epoch]
                        or type(value[4]) is not int or not 0<=value[4]<=len(ranked)):
                    raise Failure('invalid_memory_reference','Recall cursor changed; rediscover rather than skipping results.')
                position=value[4]
            def cursor(pos):
                return mem._token(snapshot,bank['handle_key'],['r',query_hash,ranking_hash,snapshot.archive_epoch,pos])
            result=dict(status='complete',mode=args['mode'],previews_only=True,results=[],delivered_count=0,next_cursor=None,
                retrieval=dict(semantic_used=semantic,semantic_status='unavailable' if self.unavailable else
                    'enabled' if self.embedding else 'disabled',candidate_limit=max(40,args.get('limit',8)*4)))
            result['retrieval']['indexing_pending']=bool(mem.query(ledger,'''SELECT EXISTS(SELECT 1 FROM recall_dirty d WHERE
                d.kind='memory' AND EXISTS(SELECT 1 FROM memory_revisions v JOIN memory_records r ON r.id=v.record_id
                    WHERE v.id=d.source_id AND r.bank_id=?) OR
                d.kind IN ('message','operation') AND EXISTS(SELECT 1 FROM memory_grants g
                    JOIN history_sources h ON h.conversation_id=g.conversation_id
                    WHERE (h.artifact_id=d.source_id OR h.turn_id=substr(d.source_id,1,32)) AND g.bank_id=? AND g.enabled=1))''',
                (snapshot.bank_id,snapshot.bank_id)).fetchone()[0])
            result['retrieval']['embedding_pending']=bool(mem.query(ledger,'''SELECT EXISTS(SELECT 1 FROM recall_pending p
                CROSS JOIN recall_segments s ON s.id=p.segment_id WHERE '''+self._access()+')',
                (snapshot.bank_id,snapshot.bank_id)).fetchone()[0])
            # A finite ranked pool is explicit, not a claim that the archive has
            # no other matches. Cursor pages cover that stable pool only.
            seen=set(); positions=[]
            for index in range(position,len(ranked)):
                if len(result['results'])>=args.get('limit',8):
                    result.update(status='page',next_cursor=cursor(index)); break
                try:
                    item=self._item(snapshot,ranked[index]['row'],args['mode'],ledger,bank['handle_key'],complete_messages)
                except Failure as error:
                    if error.code!='memory_unavailable': raise
                    item=None
                if item is None or (item['record_id'],item['revision_id']) in seen:
                    continue
                # Resolution can lead to a different record, so recheck flags on
                # the final authoritative target, not just the ranked candidate.
                if (args['mode']=='current' and not (args.get('include_rejected',False) or args.get('flag')=='user-rejected')
                        and 'user-rejected' in item.get('note',{}).get('flags',[])):
                    continue
                details=item.get('note',{})
                if args.get('_preference_mode')=='exclude' and details.get('preference_scope'):
                    continue  # Supersession cannot bypass the preference gate.
                if args.get('_preference_mode')=='only':
                    item = self._preference(snapshot, item, ranked[index], args, ledger)
                    if item is None:
                        continue
                if item.get('record_kind')=='historical_note' and any(k in args for k in ('purpose','thread_status','tag','tags','flag')):
                    details=self.store.historical_notes.metadata(snapshot,item['revision_id'],ledger) or {}
                    # Preserve historical markers for explicit filtering only;
                    # these labels never enter the active topic/flag catalogue.
                    details=dict(details,flags=details.get('tags',[]))
                # Supersession may resolve a candidate to a different record.
                # Filters describe the delivered target, not its predecessor.
                if any(k in args and details.get(k)!=args[k] for k in ('purpose','thread_status')):
                    continue
                if 'flag' in args and args['flag'] not in details.get('flags',[]):
                    continue
                topic_keys={tag_key(tag) for tag in details.get('tags',[])}
                wanted=[tag_key(tag) for tag in args.get('tags',([args['tag']] if 'tag' in args else []))]
                if wanted and not (all(tag in topic_keys for tag in wanted) if args.get('tag_mode')=='all'
                        else any(tag in topic_keys for tag in wanted)):
                    continue
                more=index+1<len(ranked)
                candidate=dict(result,results=result['results']+[item],delivered_count=len(result['results'])+1,
                    status='page' if more else 'complete',next_cursor=cursor(index+1) if more else None)
                if not fits(candidate):
                    result.update(status='page' if result['results'] else 'not_delivered',next_cursor=cursor(index),reason='result_capacity')
                    break
                result=candidate; positions.append(index+1); seen.add((item['record_id'],item['revision_id']))
            while result['results'] and not fits(result):
                result['results'].pop(); positions.pop()
                result.update(delivered_count=len(result['results']),status='page' if result['results'] else 'not_delivered',
                    next_cursor=cursor(positions[-1] if positions else position),reason='result_capacity')
            if expand:
                result = self._expand_messages(result, complete_messages, fits)
            mem._check(snapshot,ledger)
            return result

    def read_message(self,snapshot,value,args,ledger):
        if (len(value)!=5 or value[0] not in ('h','o') or value[1] not in ('current','historical')
                or value[4]!=snapshot.archive_epoch):
            raise Failure('invalid_memory_reference','Archive reference is stale or malformed.')
        source=self._source(snapshot,'operation' if value[0]=='o' else 'message',value[3],ledger)
        if not source or source['conversation_id']!=value[2]:
            raise Failure('memory_unavailable','Conversation source is not granted or available.')
        body=self._body(snapshot,source,ledger)
        result=exact_page(source['source_id'],body,args['offset'],args['max_bytes']).envelope()
        return dict(result,kind='tool_record' if value[0]=='o' else 'conversation_message',record_id=source['record_id'],revision_id=source['source_id'],
            conversation_id=source['conversation_id'],role=source['role'],created_at=source['created_at'],mode=value[1],
            **source_info(self.store,source['source_id'],ledger))
