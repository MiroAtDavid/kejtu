"""Explicit offline replacement-store upgrades, never automatic startup migration.

Only known replacement schema 16 through 38 -> 39 transitions are supported. The old
companion archive is a different product and is never opened by this operation.
Original configuration artifacts stay immutable; this schema-only upgrade does
not grant model task access to existing conversations.
"""
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from ...core.note_fields import tag_key, usable_topic
from ...core.records import Failure
from .locking import acquire_owner_lock
from .sqlite import APPLICATION_ID, SCHEMA, SCHEMA_VERSION
from .tasks import TASK_SCHEMA, TASK_ACCESS_SCHEMA
from .jobs import JOB_SCHEMA
from .research import RESEARCH_SCHEMA
from .reminders import REMINDER_SCHEMA
from .reminder_access import REMINDER_ACCESS_SCHEMA
from .deliveries import DELIVERY_SCHEMA
from .people import PEOPLE_SCHEMA
from .exchanges import EXCHANGE_SCHEMA
from .execution import EXECUTION_SCHEMA, EXECUTION_SEED
from .mcp import MCP_SCHEMA, BACKGROUND_MCP_SCHEMA
from .planner import PLANNER_SCHEMA
from .skills import SKILL_SCHEMA
from .care import CARE_SCHEMA
from .web import WEB_SCHEMA
from .web_usage import WEB_USAGE_SCHEMA
from .conversation_import import IMPORT_SCHEMA
from .historical_notes import HISTORICAL_NOTES_SCHEMA
from .conversation_titles import TITLE_SCHEMA
from .notes import NOTES_SCHEMA, FOREGROUND_NOTES_SCHEMA, NOTE_JOB_SCHEMA


def _functions(db):
    db.create_function('note_tag_key', 1, tag_key, deterministic=True)
    db.create_function('note_topic_valid', 1, usable_topic, deterministic=True)


def _schema(db):
    return [list(row) for row in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]


def _expected(version):
    with closing(sqlite3.connect(':memory:')) as db:
        _functions(db)
        schema = SCHEMA if version >= 21 else SCHEMA.replace(NOTES_SCHEMA, FOREGROUND_NOTES_SCHEMA)
        if version < 39:
            schema = schema.removesuffix(PLANNER_SCHEMA)
        if version < 38:
            schema = schema.removesuffix(BACKGROUND_MCP_SCHEMA)
        if version < 37:
            schema = schema.removesuffix(RESEARCH_SCHEMA)
        if version < 35:
            schema = schema.removesuffix(CARE_SCHEMA)
        if version < 34:
            schema = schema.removesuffix(SKILL_SCHEMA)
        if version < 33:
            schema = schema.removesuffix(MCP_SCHEMA)
        if version < 31:
            schema = schema.removesuffix(HISTORICAL_NOTES_SCHEMA)
        if version < 30:
            schema = schema.removesuffix(EXECUTION_SCHEMA)
        if version < 29:
            schema = schema.removesuffix(IMPORT_SCHEMA)
        if version < 28:
            schema = schema.removesuffix(WEB_USAGE_SCHEMA)
        if version < 27:
            schema = schema.removesuffix(WEB_SCHEMA)
        if version < 26:
            schema = schema.removesuffix(EXCHANGE_SCHEMA)
        if version < 25:
            schema = schema.removesuffix(TITLE_SCHEMA)
        if version < 24:
            schema = schema.removesuffix(PEOPLE_SCHEMA)
        if version < 23:
            schema = schema.removesuffix(DELIVERY_SCHEMA)
        if version < 22:
            schema = schema.removesuffix(REMINDER_ACCESS_SCHEMA)
        if version < 20:
            schema = schema.removesuffix(REMINDER_SCHEMA)
        if version < 19:
            schema = schema.removesuffix(JOB_SCHEMA)
        if version < 18:
            schema = schema.removesuffix(TASK_ACCESS_SCHEMA)
        if version == 16:
            schema = schema.removesuffix(TASK_SCHEMA)
        if version < 36:
            schema = schema.replace(EXECUTION_SCHEMA, EXECUTION_SCHEMA.replace(EXECUTION_SEED, '').replace('BETWEEN 1 AND 256', 'BETWEEN 1 AND 64').replace('BETWEEN 0 AND 256', 'BETWEEN 0 AND 64'))
        db.executescript(schema)
        return _schema(db)


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def fingerprints(db, names=None):
    """Exact typed row hashes, streamed in canonical SQL order; no private text.

    An offline preservation check, never a foreground archive-scan policy.
    Include virtual-table backing rows as well as the authoritative domain rows.
    """
    if names is None:
        names = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    result = {}
    for name in names:
        columns = [row[1] for row in db.execute(f'PRAGMA table_info({_quote(name)})')]
        digest = hashlib.sha256(json.dumps(columns).encode())
        count = 0
        for row in db.execute(f'SELECT * FROM {_quote(name)} ORDER BY ' +
                              ','.join(str(i + 1) for i in range(len(columns)))):
            # SQLite type distinctions matter, including BLOB versus TEXT.
            typed = [(type(value).__name__, value.hex() if isinstance(value, bytes) else value)
                     for value in row]
            digest.update(json.dumps(typed, ensure_ascii=True, separators=(',', ':')).encode() + b'\n')
            count += 1
        result[name] = dict(rows=count, sha256=digest.hexdigest())
    return result


def _check(db, expected_id, version):
    if version not in (16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39):
        raise Failure('upgrade_schema', 'Unsupported replacement schema after recovery.')
    if db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID:
        raise Failure('foreign_store', 'This is not a replacement archive.')
    if db.execute('PRAGMA user_version').fetchone()[0] != version or _schema(db) != _expected(version):
        raise Failure('upgrade_schema', 'Archive schema does not exactly match a supported replacement revision.')
    row = db.execute('SELECT state_id FROM state_metadata WHERE singleton=1').fetchone()
    if row != (expected_id,):
        raise Failure('state_identity', 'Archive identity does not match the explicit selection.')
    if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or db.execute('PRAGMA foreign_key_check').fetchone():
        raise Failure('upgrade_integrity', 'Archive integrity check failed; no schema change is permitted.')


@contextmanager
def _owned(directory, expected_id):
    directory = Path(directory).absolute()
    if (directory.is_symlink() or directory.resolve() != directory or not directory.is_dir()
            or len(expected_id) != 32 or any(c not in '0123456789abcdef' for c in expected_id)):
        raise Failure('state_identity', 'Select an explicit real replacement directory and exact state identity.')
    path, lock_path = directory / 'core.sqlite3', directory / 'owner.lock'
    if path.is_symlink() or not path.is_file() or lock_path.is_symlink() or not lock_path.is_file():
        raise Failure('foreign_store', 'An existing initialized replacement store is required.')
    with acquire_owner_lock(lock_path):
        # Header and write-once identity are checked before SQLite can recover a
        # hot journal. Immutable mode is ONLY an identity probe, not a data read.
        with path.open('rb') as source:
            header = source.read(100)
        if (len(header) != 100 or header[:16] != b'SQLite format 3\0'
                or int.from_bytes(header[68:72], 'big') != APPLICATION_ID
                or int.from_bytes(header[60:64], 'big') not in (16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39)):
            raise Failure('foreign_store', 'Only replacement schemas 16–39 are supported; nothing was opened for writing.')
        # This release owns rollback-journal stores. Do not guess the identity
        # from a stale main file when a WAL could contain authoritative changes.
        if header[18:20] != b'\x01\x01' or (path.parent / (path.name + '-wal')).exists():
            raise Failure('upgrade_journal', 'WAL stores require a separately verified checkpoint procedure.')
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as probe:
            if probe.execute('SELECT state_id FROM state_metadata WHERE singleton=1').fetchone() != (expected_id,):
                raise Failure('state_identity', 'Archive identity mismatch; no recovery was attempted.')
        yield path


def _sync(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _receipt(path, value):
    # Recovery evidence is never written over an earlier attempt's receipt.
    with path.open('x', encoding='utf-8') as target:
        os.chmod(path, 0o600)
        json.dump(value, target, indent=2, sort_keys=True)
        target.write('\n')
        target.flush()
        os.fsync(target.fileno())
    _sync(path.parent)


def _statements(db, script):
    statement = ''
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            db.execute(statement)
            statement = ''
    if statement.strip():
        raise Failure('upgrade_schema', 'Upgrade contains an incomplete SQL statement.')


def _note_origins(db):
    """Rebuild the two origin constraints without changing any original row.

    Used inside the reserved upgrade transaction with FK enforcement temporarily
    disabled on THIS disposable connection. Full FK/integrity checks run before
    and after. Recreate exact DDL, not ALTER RENAME (which rewrites child FKs).
    Install epoch triggers only after copying, preserving all existing epochs.
    """
    with closing(sqlite3.connect(':memory:')) as expected:
        expected.executescript(NOTES_SCHEMA)
        definitions = list(expected.execute('''SELECT type,name,sql FROM sqlite_schema
            WHERE tbl_name IN ('note_outcomes','note_revisions') AND sql IS NOT NULL
            ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name'''))
    for name in ('note_outcomes', 'note_revisions'):
        db.execute(f'CREATE TEMP TABLE origin_copy_{name} AS SELECT * FROM {name}')
        db.execute(f'DROP TABLE {name}')
    for kind, name, sql in definitions:
        if kind == 'table':
            db.execute(sql)
            db.execute(f'INSERT INTO {name} SELECT * FROM origin_copy_{name}')
            db.execute(f'DROP TABLE origin_copy_{name}')
    for kind, _, sql in definitions:
        if kind != 'table':
            db.execute(sql)
    _statements(db, NOTE_JOB_SCHEMA)


def _execution_limits(db):
    """Widen two CHECK constraints without rewriting saved or historical limits.

    The enclosing offline upgrade owns the transaction and verifies every original
    row and foreign key before committing. A stopped store never gains extra tool
    permissions merely because its supported numeric ceiling changes.
    """
    for name in ('execution_settings', 'turn_execution'):
        db.execute(f'CREATE TEMP TABLE execution_copy_{name} AS SELECT * FROM {name}')
        db.execute(f'DROP TABLE {name}')
    _statements(db, EXECUTION_SCHEMA)
    db.execute('DELETE FROM execution_settings')  # Replace the fresh-store default.
    for name in ('execution_settings', 'turn_execution'):
        db.execute(f'INSERT INTO {name} SELECT * FROM execution_copy_{name}')
        db.execute(f'DROP TABLE execution_copy_{name}')


def upgrade_store(directory, expected_id, backup_directory, *, checkpoint=None):
    """Upgrade while closed, with a durable verified pre-image and one transaction.

    checkpoint is a fault-injection seam for offline crash/rollback controls, not
    a hook used by product operations. Retrying a completed upgrade is read-only.
    No recovery/upgrade operation repeats model or task effects.
    """
    if SCHEMA_VERSION != 39:
        raise Failure('upgrade_version', 'This upgrade must be reviewed for the current schema.')
    checkpoint = checkpoint or (lambda phase: None)
    backup_directory = Path(backup_directory).absolute()
    with _owned(directory, expected_id) as path, closing(sqlite3.connect(
            path.as_uri() + '?mode=rw', uri=True, timeout=0, isolation_level=None)) as db:
        _functions(db)
        # SQLite's documented table-rebuild procedure requires this before
        # BEGIN. No other connection/store changes its enforcement policy.
        db.execute('PRAGMA foreign_keys=OFF')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        committed = False
        try:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            _check(db, expected_id, version)
            if version == 39:
                db.rollback()
                return dict(status='already_current', state_id=expected_id, schema=39, changed=False)
            if db.execute("SELECT 1 FROM turns WHERE status='running' LIMIT 1").fetchone():
                raise Failure('upgrade_active_turn', 'Unsettled turns remain; inspect them in the original runtime before upgrading.')
            if version >= 19 and db.execute("SELECT 1 FROM background_runs WHERE status IN ('running','cancel_requested') LIMIT 1").fetchone():
                raise Failure('upgrade_active_job', 'Unsettled background runs remain; recover them in the original runtime before upgrading.')
            if (backup_directory.resolve() != backup_directory
                    or backup_directory == path.parent or path.parent in backup_directory.parents):
                raise Failure('upgrade_backup', 'Select a new recovery directory outside the archive directory.')
            backup_directory.mkdir(mode=0o700, parents=False, exist_ok=False)
            _sync(backup_directory.parent)
            backup = backup_directory / 'before.sqlite3'
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            before = fingerprints(db)
            # The writer reservation excludes other writers while a separate RO
            # connection takes the consistent pre-image (backup on a write
            # transaction's own connection would wait indefinitely).
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0)) as source:
                with closing(sqlite3.connect(backup)) as target:
                    source.backup(target)
                    _functions(target)
                    _check(target, expected_id, version)
                    if fingerprints(target) != before:
                        raise Failure('upgrade_preservation', 'Backup does not match the reserved pre-image.')
            _sync(backup)
            _sync(backup_directory)
            with backup.open('rb') as saved:
                backup_hash = hashlib.file_digest(saved, 'sha256').hexdigest()
            record = dict(state_id=expected_id, from_schema=version, to_schema=39,
                          backup=str(backup), backup_sha256=backup_hash,
                          before=before, existing_conversation_configs='unchanged',
                          existing_model_task_access='unchanged; absent flag stays disabled',
                          background_jobs='preserved unchanged' if version >= 19 else 'empty; no permissions or schedules enabled',
                          reminders='preserved unchanged' if version >= 20 else 'empty; no reminders or schedules enabled',
                          note_origins='foreground rows unchanged; no background authoring granted',
                          activation=False)
            _receipt(backup_directory / 'prepared.json', record)
            checkpoint('backup_verified')
            # executescript commits pending transactions implicitly. Execute
            # each complete DDL statement separately so DDL and version commit
            # together and failures leave the original schema intact.
            additions = ((TASK_SCHEMA if version == 16 else '')
                         + (TASK_ACCESS_SCHEMA if version < 18 else '')
                         + (JOB_SCHEMA if version < 19 else '')
                         + (REMINDER_SCHEMA if version < 20 else '')
                         + (REMINDER_ACCESS_SCHEMA if version < 22 else '')
                         + (DELIVERY_SCHEMA if version < 23 else '')
                         + (PEOPLE_SCHEMA if version < 24 else '') + (TITLE_SCHEMA if version < 25 else '') + (EXCHANGE_SCHEMA if version < 26 else '') + (WEB_SCHEMA if version < 27 else '') + (WEB_USAGE_SCHEMA if version < 28 else '') + (IMPORT_SCHEMA if version < 29 else '') + (EXECUTION_SCHEMA if version < 30 else '') + (HISTORICAL_NOTES_SCHEMA if version < 31 else '') + (MCP_SCHEMA if version < 33 else '') + (SKILL_SCHEMA if version < 34 else '') + (CARE_SCHEMA if version < 35 else '') + (RESEARCH_SCHEMA if version < 37 else '') + (BACKGROUND_MCP_SCHEMA if version < 38 else '') + (PLANNER_SCHEMA if version < 39 else ''))
            _statements(db, additions)
            if 30 <= version < 36:
                _execution_limits(db)
            if version < 21:
                _note_origins(db)
            # The original flag is a creation-time intent, not a second live
            # permission owner. Seed exactly that intent; missing stays off.
            # No prompt/profile/artifact is rewritten during capability upgrade.
            for cid, body, sha, size, created in db.execute('''SELECT c.id,p.body,a.sha256,a.bytes,c.created_at
                    FROM conversations c JOIN artifacts a ON a.id=c.config_id
                    JOIN artifact_payloads p ON p.artifact_id=a.id ORDER BY c.id'''):
                raw = body.encode('utf-8')
                if hashlib.sha256(raw).hexdigest() != sha or len(raw) != size:
                    raise Failure('upgrade_integrity', 'A saved configuration body failed integrity verification.')
                config = json.loads(body)
                enabled = config.get('tasks_enabled', False)
                if type(enabled) is not bool or enabled and not config.get('memory_bank_id'):
                    raise Failure('upgrade_integrity', 'Saved task capability is invalid; no new permission was inferred.')
                if version < 18:
                    db.execute('INSERT INTO personal_task_grants VALUES(?,?,0,?)', (cid, int(enabled), created))
            checkpoint('schema_created')
            db.execute('PRAGMA user_version=39')
            _check(db, expected_id, 39)
            after = fingerprints(db, before)
            if after != before:
                raise Failure('upgrade_preservation', 'An existing row changed; the upgrade will roll back.')
            checkpoint('before_commit')
            db.commit()
            db.execute('PRAGMA foreign_keys=ON')
            committed = True
            checkpoint('after_commit')
            record.update(status='upgraded', after=after, preserved_tables=len(before))
            _receipt(backup_directory / 'completed.json', record)
            return record
        except BaseException as error:
            if not committed:
                db.rollback()
            # A failure after COMMIT must be resolved by inspecting schema and
            # the prepared backup, never by automatically restoring older data.
            if committed and isinstance(error, (OSError, sqlite3.Error)):
                raise Failure('upgrade_outcome_unknown',
                    'Schema committed but the completion receipt failed. Inspect this state and its prepared backup; do not restore or reapply automatically.') from error
            raise


def restore_upgrade_backup(backup_directory, directory, expected_id):
    """Restore to a new directory only. Never overwrite post-upgrade user work."""
    backup_directory, directory = Path(backup_directory).absolute(), Path(directory).absolute()
    backup = backup_directory / 'before.sqlite3'
    if (backup_directory.resolve() != backup_directory or backup.is_symlink()
            or directory.resolve() != directory or directory.exists()):
        raise Failure('upgrade_restore', 'Select a real backup and a new, absent destination directory.')
    record = json.loads((backup_directory / 'prepared.json').read_text())
    version = record['from_schema']
    if version not in (16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37):
        raise Failure('upgrade_restore', 'Backup is not from a supported pre-upgrade schema.')
    with backup.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    if record['state_id'] != expected_id or record['backup_sha256'] != digest:
        raise Failure('upgrade_restore', 'Backup identity or digest mismatch; nothing was restored.')
    with closing(sqlite3.connect(backup.as_uri() + '?mode=ro', uri=True)) as source:
        _functions(source)
        _check(source, expected_id, version)
        if fingerprints(source) != record['before']:
            raise Failure('upgrade_restore', 'Backup row fingerprints do not match the preparation receipt.')
        directory.mkdir(mode=0o700)
        _sync(directory.parent)
        target_path = directory / 'core.sqlite3'
        fd = os.open(target_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(target_path)) as target:
            source.backup(target)
            _functions(target)
            _check(target, expected_id, version)
            if fingerprints(target) != record['before']:
                raise Failure('upgrade_restore', 'Restored data did not verify; destination remains uninitialized.')
        _sync(target_path)
        fd = os.open(directory / 'owner.lock', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        _sync(directory)
    return dict(status='restored', schema=version, state_id=expected_id, directory=str(directory),
                activated=False, original_store_changed=False)
