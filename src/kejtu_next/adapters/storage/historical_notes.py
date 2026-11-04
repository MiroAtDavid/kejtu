"""Historical prose in the existing memory reader, never a legacy notebook owner.

Only explicit offline import reads the old database. Runtime reads use indexed
native identities; inherited source/correction cascades remain in the preserved
source snapshot, not in the new ownership graph. Only snapshot heads are indexed.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from ...core.records import Failure
from ...core.reference import exact_page
from .conversation_import import _source, identity, encoded, digest

HISTORICAL_NOTES_SCHEMA = '''
CREATE TABLE historical_note_imports(id TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL,
 created_at TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('available','withdrawn')), receipt TEXT NOT NULL);
CREATE TABLE historical_notes(record_id TEXT PRIMARY KEY, bank_id TEXT NOT NULL REFERENCES memory_banks(id),
 batch_id TEXT NOT NULL REFERENCES historical_note_imports(id), source_id TEXT NOT NULL,
 source_status TEXT NOT NULL, rejected INTEGER NOT NULL CHECK(rejected IN (0,1)),
 head_revision TEXT NOT NULL, UNIQUE(batch_id,source_id));
CREATE INDEX historical_notes_bank ON historical_notes(bank_id,record_id);
CREATE TABLE historical_note_revisions(revision_id TEXT PRIMARY KEY REFERENCES memory_revisions(id),
 record_id TEXT NOT NULL REFERENCES historical_notes(record_id), source_revision INTEGER NOT NULL,
 variant TEXT NOT NULL CHECK(variant IN ('head','version')), metadata TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE INDEX historical_note_revision_owner ON historical_note_revisions(record_id,source_revision,variant);
'''

NOTICE = ('Historical notebook text, not current instructions or verified current facts. '
          'Original source links and inherited correction context remain in the preserved legacy snapshot; '
          'they have not become new ownership or execution. Read relevant conversation evidence when needed.')
FIELDS = ('title', 'purpose', 'tags', 'status', 'revision', 'created_at', 'updated_at',
          'thread_status', 'basis', 'source_quote', 'correction_reason', 'plan')


def _metadata(value):
    result = {key: value.get(key) for key in FIELDS}
    for key in ('tags', 'plan'):
        if isinstance(result[key], str):
            result[key] = json.loads(result[key])
    if (not isinstance(result['tags'], list) or len(result['tags']) > 64
            or any(not isinstance(t, str) or len(t.encode()) > 256 for t in result['tags'])
            or not isinstance(result['title'], str) or len(result['title'].encode()) > 240
            or type(result['revision']) is not int or not 1 <= result['revision'] <= 100000):
        raise Failure('import_source', 'Historical note metadata exceeds its supported envelope.')
    for key in ('created_at', 'updated_at', 'purpose', 'thread_status', 'basis', 'status'):
        if result[key] is not None and (not isinstance(result[key], str) or len(result[key].encode()) > 64):
            raise Failure('import_source', 'Historical note metadata is invalid.')
    body = encoded(result)
    if len(body.encode()) > 32768:
        raise Failure('import_source', 'Historical note details exceed their bounded envelope.')
    return result, body


def import_notes(store, snapshot, *, source_id, checkpoint=lambda _: None):
    """Atomic, idempotent import under the offline store lock; no authored writes.

    The mutable old head and its saved version can differ in attribution/date.
    Preserve both explicitly rather than silently repairing either old record.
    Forgotten heads and ALL their versions remain unreadable. Rejected heads are
    omitted from ordinary recall but available by explicit historical inspection.
    """
    if not store._identity(source_id) or store.db.in_transaction:
        raise Failure('import_arguments', 'Use a stable source identity and an independent transaction.')
    if store.active_turn_id() or store.db.execute("SELECT 1 FROM background_runs WHERE status IN ('running','cancel_requested','pending')").fetchone():
        raise Failure('import_busy', 'Settle foreground and background work before import.')
    source, sha = _source(snapshot)
    with closing(source):
        previous = store.db.execute('SELECT * FROM historical_note_imports WHERE id=?', (source_id,)).fetchone()
        if previous:
            if previous['source_sha256'] != sha:
                raise Failure('import_conflict', 'This historical notebook identity names another snapshot.')
            return dict(json.loads(previous['receipt']), replayed=True, status=previous['status'])
        # A second namespace must not silently duplicate an already imported snapshot.
        if store.db.execute('SELECT 1 FROM historical_note_imports WHERE source_sha256=?', (sha,)).fetchone():
            raise Failure('import_conflict', 'This snapshot already has a historical notebook identity.')
        count = source.execute('SELECT count(*) FROM notebook').fetchone()[0]
        versions = source.execute('SELECT count(*) FROM notebook_versions').fetchone()[0]
        if not 0 < count <= 10000 or not count <= versions <= 100000:
            raise Failure('import_source', 'Historical notebook count exceeds the supported import envelope.')
        if (source.execute('SELECT 1 FROM notebook WHERE length(CAST(content AS BLOB))>65536 LIMIT 1').fetchone()
                or source.execute('SELECT 1 FROM notebook_versions WHERE length(CAST(record AS BLOB))>2097152 LIMIT 1').fetchone()):
            raise Failure('import_source', 'Historical source exceeds its supported allocation envelope.')
        bank = store.db.execute('SELECT id FROM memory_banks WHERE slot=1').fetchone()[0]
        receipt = dict(batch_id=source_id, source_sha256=sha, notes=count, retained_versions=versions,
                       heads=count, readable_notes=0, forgotten_notes=0, rejected_notes=0,
                       prose_bytes=0, inherited_edges=0, authored_notes=0, replayed=False, status='available')
        from .memory import CHUNK_BYTES
        with store.db:
            store.db.execute('INSERT INTO historical_note_imports VALUES(?,?,?,?,?)',
                (source_id, sha, datetime.now(timezone.utc).isoformat(), 'available', '{}'))
            for raw_head in source.execute('SELECT * FROM notebook ORDER BY created_at,id'):
                head = dict(raw_head)
                if not store._identity(head['id']) or head['status'] not in ('active', 'forgotten'):
                    raise Failure('import_source', 'Unknown legacy note identity or lifecycle state.')
                fields, _ = _metadata(head)
                rid = identity(source_id, 'note', head['id'])
                current = identity(source_id, 'head', head['id'])
                readable = int(head['status'] == 'active')
                rejected = int(any(t.strip().casefold().replace('_','-') == 'user-rejected' for t in fields['tags']))
                store.db.execute('INSERT INTO historical_notes VALUES(?,?,?,?,?,?,?)',
                    (rid, bank, source_id, head['id'], head['status'], rejected, current))
                store.db.execute('INSERT INTO memory_records VALUES(?,?,?,?,?)', (rid, bank, current, 'current', readable))

                def put(value, variant, revision_id, original_hash):
                    fields, _ = _metadata(value)
                    body = value.get('content')
                    if not isinstance(body, str) or len(body.encode()) > 65536:
                        raise Failure('import_source', 'Historical note prose exceeds the supported revision envelope.')
                    metadata = encoded(dict(fields, source_record_sha256=original_hash))
                    if len(metadata.encode()) > 32768:
                        raise Failure('import_source', 'Historical note details exceed their supported envelope.')
                    raw = body.encode()
                    body_hash = hashlib.sha256(raw).hexdigest()
                    payload = identity(source_id, 'payload', body_hash)
                    receipt['prose_bytes'] += len(raw)
                    if receipt['prose_bytes'] > 64 * 1024 * 1024:
                        raise Failure('import_source', 'Historical notebook prose exceeds the import capacity.')
                    if not store.db.execute('SELECT 1 FROM memory_payloads WHERE id=?', (payload,)).fetchone():
                        store.db.execute('INSERT INTO memory_payloads VALUES(?,?,?,?,?)', (payload, bank, 'memory', len(raw), body_hash))
                        for offset in range(0, len(raw), CHUNK_BYTES):
                            chunk = raw[offset:offset+CHUNK_BYTES]
                            store.db.execute('INSERT INTO memory_chunks VALUES(?,?,?,?)',
                                (payload, offset//CHUNK_BYTES, hashlib.sha256(chunk).hexdigest(), chunk))
                    sequence = store.db.execute('''INSERT INTO memory_revisions(id,record_id,payload_id,author,origin,created_at,readable)
                        VALUES(?,?,?,?,?,?,?)''', (revision_id, rid, payload, 'legacy_companion', 'historical_notebook',
                        fields['updated_at'], readable)).lastrowid
                    store.db.execute('INSERT INTO historical_note_revisions VALUES(?,?,?,?,?,?)',
                        (revision_id, rid, fields['revision'], variant, metadata, digest(metadata)))
                    if variant == 'head' and readable:
                        store.db.execute('INSERT INTO memory_index VALUES(?,?,?,?)', (revision_id, bank, sequence, body_hash))
                        store.db.execute('INSERT INTO memory_fts(rowid,scope,body) VALUES(?,?,?)', (sequence, 'b'+bank, body))

                rows = source.execute('SELECT revision,record FROM notebook_versions WHERE note_id=? ORDER BY revision', (head['id'],))
                found_head = False
                for revision, record in rows:
                    if len(record.encode()) > 2 * 1024 * 1024:
                        raise Failure('import_source', 'Historical revision exceeds the supported source envelope.')
                    value = json.loads(record)
                    if value.get('id') != 'note:' + head['id'] or value.get('revision') != revision or revision > head['revision']:
                        raise Failure('import_source', 'Historical revision identity is inconsistent.')
                    if revision == head['revision']:
                        found_head = True
                        if value.get('content') != head['content']:
                            raise Failure('import_source', 'Current note and its retained revision disagree on original text.')
                    put(value, 'version', identity(source_id, 'version', head['id'] + ':' + str(revision)), digest(record))
                if not found_head:
                    raise Failure('import_source', 'A historical note has no matching retained head.')
                put(head, 'head', current, digest(encoded(head)))
                receipt['readable_notes'] += readable
                receipt['forgotten_notes'] += 1-readable
                receipt['rejected_notes'] += rejected * readable
                checkpoint('note')
            retained = store.db.execute('''SELECT count(*) FROM historical_note_revisions v
                JOIN historical_notes h ON h.record_id=v.record_id WHERE h.batch_id=?''', (source_id,)).fetchone()[0]
            if retained != count + versions:
                raise Failure('import_source', 'Orphaned or missing historical revisions; nothing was committed.')
            # Certify the bytes actually reviewed, even if an active database was
            # mistakenly supplied instead of the independently saved snapshot.
            if any(Path(str(snapshot)+suffix).is_file() and Path(str(snapshot)+suffix).stat().st_size
                   for suffix in ('-wal','-journal')):
                raise Failure('import_source_changed', 'Historical source acquired an active journal; nothing was committed.')
            with Path(snapshot).open('rb') as file:
                if hashlib.file_digest(file, 'sha256').hexdigest() != sha:
                    raise Failure('import_source_changed', 'Historical source changed; nothing was committed.')
            store.db.execute('UPDATE memory_banks SET index_epoch=epoch WHERE id=?', (bank,))
            store.db.execute('UPDATE historical_note_imports SET receipt=? WHERE id=?', (encoded(receipt), source_id))
            checkpoint('before_commit')
        return receipt


def withdraw_import(store, batch_id):
    """Withdraw this imported collection from reads/recall, preserving new work.

    Retained source bodies and the batch receipt are not a privacy erasure. The
    withdrawal is durable and idempotent; replaying import cannot resurrect it.
    Independently authored notes and their deliberate support links survive.
    """
    if store.db.in_transaction or store.active_turn_id():
        raise Failure('import_busy', 'Withdraw only through an idle offline owner.')
    if store.db.execute("SELECT 1 FROM background_runs WHERE status IN ('running','cancel_requested','pending')").fetchone():
        raise Failure('import_busy', 'Settle background work first.')
    batch = store.db.execute('SELECT * FROM historical_note_imports WHERE id=?', (batch_id,)).fetchone()
    if not batch:
        raise Failure('import_unavailable', 'Historical notebook import is absent.')
    if batch['status'] == 'withdrawn':
        return dict(status='withdrawn', batch_id=batch_id, changed=False)
    with store.db:
        for row in store.db.execute('''SELECT r.id,r.bank_id,v.id AS revision_id,v.sequence,p.id AS payload_id,p.bytes,p.sha256
            FROM historical_notes h JOIN memory_records r ON r.id=h.record_id
            JOIN memory_revisions v ON v.record_id=r.id JOIN memory_payloads p ON p.id=v.payload_id
            WHERE h.batch_id=?''', (batch_id,)).fetchall():
            indexed = store.db.execute('SELECT 1 FROM memory_index WHERE revision_id=?', (row['revision_id'],)).fetchone()
            if indexed:
                raw = b''.join(r[0] for r in store.db.execute('SELECT body FROM memory_chunks WHERE payload_id=? ORDER BY ordinal', (row['payload_id'],)))
                if len(raw) != row['bytes'] or hashlib.sha256(raw).hexdigest() != row['sha256']:
                    raise Failure('memory_integrity', 'Historical text failed withdrawal verification.')
                store.db.execute("INSERT INTO memory_fts(memory_fts,rowid,scope,body) VALUES('delete',?,?,?)", (row['sequence'],'b'+row['bank_id'],raw.decode()))
                store.db.execute('DELETE FROM memory_index WHERE revision_id=?', (row['revision_id'],))
        store.db.execute('UPDATE memory_records SET readable=0 WHERE id IN (SELECT record_id FROM historical_notes WHERE batch_id=?)', (batch_id,))
        store.db.execute("DELETE FROM recall_dirty WHERE kind='memory' AND source_id IN (SELECT v.id FROM memory_revisions v JOIN historical_notes h ON h.record_id=v.record_id WHERE h.batch_id=?)", (batch_id,))
        store.db.execute('UPDATE memory_banks SET index_epoch=epoch WHERE id IN (SELECT bank_id FROM historical_notes WHERE batch_id=?)', (batch_id,))
        store.db.execute("UPDATE historical_note_imports SET status='withdrawn' WHERE id=?", (batch_id,))
    return dict(status='withdrawn', batch_id=batch_id, changed=True)


class HistoricalNotes:
    def __init__(self, store):
        self.store = store

    def metadata(self, snapshot, revision, ledger):
        mem = self.store.memory
        row = mem.query(ledger, '''SELECT v.metadata,v.sha256,length(CAST(v.metadata AS BLOB)) AS size,
            v.source_revision,v.variant,h.source_id,h.batch_id FROM historical_note_revisions v
            JOIN historical_notes h ON h.record_id=v.record_id
            JOIN historical_note_imports b ON b.id=h.batch_id
            WHERE v.revision_id=? AND h.bank_id=? AND b.status='available' ''', (revision, snapshot.bank_id)).fetchone()
        if row is None:
            return None
        if not 0 < row['size'] <= 32768:
            raise Failure('memory_integrity', 'Historical note metadata is outside its envelope.')
        ledger.spend_payload(row['size'])
        if digest(row['metadata']) != row['sha256']:
            raise Failure('memory_integrity', 'Historical note metadata failed verification.')
        return dict(json.loads(row['metadata']), source_id=row['source_id'], batch_id=row['batch_id'],
                    source_revision=row['source_revision'], variant=row['variant'])

    def summary(self, snapshot, revision, ledger):
        value = self.metadata(snapshot, revision, ledger)
        if value is None:
            return None
        return {key:value[key] for key in ('title','source_id','source_revision','variant','created_at','updated_at')}

    def details(self, snapshot, row, offset, maximum, ledger):
        value = self.metadata(snapshot, row['id'], ledger)
        if value is None:
            raise Failure('memory_unavailable', 'Historical note details are unavailable.')
        return dict(exact_page(row['id'], encoded(dict(value, notice=NOTICE)), offset, maximum).envelope(), kind='historical_note_details')

    def history(self, snapshot, row, key, offset, maximum, ledger):
        mem = self.store.memory
        rows = mem.query(ledger, '''SELECT v.id,v.record_id,v.sequence,h.source_revision,h.variant,v.created_at
            FROM historical_note_revisions h JOIN memory_revisions v ON v.id=h.revision_id
            WHERE h.record_id=? AND v.sequence>? AND v.readable=1 ORDER BY v.sequence LIMIT 9''',
            (row['record_id'], offset)).fetchall()
        result = dict(status='complete',kind='historical_note_history',record_id=row['record_id'],
            revision_id=row['id'],offset=offset,offset_unit='revision_sequence',results=[],next_offset=None)
        previous = offset
        for index, item in enumerate(rows):
            if index == 8:
                result.update(status='page', next_offset=previous)
                break
            entry = dict(source_revision=item['source_revision'], variant=item['variant'], created_at=item['created_at'],
                read_ref=mem._reference(snapshot,key,'m','historical',item))
            candidate = dict(result, results=result['results']+[entry],
                status='page' if index+1<len(rows) else 'complete', next_offset=item['sequence'] if index+1<len(rows) else None)
            if len(encoded(candidate).encode()) > maximum:
                result.update(status='page' if result['results'] else 'not_delivered',next_offset=previous)
                break
            result, previous = candidate, item['sequence']
        return result
