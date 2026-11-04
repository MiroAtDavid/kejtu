"""Authoritative read-only memory resolver and bounded chunk reads.

FTS proposes identities, never final evidence. Fixture writers own index updates
outside turns. Handles bind bank/grant epochs without a growing handle table.
HMAC prevents model-forged references, not a malicious local database owner.
"""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import re

from ...core.memory import MemorySnapshot
from ...core.records import Failure
from .history import encoded
from . import memory_refs
from . import note_topics
from . import note_inventory

CHUNK_BYTES = 4096
MEMORY_SCHEMA = '''
CREATE TABLE memory_banks(id TEXT PRIMARY KEY, slot INTEGER NOT NULL UNIQUE,
 epoch INTEGER NOT NULL CHECK(epoch>=0), index_epoch INTEGER NOT NULL,
 handle_key BLOB NOT NULL CHECK(length(handle_key)=32));
INSERT INTO memory_banks VALUES(lower(hex(randomblob(16))),1,0,0,randomblob(32));
CREATE TABLE memory_grants(conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
 bank_id TEXT NOT NULL REFERENCES memory_banks(id), enabled INTEGER NOT NULL,
 epoch INTEGER NOT NULL CHECK(epoch>=0));
CREATE TABLE memory_payloads(id TEXT PRIMARY KEY, bank_id TEXT NOT NULL REFERENCES memory_banks(id),
 kind TEXT NOT NULL, bytes INTEGER NOT NULL CHECK(bytes>=0), sha256 TEXT NOT NULL);
CREATE TABLE memory_chunks(payload_id TEXT NOT NULL REFERENCES memory_payloads(id),
 ordinal INTEGER NOT NULL, sha256 TEXT NOT NULL, body BLOB NOT NULL,
 PRIMARY KEY(payload_id,ordinal));
CREATE TABLE memory_records(id TEXT PRIMARY KEY, bank_id TEXT NOT NULL REFERENCES memory_banks(id),
 head_revision TEXT NOT NULL, validity TEXT NOT NULL, readable INTEGER NOT NULL);
CREATE TABLE memory_revisions(sequence INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,
 record_id TEXT NOT NULL REFERENCES memory_records(id), payload_id TEXT NOT NULL REFERENCES memory_payloads(id),
 author TEXT CHECK(length(CAST(author AS BLOB))<=256),
 origin TEXT CHECK(length(CAST(origin AS BLOB))<=64),
 created_at TEXT CHECK(length(CAST(created_at AS BLOB))<=64), readable INTEGER NOT NULL);
CREATE INDEX memory_revision_owner ON memory_revisions(record_id,sequence);
CREATE TABLE memory_supersessions(old_id TEXT NOT NULL REFERENCES memory_records(id),
 target_id TEXT NOT NULL, PRIMARY KEY(old_id,target_id));
CREATE TABLE memory_sources(id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
 bank_id TEXT NOT NULL REFERENCES memory_banks(id), payload_id TEXT NOT NULL REFERENCES memory_payloads(id),
 kind TEXT NOT NULL, created_at TEXT CHECK(length(CAST(created_at AS BLOB))<=64), readable INTEGER NOT NULL);
CREATE TABLE memory_edges(id INTEGER PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES memory_revisions(id),
 source_revision_id TEXT NOT NULL, relation TEXT NOT NULL CHECK(length(CAST(relation AS BLOB))<=64));
CREATE INDEX memory_edge_owner ON memory_edges(revision_id,id);
CREATE TABLE memory_index(revision_id TEXT PRIMARY KEY REFERENCES memory_revisions(id),
 bank_id TEXT NOT NULL, sequence INTEGER NOT NULL UNIQUE, sha256 TEXT NOT NULL);
CREATE INDEX memory_index_bank ON memory_index(bank_id,sequence);
CREATE VIRTUAL TABLE memory_fts USING fts5(scope,body,content='',tokenize='unicode61 remove_diacritics 0');
'''

# Any authoritative fixture change invalidates issued handles and index freshness.
# Normal conversation/receipt writes touch none of these tables. These are schema
# safeguards, not a product mutation API; index publication remains explicit.
for _table, _bank in (
    ('memory_records', '{row}.bank_id'),
    ('memory_revisions', '(SELECT bank_id FROM memory_records WHERE id={row}.record_id)'),
    ('memory_sources', '{row}.bank_id'),
    ('memory_supersessions', '(SELECT bank_id FROM memory_records WHERE id={row}.old_id)'),
    ('memory_edges', '(SELECT r.bank_id FROM memory_revisions v JOIN memory_records r ON r.id=v.record_id WHERE v.id={row}.revision_id)'),
    ('memory_payloads', '{row}.bank_id'),
    ('memory_chunks', '(SELECT bank_id FROM memory_payloads WHERE id={row}.payload_id)'),
):
    for _event, _rows in (('INSERT', ('NEW',)), ('UPDATE', ('OLD', 'NEW')), ('DELETE', ('OLD',))):
        _banks = ','.join(_bank.format(row=row) for row in _rows)
        MEMORY_SCHEMA += f'''CREATE TRIGGER {_table}_{_event.lower()}_epoch AFTER {_event} ON {_table}
            BEGIN UPDATE memory_banks SET epoch=epoch+1 WHERE id IN ({_banks}); END;\n'''
MEMORY_SCHEMA += '''CREATE TRIGGER memory_grant_epoch AFTER UPDATE OF enabled,bank_id ON memory_grants
    BEGIN UPDATE memory_grants SET epoch=OLD.epoch+1 WHERE conversation_id=NEW.conversation_id; END;
'''


class MemoryStore:
    def automatic_query(self, snapshot, question, ledger):
        return self.store.recall.automatic_query(snapshot,question,ledger)

    def __init__(self, store):
        self.store, self.db = store, store.db
        from .preference_settings import PreferenceSettings
        self.preference_settings = PreferenceSettings(store)

    @contextmanager
    def bounded(self, ledger):
        # Reuse the existing SQL progress/deadline/partial-quantum owner. Nested
        # memory work always receives the caller's ledger, never a fresh one.
        try:
            with self.store._history.bounded(ledger):
                yield
        except Failure as error:
            if error.code == 'history_index_unavailable':
                raise Failure('memory_index_unavailable', 'Memory storage/index is unavailable; no scan fallback ran.') from None
            raise

    def query(self, ledger, sql, parameters=()):
        return self.store._history.query(ledger, sql, parameters)

    def _size_recall_work(self, ledger):
        # Storage reports population; the existing shared ledger owns allocation.
        # COUNT uses the index table, never source bodies, and runs once per turn.
        if ledger.recall_index_size is None:
            ledger.admit_recall_index(self.query(ledger, 'SELECT count(*) FROM recall_segments').fetchone()[0])

    def snapshot(self, conversation_id, bank_id, ledger):
        with self.bounded(ledger):
            row = self.query(ledger, '''SELECT b.epoch,g.epoch AS grant_epoch FROM memory_grants g
                JOIN memory_banks b ON b.id=g.bank_id
                WHERE g.conversation_id=? AND g.bank_id=? AND g.enabled=1''',
                (conversation_id, bank_id)).fetchone()
            if row is None:
                raise Failure('memory_unavailable', 'No active explicit memory grant is available.')
            hybrid = self.query(ledger, '''SELECT json_extract(p.body,'$.hybrid_memory') FROM conversations c
                JOIN artifact_payloads p ON p.artifact_id=c.config_id WHERE c.id=?''',(conversation_id,)).fetchone()[0] == 1
            archive_epoch = self.query(ledger,'SELECT epoch FROM recall_epoch').fetchone()[0] if hybrid else 0
            if hybrid:
                self._size_recall_work(ledger)
            return MemorySnapshot(conversation_id, bank_id, row['epoch'], row['grant_epoch'],hybrid,archive_epoch)

    def research_snapshot(self, conversation_id, bank_id, ledger):
        if not ledger.web_only:
            raise Failure('memory_source_changed', 'Append-safe recall belongs only to background research.')
        with self.bounded(ledger):
            snapshot = self.snapshot(conversation_id, bank_id, ledger)
            archive = self.query(ledger, 'SELECT epoch,(SELECT count(*) FROM history_sources) AS sources FROM recall_epoch').fetchone()
            return replace(snapshot, archive_epoch=archive['epoch'], archive_sources=archive['sources'])

    def job_snapshot(self, run_id, ledger):
        with self.bounded(ledger):
            grant = self.store.jobs.notebook_grant(run_id)
            bank = self.query(ledger, 'SELECT epoch FROM memory_banks WHERE id=?', (grant['bank_id'],)).fetchone()
            archive = self.query(ledger, 'SELECT epoch FROM recall_epoch').fetchone()[0]
            self._size_recall_work(ledger)
            return MemorySnapshot(None, grant['bank_id'], bank['epoch'], grant['epoch'], True, archive, run_id)

    def _check(self, snapshot, ledger):
        if snapshot.job_run_id is not None:
            grant = self.store.jobs.notebook_grant(snapshot.job_run_id)
            row = self.query(ledger, 'SELECT * FROM memory_banks WHERE id=? AND epoch=?',
                             (snapshot.bank_id, snapshot.epoch)).fetchone()
            if (row is None or snapshot.conversation_id is not None or not snapshot.hybrid
                    or grant['bank_id'] != snapshot.bank_id or grant['epoch'] != snapshot.grant_epoch
                    or self.query(ledger, 'SELECT epoch FROM recall_epoch').fetchone()[0] != snapshot.archive_epoch):
                raise Failure('memory_source_changed', 'Notebook or review authority changed; continuation stopped.')
            return row
        row = self.query(ledger, '''SELECT b.* FROM memory_banks b JOIN memory_grants g ON g.bank_id=b.id
            WHERE g.conversation_id=? AND b.id=? AND b.epoch=? AND g.epoch=? AND g.enabled=1''',
            (snapshot.conversation_id, snapshot.bank_id, snapshot.epoch, snapshot.grant_epoch)).fetchone()
        if row is None:
            raise Failure('memory_source_changed', 'Memory authority changed during this turn; no further call may send the frozen results.')
        if snapshot.hybrid:
            append_safe = ledger.web_only and snapshot.archive_sources is not None
            # One SQL statement observes epoch and count atomically. Two reads
            # could let a concurrent insertion conceal an intervening revocation.
            archive = self.query(ledger, 'SELECT epoch,(SELECT count(*) FROM history_sources) AS sources FROM recall_epoch'
                if append_safe else 'SELECT epoch FROM recall_epoch').fetchone()
            epoch = archive['epoch']
            if epoch != snapshot.archive_epoch:
                # recall_history_insert adds exactly one epoch and one source.
                # Every revocation/edit adds an epoch without adding a source;
                # deletion adds an epoch AND removes a source. Equality therefore
                # proves append-only growth, even if deletions are mixed with adds.
                # Retain the original epoch/handles: exact reads still resolve their
                # originals, and ranked paging still checks its ranking fingerprint.
                additions = archive['sources'] - snapshot.archive_sources if append_safe else -1
                if epoch <= snapshot.archive_epoch or epoch - snapshot.archive_epoch != additions:
                    raise Failure('memory_source_changed','Conversation archive authority changed; no frozen recall may be sent.')
        return row

    async def prepare(self, snapshot, query, ledger):
        if snapshot.hybrid:
            await self.store.recall.prepare(snapshot, query, ledger)

    def check(self, snapshot, ledger):
        with self.bounded(ledger):
            self._check(snapshot, ledger)

    def _token(self, snapshot, key, value):
        return memory_refs.legacy_token(snapshot, key, value)

    def _decode(self, snapshot, key, token):
        return memory_refs.decode(snapshot, key, token)

    def _reference(self, snapshot, key, kind, mode, row, source_revision=None):
        if kind in ('n','d'):
            return self._token(snapshot, key, [kind, mode, row['record_id'], row['id'], None])
        return memory_refs.reference(snapshot, key, [kind, mode, row['record_id'], row['id'], source_revision])

    def _revision(self, snapshot, record_id, revision_id, ledger, *, manual=False):
        row = self.query(ledger, '''SELECT v.*,r.bank_id,r.head_revision,r.validity,r.readable AS record_readable,
            n.record_id AS owned_note,h.record_id AS historical_note,h.rejected AS historical_rejected
            FROM memory_revisions v JOIN memory_records r ON r.id=v.record_id
            LEFT JOIN note_records n ON n.record_id=r.id AND n.bank_id=r.bank_id
            LEFT JOIN historical_notes h ON h.record_id=r.id AND h.bank_id=r.bank_id
            WHERE r.bank_id=? AND r.id=? AND v.id=?
            AND (? OR NOT EXISTS(SELECT 1 FROM excluded_memory_revisions e WHERE e.revision_id=v.id))''',
            (snapshot.bank_id, record_id, revision_id, int(manual))).fetchone()
        if row is None or row['record_readable'] != 1 or row['readable'] != 1 or row['validity'] not in ('current', 'superseded'):
            raise Failure('memory_unavailable', 'Memory is absent, ineligible or outside the supported read policy.')
        return row

    def _head(self, snapshot, record_id, ledger):
        row = self.query(ledger, 'SELECT head_revision FROM memory_records WHERE bank_id=? AND id=?',
                         (snapshot.bank_id, record_id)).fetchone()
        if row is None:
            raise Failure('memory_resolution_failed', 'A required replacement is unavailable; no current fact was inferred.')
        return self._revision(snapshot, record_id, row[0], ledger)

    def _resolve(self, snapshot, row, ledger):
        seen = set()
        current = self._head(snapshot, row['record_id'], ledger)
        while True:
            ident = current['record_id']
            if ident in seen:
                raise Failure('memory_resolution_failed', 'Supersession has a cycle; no current fact was inferred.')
            seen.add(ident)
            targets = self.query(ledger, 'SELECT target_id FROM memory_supersessions WHERE old_id=? LIMIT 2', (ident,)).fetchall()
            if current['validity'] == 'current' and not targets:
                return current
            if current['validity'] != 'superseded' or len(targets) != 1:
                raise Failure('memory_resolution_failed', 'Supersession is missing, ambiguous or inconsistent.')
            current = self._head(snapshot, targets[0][0], ledger)

    def _meta(self, snapshot, payload_id, kind, ledger):
        row = self.query(ledger, 'SELECT * FROM memory_payloads WHERE bank_id=? AND id=? AND kind=?',
                         (snapshot.bank_id, payload_id, kind)).fetchone()
        if (row is None or type(row['bytes']) is not int or not 0 <= row['bytes'] < 2**63
                or not re.fullmatch('[0-9a-f]{64}', row['sha256'])):
            raise Failure('memory_integrity', 'Memory payload metadata is missing or invalid.')
        return row

    def _bytes(self, meta, start, end, ledger):
        if start == end:
            return b''
        parts = []
        for ordinal in range(start // CHUNK_BYTES, (end + CHUNK_BYTES - 1) // CHUNK_BYTES):
            row = self.query(ledger, '''SELECT rowid,sha256,typeof(body) AS kind FROM memory_chunks
                WHERE payload_id=? AND ordinal=?''', (meta['id'], ordinal)).fetchone()
            if row is None or row['kind'] != 'blob':
                raise Failure('memory_integrity', 'A required memory chunk is missing or invalid.')
            expected = min(CHUNK_BYTES, meta['bytes'] - ordinal * CHUNK_BYTES)
            # Check storage length before allocation. Charge all bytes read for
            # chunk verification, including the unread part of a boundary chunk.
            with self.db.blobopen('memory_chunks', 'body', row['rowid'], readonly=True) as blob:
                if len(blob) != expected:
                    raise Failure('memory_integrity', 'Memory chunk length does not match its envelope.')
                ledger.spend_payload(expected)
                raw = blob.read(expected)
            if hashlib.sha256(raw).hexdigest() != row['sha256']:
                raise Failure('memory_integrity', 'A required memory chunk is corrupt.')
            left, right = max(start - ordinal * CHUNK_BYTES, 0), min(end - ordinal * CHUNK_BYTES, expected)
            parts.append(raw[left:right])
        return b''.join(parts)

    def _page(self, meta, offset, maximum, ledger):
        result = {'status': 'not_delivered', 'sha256': meta['sha256'], 'total_bytes': meta['bytes'],
                  'offset': offset, 'end': offset, 'next_offset': None, 'reason': None,
                  'content': '', 'verification': 'read_chunks_only'}
        if offset > meta['bytes']:
            return dict(result, reason='offset_out_of_range')
        if maximum <= 0 and offset < meta['bytes']:
            return dict(result, reason='page_does_not_fit')
        # One byte of lookahead suffices to avoid splitting a UTF-8 code point.
        raw = self._bytes(meta, offset, min(meta['bytes'], offset + maximum + 1), ledger)
        if raw and raw[0] & 0xC0 == 0x80:
            return dict(result, reason='invalid_utf8_boundary')
        size = min(maximum, len(raw))
        while size and size < len(raw) and raw[size] & 0xC0 == 0x80:
            size -= 1
        if not size and offset < meta['bytes']:
            return dict(result, reason='page_does_not_fit')
        try:
            content = raw[:size].decode('utf-8')
        except UnicodeError:
            raise Failure('memory_integrity', 'Memory page is not valid UTF-8.') from None
        end = offset + size
        if offset == 0 and end == meta['bytes'] and hashlib.sha256(raw[:size]).hexdigest() != meta['sha256']:
            raise Failure('memory_integrity', 'The complete returned payload disagrees with its source digest.')
        return dict(result, status='complete' if offset == 0 and end == meta['bytes'] else 'page',
                    end=end, next_offset=end if end < meta['bytes'] else None, content=content)

    def search(self, snapshot, args, ledger, maximum, *, budget=None):
        if args.get('_automatic') and snapshot.hybrid:
            return self.store.recall.automatic(snapshot,args,ledger,maximum)
        if args.get('scope') in ('tags','tag_cleanup'):
            return note_topics.catalogue(self.store,snapshot,args,ledger,maximum,budget)
        if (snapshot.hybrid and args.get('scope') in ('notebook','memories')
                and args['mode']=='current' and not args['query'].strip()):
            return note_inventory.page(self.store,snapshot,args,ledger,maximum,budget)
        if snapshot.hybrid:
            return self.store.recall.search(snapshot,args,ledger,maximum,budget=budget)
        def fits(page):
            return len(encoded(page).encode()) <= maximum and (budget is None or budget.fits(page))
        query, mode, cursor = args['query'], args['mode'], args['cursor']
        digest = hashlib.sha256(query.encode()).hexdigest()
        with self.bounded(ledger):
            bank = self._check(snapshot, ledger)
            if bank['index_epoch'] != snapshot.epoch:
                raise Failure('memory_index_unavailable', 'Memory index is stale; no full-scan fallback ran.')
            before = 2**63 - 1
            if cursor:
                value = self._decode(snapshot, bank['handle_key'], cursor)
                if (not isinstance(value, list) or len(value) != 4 or value[:3] != ['c', mode, digest]
                        or type(value[3]) is not int or not 0 < value[3] <= before):
                    raise Failure('invalid_memory_reference', 'Cursor does not match this query and read mode.')
                before = value[3]
            if query.strip():
                match = 'scope:"b' + snapshot.bank_id + '" AND body:"' + query.replace('"', '""') + '"'
                hits = self.query(ledger, 'SELECT rowid FROM memory_fts WHERE memory_fts MATCH ? AND rowid<? ORDER BY rowid DESC LIMIT 9',
                                  (match, before)).fetchall()
            else:
                hits = self.query(ledger, '''SELECT sequence FROM memory_index
                    WHERE bank_id=? AND sequence<? ORDER BY sequence DESC LIMIT 9''',
                    (snapshot.bank_id, before)).fetchall()
            result = {'status': 'complete', 'mode': mode, 'previews_only': True,
                      'results': [], 'delivered_count': 0, 'next_cursor': None}
            seen, previous, delivered_positions = set(), before, []
            for index, hit in enumerate(hits):
                def next_cursor(position):
                    return self._token(snapshot, bank['handle_key'], ['c', mode, digest, position])
                if index == 8:
                    result.update(status='page', next_cursor=next_cursor(previous))
                    break
                entry = self.query(ledger, 'SELECT id,record_id FROM memory_revisions WHERE sequence=?', (hit[0],)).fetchone()
                if entry is None:
                    raise Failure('memory_index_unavailable', 'Index points to missing memory; no fallback ran.')
                try:
                    original = self._revision(snapshot, entry['record_id'], entry['id'], ledger)
                except Failure as error:
                    if error.code != 'memory_unavailable':
                        raise
                    previous = hit[0]  # Ineligible candidates disclose no identity/body.
                    continue
                indexed = self.query(ledger, '''SELECT i.sha256,p.sha256 AS expected FROM memory_index i
                    JOIN memory_payloads p ON p.id=? WHERE i.revision_id=?''',
                    (original['payload_id'], original['id'])).fetchone()
                if indexed is None or indexed['sha256'] != indexed['expected']:
                    raise Failure('memory_index_unavailable', 'Indexed memory revision is stale; no fallback ran.')
                current = self._resolve(snapshot, original, ledger)
                row = current if mode == 'current' else original
                if mode == 'current' and current['historical_rejected']:
                    previous=hit[0]
                    continue
                if mode=='current' and current['owned_note'] is not None:
                    details=self.store.notes.metadata(snapshot,current['id'],ledger)
                    if 'user-rejected' in details.get('flags',[]):
                        previous=hit[0]
                        continue
                identity = row['record_id'], row['id']
                if identity in seen:
                    previous = hit[0]
                    continue
                meta = self._meta(snapshot, row['payload_id'], 'memory', ledger)
                preview = self._page(meta, 0, 128, ledger)
                item = {'record_id': row['record_id'], 'revision_id': row['id'],
                        'read_ref': self._reference(snapshot, bank['handle_key'], 'm', mode, row),
                        'sha256': meta['sha256'], 'total_bytes': meta['bytes'],
                        'preview': preview['content'], 'preview_offset': 0, 'preview_end': preview['end']}
                if row['owned_note'] is not None:
                    item['record_kind'] = 'authored_note'
                    details=self.store.notes.metadata_summary(snapshot,row['id'],ledger)
                    if details: item['note']=details
                if row['historical_note'] is not None:
                    item.update(record_kind='historical_note', historical_note=self.store.historical_notes.summary(snapshot,row['id'],ledger))
                more = index + 1 < len(hits)
                candidate = dict(result, results=result['results'] + [item], delivered_count=len(result['results']) + 1,
                                 status='page' if more else 'complete', next_cursor=next_cursor(hit[0]) if more else None)
                if not fits(candidate):
                    result.update(status='page' if result['results'] else 'not_delivered',
                                  next_cursor=next_cursor(previous), reason='result_capacity')
                    break  # Do not skip the undelivered candidate to fit a later one.
                result = candidate
                delivered_positions.append(hit[0])
                seen.add(identity)
                previous = hit[0]
            else:
                result.update(status='complete', next_cursor=None)
            # A capacity reason/cursor can cost more than the last accepted
            # candidate (including EOF's null cursor). Drop whole trailing hits
            # only, and rewind to the last one actually delivered, never past it.
            while result['results'] and not fits(result):
                result['results'].pop()
                delivered_positions.pop()
                result.update(status='page' if result['results'] else 'not_delivered',
                              delivered_count=len(result['results']), reason='result_capacity',
                              next_cursor=next_cursor(delivered_positions[-1] if delivered_positions else before))
            self._check(snapshot, ledger)
            return result

    def _source(self, snapshot, row, source_revision, ledger):
        source = self.query(ledger, '''SELECT s.* FROM memory_sources s
            WHERE s.id=? AND s.bank_id=? AND s.readable=1 AND s.kind IN ('message','note','fixture')
            AND EXISTS(SELECT 1 FROM memory_edges e WHERE e.revision_id=? AND e.source_revision_id=s.id)''',
            (source_revision, snapshot.bank_id, row['id'])).fetchone()
        if source is None:
            raise Failure('memory_source_unavailable', 'Supporting source is unavailable or not granted by this record.')
        return source

    def _provenance(self, snapshot, row, mode, key, offset, ledger, maximum):
        if row['historical_note'] is not None:
            return self.store.historical_notes.history(snapshot,row,key,offset,maximum,ledger)
        if row['owned_note'] is not None:
            return self.store.notes.provenance(snapshot, row, mode, key, offset, ledger, maximum)
        edges = self.query(ledger, 'SELECT * FROM memory_edges WHERE revision_id=? AND id>? ORDER BY id LIMIT 9',
                           (row['id'], offset)).fetchall()
        result = {'status': 'complete', 'kind': 'provenance', 'record_id': row['record_id'],
                  'revision_id': row['id'], 'offset_unit': 'edge_id', 'offset': offset,
                  'results': [], 'delivered_count': 0, 'next_offset': None}
        previous = offset
        for index, edge in enumerate(edges):
            if index == 8:
                result.update(status='page', next_offset=previous)
                break
            try:
                source = self._source(snapshot, row, edge['source_revision_id'], ledger)
                item = {'edge_id': edge['id'], 'relation': edge['relation'],
                        'source_id': source['source_id'], 'source_revision_id': source['id'],
                        'read_ref': self._reference(snapshot, key, 's', mode, row, source['id'])}
            except Failure as error:
                if error.code != 'memory_source_unavailable':
                    raise
                item = {'edge_id': edge['id'], 'status': 'support_unavailable'}
            more = index + 1 < len(edges)
            candidate = dict(result, results=result['results'] + [item], delivered_count=len(result['results']) + 1,
                             status='page' if more else 'complete', next_offset=edge['id'] if more else None)
            if len(encoded(candidate).encode()) > maximum:
                result.update(status='page' if result['results'] else 'not_delivered',
                              next_offset=previous, reason='result_capacity')
                break
            result, previous = candidate, edge['id']
        # A capacity reason added after the candidate check is also part of the
        # byte envelope. Remove only whole trailing edges; never skip them in the
        # cursor or cut serialized JSON. An empty refusal still needs metadata.
        while result['results'] and len(encoded(result).encode()) > maximum:
            kept = result['results'][:-1]
            result.update(results=kept, delivered_count=len(kept),
                          status='page' if kept else 'not_delivered',
                          next_offset=kept[-1]['edge_id'] if kept else offset)
        return result

    def read(self, snapshot, args, ledger, maximum):
        with self.bounded(ledger):
            bank = self._check(snapshot, ledger)
            value = self._decode(snapshot, bank['handle_key'], args['read_ref'])
            if snapshot.hybrid and isinstance(value,list) and value and value[0] in ('h','o'):
                return self.store.recall.read_message(snapshot,value,
                    dict(args,max_bytes=min(args['max_bytes'],maximum)),ledger)
            if (not isinstance(value, list) or len(value) != 5 or value[0] not in ('m', 'p', 's', 'n','d')
                    or value[1] not in ('current', 'historical')
                    or any(not isinstance(v, str) or not re.fullmatch('[0-9a-f]{32}', v) for v in value[2:4])
                    or (value[0] == 's' and (not isinstance(value[4], str) or not re.fullmatch('[0-9a-f]{32}', value[4])))
                    or (value[0] != 's' and value[4] is not None)):
                raise Failure('invalid_memory_reference', 'Reference is not a typed memory/source/provenance handle.')
            kind, mode, rid, revision, source_revision = value
            row = self._revision(snapshot, rid, revision, ledger)
            current = self._resolve(snapshot, row, ledger)
            if mode == 'current' and (current['record_id'], current['id']) != (rid, revision):
                raise Failure('memory_source_changed', 'Current memory revision changed; rediscover it in a new turn.')
            maximum = min(args['max_bytes'], maximum)
            if kind == 'p':
                # Bound the candidate before allocation. The shared ledger fits
                # its actual escaped representation, dropping only whole edges.
                result = self._provenance(snapshot, row, mode, bank['handle_key'], args['offset'], ledger, maximum)
            elif kind == 'n':
                result = self.store.notes.origin_page(snapshot, row, args['offset'], maximum, ledger)
            elif kind == 'd':
                if row['historical_note'] is not None:
                    result = self.store.historical_notes.details(snapshot,row,args['offset'],maximum,ledger)
                elif row['owned_note'] is None:
                    raise Failure('memory_unavailable','Only authored notes have notebook details.')
                else:
                    result = self.store.notes.metadata_page(snapshot,row,args['offset'],maximum,ledger)
            else:
                source = self._source(snapshot, row, source_revision, ledger) if kind == 's' else row
                meta = self._meta(snapshot, source['payload_id'], 'source' if kind == 's' else 'memory', ledger)
                result = self._page(meta, args['offset'], maximum, ledger)
                # Identity/extent stay on every page. The opaque request handle
                # stays in the native call and durable operation arguments; page
                # continuation reuses it, not a second echoed body field.
                result.update(kind='source' if kind == 's' else 'memory', record_id=rid, revision_id=revision,
                              mode=mode)
                if kind == 's':
                    result.update(source_id=source['source_id'], source_revision_id=source['id'],
                                  source_kind=source['kind'], created_at=source['created_at'])
                else:
                    if row['historical_note'] is not None:
                        result.update(record_kind='historical_note',read_only=True,
                            notice='Historical text: old plans and procedures are not active. Inherited legacy context is not applied; check conversation evidence for present-day claims.',
                            historical_note=self.store.historical_notes.summary(snapshot,row['id'],ledger),
                            details_ref=self._reference(snapshot,bank['handle_key'],'d',mode,row))
                    if row['owned_note'] is not None:
                        result['record_kind'] = 'authored_note'
                        details=self.store.notes.metadata_summary(snapshot,row['id'],ledger)
                        if details:
                            result['note']=details
                            result['details_ref']=self._reference(snapshot,bank['handle_key'],'d',mode,row)
                    result.update(author=row['author'], origin=row['origin'], created_at=row['created_at'],
                                  validity=row['validity'], revision_current=row['id'] == row['head_revision'],
                                  provenance_ref=self._reference(snapshot, bank['handle_key'], 'p', mode, row))
                    if mode == 'historical' and (current['record_id'], current['id']) != (rid, revision):
                        result['current_ref'] = self._reference(snapshot, bank['handle_key'], 'm', 'current', current)
            self._check(snapshot, ledger)
            return result
