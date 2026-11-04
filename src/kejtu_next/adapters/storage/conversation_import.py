"""Explicit offline transcript adoption, separate from ordinary runtime writes.

Only this operator-invoked reader understands the old format. Runtime discovery
uses native artifacts/history/recall; never a live legacy connection or index.
Origin maps deliberately outlive deleted bodies: replay cannot resurrect them.
"""
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from ...core.execution import HistoryPolicy
from ...core.records import Failure

IMPORT_SCHEMA = '''
CREATE TABLE conversation_imports(id TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL,
 created_at TEXT NOT NULL, receipt TEXT NOT NULL);
CREATE TABLE imported_conversations(conversation_id TEXT PRIMARY KEY,
 batch_id TEXT NOT NULL REFERENCES conversation_imports(id), source_id TEXT NOT NULL,
 title TEXT NOT NULL, source_created_at TEXT, source_updated_at TEXT,
 UNIQUE(batch_id,source_id));
CREATE TABLE imported_exchanges(turn_id TEXT PRIMARY KEY,
 batch_id TEXT NOT NULL REFERENCES conversation_imports(id), conversation_id TEXT NOT NULL,
 source_entry_id INTEGER NOT NULL, snapshot_sha256 TEXT NOT NULL, pending INTEGER,
 error TEXT, rich_state INTEGER NOT NULL, UNIQUE(batch_id,conversation_id,source_entry_id));
CREATE TABLE imported_messages(artifact_id TEXT PRIMARY KEY,
 batch_id TEXT NOT NULL REFERENCES conversation_imports(id), source_id TEXT NOT NULL,
 source_sha256 TEXT NOT NULL, excluded INTEGER NOT NULL CHECK(excluded IN (0,1)),
 provenance TEXT NOT NULL, source_created_at TEXT, UNIQUE(batch_id,source_id));
CREATE TABLE imported_exclusions(batch_id TEXT NOT NULL REFERENCES conversation_imports(id),
 fingerprint TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT,
 PRIMARY KEY(batch_id,fingerprint));
'''


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def identity(batch, kind, source):
    return uuid.uuid5(uuid.UUID(batch), kind + ':' + str(source)).hex


def imported(store, conversation_id):
    return store.db.execute('SELECT * FROM imported_conversations WHERE conversation_id=?',
                            (conversation_id,)).fetchone()


def require_writable(store, conversation_id):
    if imported(store, conversation_id):
        raise Failure('imported_conversation',
            'This imported conversation is read-only. Start a new conversation; its history remains available to recall.')


def source_info(store, artifact_id, ledger):
    row = store.memory.query(ledger, 'SELECT source_id,provenance FROM imported_messages WHERE artifact_id=?',
                           (artifact_id,)).fetchone()
    return dict(origin='imported_conversation', source_id=row['source_id'],
                provenance=json.loads(row['provenance'])) if row else {}


def _title(db, conversation):
    if conversation['title']:
        return conversation['title']
    first = db.execute('SELECT body FROM messages WHERE conversation_id=? AND role=\'user\' ORDER BY position LIMIT 1',
                       (conversation['id'],)).fetchone()
    row = db.execute('SELECT title,source_hash FROM conversation_titles WHERE conversation_id=? AND status=\'ready\'',
                     (conversation['id'],)).fetchone()
    original = db.execute('''SELECT u.body AS question,a.body AS answer FROM messages u JOIN messages a
        ON a.conversation_id=u.conversation_id AND a.entry_id=u.entry_id AND a.role='assistant'
        WHERE u.conversation_id=? AND u.role='user' AND u.excluded=0 AND a.excluded=0
        AND trim(u.body)<>'' AND trim(a.body)<>'' ORDER BY u.position LIMIT 1''', (conversation['id'],)).fetchone()
    if row and original and digest(encoded(dict(original))) == row['source_hash']:
        return row['title']
    # Exact original display fallback, not a newly generated summary.
    line = ' '.join(first[0].strip().splitlines()[0].split()) if first and first[0].strip() else ''
    if not line: return 'Empty conversation'
    words = line.split(' '); prefix = ' '.join(words[:8])
    if len(prefix) <= 56 and len(words) <= 8: return prefix
    clipped = prefix[:56]; boundary = clipped.rfind(' ')
    return (clipped[:boundary] if len(prefix) > 56 and boundary > 24 else clipped) + '…'


def _text(value, maximum, nullable=False):
    if nullable and value is None: return value
    if not isinstance(value, str) or len(value.encode()) > maximum:
        raise Failure('import_source', 'Legacy text or metadata exceeds the supported import bound.')
    return value


def _source(path):
    path = Path(path).absolute()
    if path.is_symlink() or path.resolve() != path or not path.is_file():
        raise Failure('import_source', 'Select a real, consistent legacy snapshot.')
    if any(Path(str(path)+suffix).is_file() and Path(str(path)+suffix).stat().st_size
           for suffix in ('-wal','-journal')):
        raise Failure('import_source', 'Use a standalone consistent backup, not an active database or journal.')
    with path.open('rb') as file: sha = hashlib.file_digest(file, 'sha256').hexdigest()
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
    if db.execute('PRAGMA user_version').fetchone()[0] != 12:
        db.close(); raise Failure('import_source', 'Only the inventoried legacy schema12 is supported.')
    return db, sha


def import_conversations(store, snapshot, *, source_id, profile, checkpoint=lambda _: None):
    """Atomic/idempotent offline import. Caller holds SqliteStore's owner lock.

    source_id is the stable archive namespace, not a new ID on each retry. A
    changed snapshot under that namespace conflicts instead of mixing revisions.
    The supplied current profile configures read access only; imported threads
    cannot dispatch inference and their inspection never claims this old model.
    """
    if not store._identity(source_id) or store.db.in_transaction:
        raise Failure('import_arguments', 'Use a stable source identity and an independent transaction.')
    if store.active_turn_id() or store.db.execute("SELECT 1 FROM background_runs WHERE status IN ('running','cancel_requested','pending')").fetchone():
        raise Failure('import_busy', 'Settle foreground and background work before import.')
    source, sha = _source(snapshot)
    with closing(source):
        previous = store.db.execute('SELECT * FROM conversation_imports WHERE id=?',(source_id,)).fetchone()
        if previous:
            if previous['source_sha256'] != sha:
                raise Failure('import_conflict', 'This archive identity already names a different snapshot; nothing changed.')
            return dict(json.loads(previous['receipt']), replayed=True)
        counts = {t:source.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('conversations','turns','messages')}
        if not 0 < counts['conversations'] <= 10000 or counts['turns'] > 100000 or counts['messages'] != counts['turns'] * 2:
            raise Failure('import_source', 'Legacy transcript count or message pairing is unsupported.')
        bank = store.db.execute('SELECT id FROM memory_banks WHERE slot=1').fetchone()[0]
        excluded = {r[0] for r in source.execute('SELECT fingerprint FROM exclusions')}
        receipt = dict(batch_id=source_id, source_sha256=sha, **counts, indexed_messages=0,
                       excluded_messages=0, unknown_message_dates=0, replayed=False)
        imported_at = datetime.now(timezone.utc).isoformat()
        # Native identities are deterministic and cannot collide with a prior
        # removal tombstone without the whole transaction refusing.
        with store.db:
            store.db.execute('INSERT INTO conversation_imports VALUES(?,?,?,?)', (source_id,sha,imported_at,'{}'))
            store.db.executemany('INSERT INTO imported_exclusions VALUES(?,?,?,?)',
                ((source_id,*tuple(r)) for r in source.execute('SELECT fingerprint,reason,created_at FROM exclusions')))
            config = store._put('configuration', encoded(dict(profile=asdict(profile),
                system_prompt='Imported historical transcript. Read-only; start a new conversation to reply.',
                history_enabled=True, history_policy=HistoryPolicy.STANDARD.value,
                memory_bank_id=bank, hybrid_memory=True, imported_archive=source_id)))
            sequence = max(store.db.execute('SELECT coalesce(max(sequence),0) FROM turns').fetchone()[0],
                           store.db.execute('SELECT sequence FROM exchange_sequence_floor').fetchone()[0])
            actual = dict(conversations=0,turns=0,messages=0)
            for conv in source.execute('SELECT * FROM conversations ORDER BY created_at,id'):
                cid = identity(source_id,'conversation',conv['id'])
                title = _text(_title(source,conv),320)
                created = _text(conv['created_at'],64,True)
                store.db.execute('INSERT INTO conversations VALUES(?,?,?)',(cid,config,created or ''))
                store.db.execute('INSERT INTO imported_conversations VALUES(?,?,?,?,?,?)',
                    (cid,source_id,conv['id'],title,created,_text(conv['updated_at'],64,True)))
                store.db.execute('INSERT INTO memory_grants VALUES(?,?,1,0)',(cid,bank))
                actual['conversations'] += 1
                for turn in source.execute('SELECT * FROM turns WHERE conversation_id=? ORDER BY position',(conv['id'],)):
                    raw = _text(turn['snapshot'],4*1024*1024); entry = json.loads(raw)
                    rows = list(source.execute('SELECT * FROM messages WHERE conversation_id=? AND entry_id=? ORDER BY position',
                                                (conv['id'],turn['entry_id'])))
                    if len(rows)!=2 or [m['role'] for m in rows]!=['user','assistant'] or rows[0]['body']!=entry['question']:
                        raise Failure('import_source','An exchange has inconsistent original messages.')
                    view=entry.get('view') or {}; synthesis=view.get('synthesis') or {}
                    pinned=next((v for v in (view.get('evidenceNavigation') or {}).get('options',[])
                        if v.get('internalId')==view.get('pinnedEvidenceId')),None) or {}
                    if rows[1]['body']!=synthesis.get('answerText',pinned.get('answerText',view.get('answerText',''))):
                        raise Failure('import_source','Selected answer and original message differ.')
                    tid=identity(source_id,'turn',str(conv['id'])+':'+str(turn['entry_id']))
                    sequence+=1; artifacts=[]; flags=[]
                    complete=bool(rows[1]['body'].strip()) and not entry.get('pending') and not entry.get('error')
                    for m in rows:
                        body=_text(m['body'],4*1024*1024); date=_text(m['created_at'],64,True)
                        kind='user_message' if m['role']=='user' else 'assistant_message' if complete else 'partial_response'
                        # Reuse the payload/chunk owner's exact integrity format.
                        temporary=store._put(kind,body)
                        store.db.execute('UPDATE artifacts SET created_at=? WHERE id=?',(date or '',temporary))
                        # ID updates would conflict with deferred children; instead
                        # deterministic source->native mappings live in the receipt
                        # owner while payload identities remain storage-owned.
                        aid=temporary; artifacts.append(aid)
                        flag=bool(m['excluded'] or (body.strip() and digest(' '.join(body.casefold().split())) in excluded))
                        flags.append(flag)
                        p=json.loads(_text(m['provenance'],4*1024*1024))
                        provenance={k:_text(p.get(k),1024,True) for k in ('mode','model','vault_id','vault_name')}
                        store.db.execute('INSERT INTO imported_messages VALUES(?,?,?,?,?,?,?)',
                            (aid,source_id,m['id'],digest(body),int(flag),encoded(provenance),date))
                        receipt['excluded_messages']+=flag;receipt['unknown_message_dates']+=date is None
                        actual['messages']+=1
                    store.db.execute('''INSERT INTO turns(sequence,id,conversation_id,user_id,answer_id,partial_id,status,
                        reason,detail,created_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                        (sequence,tid,cid,artifacts[0],artifacts[1] if complete else None,
                         artifacts[1] if not complete else None,'completed' if complete else 'interrupted',
                         None if complete else 'historical_incomplete',
                         None if complete else 'Original exchange has no completed answer.',rows[0]['created_at'] or '',rows[1]['created_at']))
                    store.db.execute('INSERT INTO imported_exchanges VALUES(?,?,?,?,?,?,?,?)',
                        (tid,source_id,cid,turn['entry_id'],digest(raw),entry.get('pending'),entry.get('error'),int(bool(entry.get('replyState')))))
                    # No admission unit, provider state or tool receipt is invented.
                    # Only original nonempty, individually eligible messages enter
                    # recall. The blank/partial body remains visible in the UI.
                    for m,aid,flag in zip(rows,artifacts,flags):
                        if flag or not m['body'].strip() or (m['role']=='assistant' and not complete):continue
                        rowid=store.db.execute('INSERT INTO history_sources(conversation_id,turn_id,sequence,artifact_id,role) VALUES(?,?,?,?,?)',
                            (cid,tid,sequence,aid,m['role'])).lastrowid
                        store.db.execute('INSERT INTO history_fts(rowid,scope,body) VALUES(?,?,?)',(rowid,'c'+cid,m['body']))
                        receipt['indexed_messages']+=1
                    actual['turns']+=1
                    checkpoint('exchange')
            if actual!=counts:raise Failure('import_source','Orphaned or missing original records; import rolled back.')
            # Even a mistakenly supplied changing source cannot certify a hash
            # of different bytes. Activation uses an independently saved snapshot.
            if any(Path(str(snapshot)+suffix).is_file() and Path(str(snapshot)+suffix).stat().st_size
                   for suffix in ('-wal','-journal')):
                raise Failure('import_source_changed','Source acquired an active journal during import; nothing was committed.')
            with Path(snapshot).open('rb') as file:
                if hashlib.file_digest(file,'sha256').hexdigest()!=sha:
                    raise Failure('import_source_changed','Source changed during import; nothing was committed.')
            store.db.execute('UPDATE conversation_imports SET receipt=? WHERE id=?',(encoded(receipt),source_id))
            if store.db.execute('PRAGMA foreign_key_check').fetchone():raise Failure('import_integrity','Imported references failed integrity.')
            checkpoint('before_commit')
        return receipt
