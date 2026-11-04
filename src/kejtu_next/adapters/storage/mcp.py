"""Exchange-owned MCP attempts and complete normalized SDK results."""
import hashlib
import uuid
from datetime import datetime, timezone

from ...core.mcp import MAX_RESULT, digest, encode, parse
from ...core.execution import MAX_FOREGROUND_OPERATIONS
from ...core.records import Failure

MCP_SCHEMA = '''
CREATE TABLE mcp_calls(id TEXT PRIMARY KEY,turn_id TEXT NOT NULL REFERENCES turns(id),
 server TEXT NOT NULL,tool TEXT NOT NULL,connection_revision TEXT NOT NULL,schema_revision TEXT NOT NULL,
 arguments_sha256 TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('started','completed','uncertain','not_executed')),
 result_id TEXT REFERENCES artifacts(id),created_at TEXT NOT NULL,finished_at TEXT);
CREATE INDEX mcp_calls_turn ON mcp_calls(turn_id,created_at,id);
'''


BACKGROUND_MCP_SCHEMA = MCP_SCHEMA.replace('mcp_calls', 'background_mcp_calls').replace(
    'turn_id', 'run_id').replace('REFERENCES turns(id)', 'REFERENCES background_runs(id)')


class McpStore:
    def __init__(self, store, *, background=False):
        self.store, self.db = store, store.db
        self.background = background
        self.table = 'background_mcp_calls' if background else 'mcp_calls'
        self.origin = 'run_id' if background else 'turn_id'

    def check_origin(self, ident, *, running=False):
        if self.background:
            run = self.store.jobs.run(ident)
            definition = self.store.jobs.check_run(ident) if running else self.store.jobs.definition(run['job_id'], run['revision'])
            self.store.research.guard(definition, execution=running)
        elif running and self.store.turn_metadata(ident)['status'] != 'running':
            raise Failure('mcp_turn', 'This exchange is no longer running.')

    def start(self, turn_id, server, tool, revision, schema_revision, arguments):
        self.check_origin(turn_id, running=True)
        identity = uuid.uuid4().hex
        with self.db:
            self.db.execute(f'INSERT INTO {self.table} VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (identity, turn_id, server, tool, revision, schema_revision, digest(arguments), 'started', None,
                 datetime.now(timezone.utc).isoformat(), None))
        return identity

    def finish(self, identity, result=None, *, rejected=False):
        body = None if result is None else encode(result)
        if body is not None and len(body.encode()) > MAX_RESULT:
            self.finish(identity)
            raise Failure('mcp_result_size', 'MCP result exceeds 2 MB; its full content was not saved. Effects may have occurred.')
        with self.db:
            row = self.db.execute(f'SELECT status FROM {self.table} WHERE id=?', (identity,)).fetchone()
            if row is None or row[0] != 'started':
                raise Failure('mcp_call_state', 'This MCP attempt is already settled.')
            result_id = self.store._put('mcp_result', body) if body is not None else None
            self.db.execute(f'UPDATE {self.table} SET status=?,result_id=?,finished_at=? WHERE id=?',
                ('completed' if body is not None else 'not_executed' if rejected else 'uncertain', result_id, datetime.now(timezone.utc).isoformat(), identity))
        return result_id

    def load(self, result_id, turn_id):
        self.check_origin(turn_id)
        eligibility = '' if self.background else 'JOIN recall_turns t ON t.id=c.turn_id'
        row = self.db.execute(f'''SELECT c.*,p.body,a.sha256,a.bytes FROM {self.table} c
            JOIN artifacts a ON a.id=c.result_id JOIN artifact_payloads p ON p.artifact_id=a.id
            {eligibility} WHERE c.result_id=? AND c.{self.origin}=? AND a.kind='mcp_result'
            AND a.bytes<=? AND length(CAST(p.body AS BLOB))<=?''', (result_id, turn_id, MAX_RESULT, MAX_RESULT)).fetchone()
        if row is None:
            raise Failure('mcp_result_unavailable', 'This result does not belong to this eligible exchange.')
        value = dict(row)
        raw = value['body'].encode()
        if len(raw) != value['bytes'] or hashlib.sha256(raw).hexdigest() != value['sha256']:
            raise Failure('mcp_result_integrity', 'Saved MCP result failed its integrity check.')
        parse(value['body'])
        return value

    def list(self, turn_id):
        self.check_origin(turn_id)
        if not self.background:self.store.turn_metadata(turn_id)
        return [dict(row) for row in self.db.execute(f'''SELECT id,server,tool,status,result_id,created_at FROM {self.table}
            WHERE {self.origin}=? ORDER BY created_at,id LIMIT ?''', (turn_id, MAX_FOREGROUND_OPERATIONS))]
