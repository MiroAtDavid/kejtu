"""Single-owner candidate archive. Never opens or migrates a legacy archive."""
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from .locking import acquire_owner_lock
from ...core.budget import Ledger, message_cost
from ...core.context import RequestAccounting, request_schema
from ...core.execution import HistoryPolicy, select_history_policy
from ...core.memory import MEMORY_ARGUMENT_REASONS
from ...core.tools import HISTORY_ARGUMENT_REASONS
from ...core.notes import NotePolicy
from ...core.providers import provider
from ...core.records import Conversation, Failure, Message, Profile, Reply, ToolCall, Turn
from ...core.reference import ReferencePage, exact_page
from .access_settings import AccessSettings
from .onboarding import Onboarding
from .history import HISTORY_SCHEMA, HistoryStore
from .image_views import ImageViews
from .memory import MEMORY_SCHEMA, MemoryStore
from .notes import NOTES_SCHEMA, NoteStore
from .note_removal import REMOVAL_SCHEMA, NoteRemovalStore
from .notebook import NotebookStore
from .recall import RECALL_SCHEMA, RecallStore
from .note_metadata import SCHEMA as NOTE_METADATA_SCHEMA
from .tasks import TASK_SCHEMA, TASK_ACCESS_SCHEMA, TaskStore
from .jobs import JOB_SCHEMA, JobStore
from .research import RESEARCH_SCHEMA, ResearchStore
from .reminders import REMINDER_SCHEMA, ReminderStore
from .reminder_access import REMINDER_ACCESS_SCHEMA
from .deliveries import DELIVERY_SCHEMA, DeliveryStore
from .people import PEOPLE_SCHEMA, PeopleStore
from .planner import PLANNER_SCHEMA, PlannerStore

from .conversation_titles import TITLE_SCHEMA, ConversationTitles
from .exchanges import EXCHANGE_SCHEMA, ExchangeStore
from .web import WEB_SCHEMA, WebStore
from .mcp import MCP_SCHEMA, BACKGROUND_MCP_SCHEMA, McpStore
from .skills import SKILL_SCHEMA, SkillStore
from .care import CARE_SCHEMA, CareStore
from .web_usage import WEB_USAGE_SCHEMA
from .execution import EXECUTION_SCHEMA, ExecutionStore
from .conversation_import import IMPORT_SCHEMA, imported, require_writable
from .historical_notes import HISTORICAL_NOTES_SCHEMA, HistoricalNotes

APPLICATION_ID = 0x4B4A5232
SCHEMA_VERSION = 39
BODY_CHUNK_BYTES = 32768

SCHEMA = '''
CREATE TABLE artifacts(id TEXT PRIMARY KEY, kind TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE artifact_payloads(artifact_id TEXT PRIMARY KEY REFERENCES artifacts(id), body TEXT NOT NULL);
CREATE TABLE artifact_chunks(artifact_id TEXT NOT NULL REFERENCES artifacts(id),
 ordinal INTEGER NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(artifact_id,ordinal));
CREATE TABLE conversations(id TEXT PRIMARY KEY, config_id TEXT NOT NULL REFERENCES artifacts(id), created_at TEXT NOT NULL);
CREATE INDEX recent_conversations ON conversations(created_at,id);
CREATE TABLE state_metadata(singleton INTEGER PRIMARY KEY CHECK(singleton=1), state_id TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL);
CREATE TABLE native_mutations(kind TEXT NOT NULL, mutation_key TEXT NOT NULL, intent_sha256 TEXT NOT NULL,
 conversation_id TEXT REFERENCES conversations(id), turn_id TEXT REFERENCES turns(id), created_at TEXT NOT NULL,
 PRIMARY KEY(kind,mutation_key));
CREATE TABLE turns(sequence INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,
 conversation_id TEXT NOT NULL REFERENCES conversations(id), user_id TEXT NOT NULL REFERENCES artifacts(id),
 answer_id TEXT REFERENCES artifacts(id), partial_id TEXT REFERENCES artifacts(id),
 status TEXT NOT NULL CHECK(status IN ('running','completed','failed','cancelled','interrupted')),
 reason TEXT, detail TEXT, calls INTEGER NOT NULL DEFAULT 0, input_estimate INTEGER,
 input_tokens INTEGER, output_tokens INTEGER, finish_reason TEXT,
 created_at TEXT NOT NULL, finished_at TEXT);
CREATE INDEX conversation_turns ON turns(conversation_id,sequence);
CREATE UNIQUE INDEX one_running_turn ON turns(conversation_id) WHERE status='running';
CREATE TABLE context_events(sequence INTEGER PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES turns(id), record TEXT NOT NULL);
CREATE INDEX context_event_turn ON context_events(turn_id,sequence);
CREATE TABLE model_attempts(turn_id TEXT NOT NULL REFERENCES turns(id), ordinal INTEGER NOT NULL,
 input_estimate INTEGER NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
 finish_reason TEXT, state TEXT NOT NULL, note_failure TEXT, PRIMARY KEY(turn_id,ordinal));
CREATE TABLE reference_reads(turn_id TEXT PRIMARY KEY REFERENCES turns(id),
 reference_id TEXT NOT NULL REFERENCES artifacts(id), call_id TEXT NOT NULL,
 arguments TEXT NOT NULL, state TEXT NOT NULL, result TEXT);
CREATE TABLE provider_states(turn_id TEXT PRIMARY KEY REFERENCES turns(id),
 answer_id TEXT NOT NULL UNIQUE REFERENCES artifacts(id),
 config_id TEXT NOT NULL REFERENCES artifacts(id),
 reasoning_id TEXT NOT NULL UNIQUE REFERENCES artifacts(id),
 format TEXT NOT NULL, binding_sha256 TEXT NOT NULL);
CREATE INDEX unfinished_attempts ON model_attempts(state) WHERE state='reserved';
CREATE INDEX unfinished_reference_reads ON reference_reads(state) WHERE state='started';
''' + HISTORY_SCHEMA + MEMORY_SCHEMA + NOTES_SCHEMA + REMOVAL_SCHEMA + RECALL_SCHEMA + NOTE_METADATA_SCHEMA + TASK_SCHEMA + TASK_ACCESS_SCHEMA + JOB_SCHEMA + REMINDER_SCHEMA + REMINDER_ACCESS_SCHEMA + DELIVERY_SCHEMA + PEOPLE_SCHEMA + TITLE_SCHEMA + EXCHANGE_SCHEMA + WEB_SCHEMA + WEB_USAGE_SCHEMA + IMPORT_SCHEMA + EXECUTION_SCHEMA + HISTORICAL_NOTES_SCHEMA + MCP_SCHEMA + SKILL_SCHEMA + CARE_SCHEMA + RESEARCH_SCHEMA + BACKGROUND_MCP_SCHEMA + PLANNER_SCHEMA


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_binding(turn_id, answer_id, config_id, reasoning_id, format):
    # Detect broken associations independently of the payload's own digest.
    # Not authentication against somebody able to rewrite this DB coherently.
    return hashlib.sha256(json.dumps([turn_id, answer_id, config_id, reasoning_id, format]).encode()).hexdigest()


class SqliteStore:
    schema_version = SCHEMA_VERSION

    def __init__(self, directory: str | Path, *, create: bool = False,
                 expected_state_id: str | None = None):
        directory = Path(directory).absolute()
        self.titles = ConversationTitles(self)
        self.db = None
        self.lock = None
        if expected_state_id is not None and (create or not self._identity(expected_state_id)):
            raise Failure('state_identity', 'Expected state identity is invalid for this open; nothing was changed.')
        if directory.is_symlink():
            raise Failure('foreign_store', 'A symlink cannot select the candidate state directory.')
        if create:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if any(directory.iterdir()):
                raise Failure('foreign_store', 'Initialize requires an empty candidate directory.')
        elif not directory.is_dir():
            raise Failure('missing_store', 'Explicitly initialize a candidate state directory first.')
        self.path = directory / 'core.sqlite3'
        lock_path = directory / 'owner.lock'
        try:
            if not create:
                if not self.path.is_file() or self.path.is_symlink() or not lock_path.is_file():
                    raise Failure('foreign_store', 'This is not an initialized candidate store.')
            self.lock = acquire_owner_lock(lock_path, create=create)
            if create:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            else:
                # Inspect the on-disk header before SQLite may recover a hot
                # journal. Even recovery writes are forbidden for a foreign DB.
                with self.path.open('rb') as source:
                    header = source.read(100)
                if (header[:16] != b'SQLite format 3\x00' or len(header) != 100
                        or int.from_bytes(header[68:72], 'big') != APPLICATION_ID
                        or int.from_bytes(header[60:64], 'big') != SCHEMA_VERSION):
                    raise Failure('foreign_store', f'Not a schema-{SCHEMA_VERSION} candidate store; no migration or recovery was attempted. Use the original runtime for older stores.')
                if expected_state_id is not None:
                    self._verify_expected_state(expected_state_id)
            self.db = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=0)
            self.db.row_factory = sqlite3.Row
            from ...core.note_fields import tag_key, usable_topic
            self.db.create_function('note_tag_key',1,tag_key,deterministic=True)
            self.db.create_function('note_topic_valid',1,usable_topic,deterministic=True)
            if create:
                try:
                    self.db.executescript('BEGIN;\n' + SCHEMA +
                        f'PRAGMA application_id={APPLICATION_ID}; PRAGMA user_version={SCHEMA_VERSION}; COMMIT;')
                    with self.db:
                        self.db.execute('INSERT INTO state_metadata VALUES(1,?,?)', (uuid.uuid4().hex, stamp()))
                except sqlite3.OperationalError:
                    self.db.rollback()
                    raise Failure('history_index_unavailable', 'Required SQLite FTS5 schema could not be created; no fallback was used.') from None
            elif (self.db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
                  or self.db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION):
                raise Failure('foreign_store', f'Not a schema-{SCHEMA_VERSION} candidate store; no migration was attempted.')
            if expected_state_id is not None and self.state_id != expected_state_id:
                raise Failure('state_identity', 'Recovered state identity does not match the selected state.')
            self.db.execute('PRAGMA foreign_keys=ON')
            self.access_settings = AccessSettings(self)
            self.onboarding = Onboarding(self)
            self._history = HistoryStore(self)
            self._images = ImageViews(self)
            self.memory = MemoryStore(self)
            self.historical_notes = HistoricalNotes(self)
            self.recall = RecallStore(self)
            self.notes = NoteStore(self)
            self.personal_tasks = TaskStore(self)
            self.planner = PlannerStore(self)
            self.people = PeopleStore(self)
            self.web = WebStore(self)
            self.mcp = McpStore(self)
            self.skills = SkillStore(self)
            self.execution = ExecutionStore(self)
            self.jobs = JobStore(self)
            self.research = ResearchStore(self)
            self.care = CareStore(self)
            self.reminders = ReminderStore(self)
            self.deliveries = DeliveryStore(self)
            self.note_removal = NoteRemovalStore(self)
            self.notebook = NotebookStore(self)
            self.exchanges = ExchangeStore(self)
            # The lock excludes another cooperating application owner, not a
            # remote completion or a transport orphaned by SIGKILL. Recover
            # receipts, never replay an uncertain provider call.
            with self.db:
                self.db.execute("UPDATE turns SET status='interrupted', reason='interrupted', detail=?, finished_at=? WHERE status='running'",
                    ('Previous owner exited without a final outcome. Remote completion is unknown; no retry was sent.', stamp()))
                self.db.execute("UPDATE model_attempts SET state='interrupted' WHERE state='reserved'")
                self.db.execute("UPDATE reference_reads SET state='interrupted' WHERE state='started'")
                self.db.execute("UPDATE mcp_calls SET status='uncertain' WHERE status='started'")
                self.db.execute("UPDATE background_mcp_calls SET status='uncertain' WHERE status='started'")
                self._history.recover()
                self.jobs.recover(stamp())
        except Exception:
            self.close()
            raise

    def _verify_expected_state(self, expected: str):
        # The cooperating-owner lock holds the file stable during this short
        # read, and state_metadata is write-once. Ordinary SQLite reads may
        # recover a hot journal: this identity-only connection must not do so.
        # Never use the immutable view for archive data or recovery decisions.
        try:
            with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro&immutable=1',
                                         uri=True, timeout=0)) as probe:
                row = probe.execute('SELECT state_id FROM state_metadata WHERE singleton=1').fetchone()
        except sqlite3.Error:
            raise Failure('state_identity', 'State identity is unreadable; no recovery was attempted.') from None
        if row is None or not self._identity(row[0]) or row[0] != expected:
            raise Failure('state_identity', 'State identity changed; explicit operator selection required. No recovery was attempted.')

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.lock is not None:
            self.lock.close()
            self.lock = None

    def _put(self, kind: str, body: str) -> str:
        ident = uuid.uuid4().hex
        raw = body.encode('utf-8')
        digest = hashlib.sha256(raw).hexdigest()
        self.db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?)',
            (ident, kind, digest, len(raw), stamp()))
        self.db.execute('INSERT INTO artifact_payloads VALUES(?,?)', (ident, body))
        # Integrity metadata, not another body copy. Page reads verify at most
        # two fixed-size chunks even when the original answer is much larger.
        self.db.executemany('INSERT INTO artifact_chunks VALUES(?,?,?)',
            ((ident, start // BODY_CHUNK_BYTES, hashlib.sha256(digest.encode() + raw[start:start + BODY_CHUNK_BYTES]).hexdigest())
             for start in range(0, len(raw), BODY_CHUNK_BYTES)))
        return ident

    def _body(self, ident: str | None) -> str | None:
        if ident is None:
            return None
        row = self.db.execute('SELECT a.sha256,p.body FROM artifacts a JOIN artifact_payloads p ON p.artifact_id=a.id WHERE a.id=?', (ident,)).fetchone()
        if row is None or hashlib.sha256(row['body'].encode('utf-8')).hexdigest() != row['sha256']:
            raise Failure('store_integrity', 'An exact payload is missing or corrupt.')
        return row['body']

    @property
    def state_id(self) -> str:
        row = self.db.execute('SELECT state_id FROM state_metadata WHERE singleton=1').fetchone()
        if row is None or not self._identity(row['state_id']):
            raise Failure('store_integrity', 'Development state identity is missing or invalid.')
        return row['state_id']

    async def maintain_memory(self):
        """One bounded idle batch, sharing the selected conversation's limits."""
        if self.active_turn_id() is not None:
            return
        # Warm the configured local encoder before allocating a conversation
        # ledger. This runs under NativeApplication's cancellable idle owner;
        # an empty new archive can therefore prepare without creating a turn.
        warmup = getattr(self.recall.embedding, 'warmup', None)
        if warmup is not None:
            await warmup()
        if self.active_turn_id() is not None:
            return
        row = self.db.execute('''SELECT c.id FROM memory_grants g JOIN conversations c ON c.id=g.conversation_id
            JOIN artifact_payloads p ON p.artifact_id=c.config_id
            WHERE g.enabled=1 AND json_extract(p.body,'$.hybrid_memory')=1 LIMIT 1''').fetchone()
        if row is None:
            return
        conv = self.conversation(row[0])
        ledger = Ledger(conv.profile,time.monotonic(),with_history=True,with_memory=True,
            history_policy=conv.history_policy,hybrid_memory=True)
        snapshot = self.memory.snapshot(conv.id,conv.memory_bank_id,ledger)
        await self.recall.prepare(snapshot,'',ledger)

    def conversation_message_byte_limit(self, conversation_id):
        from ...core.records import message_byte_limit
        # Metadata only: do not load system prompts or message bodies for the composer.
        row = self.db.execute("""SELECT json_extract(p.body,'$.profile.context_limit')
            FROM conversations c JOIN artifact_payloads p ON p.artifact_id=c.config_id
            WHERE c.id=?""", (conversation_id,)).fetchone()
        if row is None:
            raise Failure('conversation_missing', 'This conversation is unavailable.')
        return message_byte_limit(row[0])

    def maximum_profile_context(self):
        row = self.db.execute('''SELECT MAX(json_extract(p.body,'$.profile.context_limit'))
            FROM conversations c JOIN artifact_payloads p ON p.artifact_id=c.config_id''').fetchone()
        return row[0] or 0

    @staticmethod
    def _identity(value) -> bool:
        return isinstance(value, str) and len(value) == 32 and all(c in '0123456789abcdef' for c in value)

    @staticmethod
    def _sha256(value) -> bool:
        return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)

    def _mutation(self, kind: str, mutation_key: str):
        if kind not in ('conversation.create', 'turn.submit') or not self._identity(mutation_key):
            raise Failure('invalid_request', 'Mutation kind or key is invalid; nothing was saved.')
        if kind == 'turn.submit' and self.db.execute(
                'SELECT 1 FROM exchange_removed_submissions WHERE mutation_key=?', (mutation_key,)).fetchone():
            raise Failure('exchange_removed', 'This submission was permanently removed; it cannot be replayed.')
        return self.db.execute('SELECT * FROM native_mutations WHERE kind=? AND mutation_key=?',
                               (kind, mutation_key)).fetchone()

    def lookup_mutation(self, kind: str, mutation_key: str) -> dict:
        if kind == 'turn.submit':
            removed = self.db.execute('SELECT * FROM exchange_removed_submissions WHERE mutation_key=?', (mutation_key,)).fetchone()
            if removed:
                return dict(removed) | {'status': 'removed', 'kind': kind}
        row = self._mutation(kind, mutation_key)
        if row is None:
            return {'status': 'absent'}
        result = {'status': 'accepted', 'kind': kind, 'mutation_key': mutation_key,
                  'intent_sha256': row['intent_sha256']}
        if row['conversation_id'] is not None:
            result['conversation_id'] = row['conversation_id']
        if row['turn_id'] is not None:
            result['turn_id'] = row['turn_id']
            result['turn_status'] = self._row(row['turn_id'])['status']
        return result

    def record_recall_admission(self, turn_id, result):
        from ...core.tools import result_receipt
        with self.db:
            self.db.execute('INSERT INTO recall_admissions VALUES(?,?)',
                            (turn_id, json.dumps(result_receipt(result), ensure_ascii=False)))

    def create_conversation(self, profile: Profile, system_prompt: str, *, reference_body: str | None = None,
                            history_enabled: bool = False, history_policy: HistoryPolicy | None = None,
                            memory_enabled: bool = False, notes_enabled: bool = False, hybrid_memory: bool = False,
                            tasks_enabled: bool = False, request_counter=None, provider_first: bool = False,
                            mutation_key: str | None = None, intent_sha256: str | None = None) -> str:
        # Reject impossible new configurations before writing any artifact. Reads
        # of existing conversations remain available, including older bad configs.
        policy = select_history_policy(history_policy, enabled=history_enabled)
        if type(memory_enabled) is not bool or memory_enabled and not history_enabled:
            raise ValueError('memory requires an explicit history execution grant')
        if type(notes_enabled) is not bool or notes_enabled and not memory_enabled:
            raise ValueError('notes require an explicit memory grant')
        if type(hybrid_memory) is not bool or hybrid_memory and not memory_enabled:
            raise ValueError('hybrid recall requires an explicit memory grant')
        if type(tasks_enabled) is not bool or tasks_enabled and not memory_enabled:
            raise ValueError('tasks require an explicit memory grant')
        if type(provider_first) is not bool:
            raise ValueError('provider-first admission requires an explicit adapter capability')
        if mutation_key is not None:
            prior = self._mutation('conversation.create', mutation_key)
            if prior is not None:
                if prior['intent_sha256'] != intent_sha256:
                    raise Failure('mutation_conflict', 'Mutation key was already used for a different conversation intent.')
                # A committed creation is a durable receipt, not a new admission.
                # Counter availability may change after restart; resolve the same
                # accepted intent without rerunning it or rewriting configuration.
                return prior['conversation_id']
            if not self._sha256(intent_sha256):
                raise Failure('invalid_request', 'Conversation intent digest is invalid; nothing was saved.')
        ledger = Ledger(profile, 0.0, with_reference=reference_body is not None, with_history=history_enabled,
               history_policy=policy, with_memory=memory_enabled,
               with_notes=notes_enabled, notes_write=notes_enabled,hybrid_memory=hybrid_memory,
               with_tasks=tasks_enabled)
        if request_counter is not None or provider_first:
            # Trusted adapter capability, never a saved profile setting or a
            # caller-supplied token total. Charge the complete current schema
            # through the same owner as turn admission; no artifacts exist yet.
            ledger.accounting = RequestAccounting(profile, request_schema(
                history=history_enabled, reference=reference_body is not None, memory=memory_enabled,
                notes=notes_enabled, notes_write=notes_enabled, hybrid=hybrid_memory, tasks=tasks_enabled),
                counter=request_counter, provider_first=provider_first)
        ledger.validate_configuration(system_prompt)
        if reference_body is not None and (not isinstance(reference_body, str)
                or len(reference_body.encode()) > 4 * profile.context_limit):
            raise Failure('input_size', 'Attached reference exceeds its explicit snapshot bound; nothing was saved.')
        ident = uuid.uuid4().hex
        with self.db:
            value = {'profile': asdict(profile), 'system_prompt': system_prompt}
            if history_enabled:
                value['history_enabled'] = True
                value['history_policy'] = policy.value
            if notes_enabled:
                value['notes_enabled'] = True
            if tasks_enabled:
                value['tasks_enabled'] = True
            if reference_body is not None:
                value['reference_id'] = self._put('reference', reference_body)
            if memory_enabled:
                bank = self.db.execute('SELECT id FROM memory_banks WHERE slot=1').fetchone()
                if bank is None:
                    raise Failure('memory_unavailable', 'Candidate memory bank is unavailable; nothing was saved.')
                value['memory_bank_id'] = bank[0]
                value['hybrid_memory'] = hybrid_memory
            config = self._put('configuration', json.dumps(value, ensure_ascii=False))
            self.db.execute('INSERT INTO conversations VALUES(?,?,?)', (ident, config, stamp()))
            self.db.execute('INSERT INTO personal_task_grants VALUES(?,?,0,?)', (ident, int(tasks_enabled), stamp()))
            if mutation_key is not None:
                self.db.execute('INSERT INTO native_mutations VALUES(?,?,?,?,?,?)',
                    ('conversation.create', mutation_key, intent_sha256, ident, None, stamp()))
            if memory_enabled:
                self.db.execute('INSERT INTO memory_grants VALUES(?,?,1,0)', (ident, bank[0]))
            if notes_enabled:
                self.db.execute('INSERT INTO note_write_grants VALUES(?,?,1,0)', (ident, bank[0]))
        return ident

    def conversation(self, conversation_id: str) -> Conversation:
        row = self.db.execute('SELECT config_id FROM conversations WHERE id=?', (conversation_id,)).fetchone()
        if row is None:
            raise Failure('missing_conversation', 'Conversation does not exist in this candidate store.')
        config = json.loads(self._body(row['config_id']))
        enabled = config.get('history_enabled', False)
        try:
            # Never guess a saved policy. A missing grant's bound must
            # not silently become either a wider runtime or the R007 fixture.
            if enabled and config.get('history_policy') is None:
                raise ValueError('missing execution policy')
            policy = select_history_policy(config.get('history_policy'), enabled=enabled)
            bank = config.get('memory_bank_id')
            hybrid = config.get('hybrid_memory', False)
            if type(hybrid) is not bool or hybrid and bank is None:
                raise ValueError('invalid hybrid memory grant')
            notes = config.get('notes_enabled', False)
            task_grant = self.db.execute('SELECT enabled FROM personal_task_grants WHERE conversation_id=?',
                                         (conversation_id,)).fetchone()
            if task_grant is not None and task_grant[0] not in (0, 1):
                raise ValueError('invalid task capability record')
            # A missing grant denies task tools, not access to retained chat.
            # Never fall back to the immutable creation flag as live authority.
            tasks = task_grant is not None and bool(task_grant[0])
            if tasks and bank is None:
                raise ValueError('invalid task capability')
            if type(notes) is not bool or notes and bank is None:
                raise ValueError('invalid note grant configuration')
            if bank is not None and (not enabled or not isinstance(bank, str) or len(bank) != 32
                                     or any(c not in '0123456789abcdef' for c in bank)):
                raise ValueError('invalid memory grant')
        except (ValueError, TypeError):
            raise Failure('store_integrity', 'Saved history execution policy is missing or invalid.') from None
        writable = notes and self.db.execute('''SELECT 1 FROM note_write_grants n JOIN memory_grants m
            ON n.conversation_id=m.conversation_id AND n.bank_id=m.bank_id
            WHERE n.conversation_id=? AND n.bank_id=? AND n.enabled=1 AND m.enabled=1''',
            (conversation_id, bank)).fetchone() is not None
        reminder_grant = self.db.execute('SELECT enabled FROM reminder_grants WHERE conversation_id=?', (conversation_id,)).fetchone()
        people_grant = self.db.execute('SELECT mode FROM people_grants WHERE conversation_id=?', (conversation_id,)).fetchone()
        reminders = bool(reminder_grant and reminder_grant[0])
        people = people_grant[0] if people_grant else 'off'
        # Global choices govern existing and future ordinary chats, but cannot
        # create a missing memory/history grant or widen source eligibility.
        available = bool(enabled and bank is not None and self.db.execute(
            'SELECT 1 FROM memory_grants WHERE conversation_id=? AND bank_id=? AND enabled=1',
            (conversation_id, bank)).fetchone())
        access = self.access_settings
        if access.value['tasks'] is not None:
            tasks = available and access.resolve('tasks', tasks)
        if access.value['reminders'] is not None:
            reminders = available and access.resolve('reminders', reminders)
        if access.value['people'] is not None:
            people = access.resolve('people', people) if available else 'off'
        return Conversation(conversation_id, Profile(**config['profile']), config['system_prompt'],
                            config.get('reference_id'), enabled, policy, bank, notes, writable,hybrid,tasks,
                            reminders, people)

    def start(self, conversation_id: str, user: str, *, mutation_key: str | None = None,
              intent_sha256: str | None = None, expected_previous_turn_id: str | None = None, selected_skill=None) -> str:
        require_writable(self, conversation_id)
        conversation = self.conversation(conversation_id)
        if mutation_key is not None:
            prior = self._mutation('turn.submit', mutation_key)
            if prior is not None:
                if prior['intent_sha256'] != intent_sha256 or prior['conversation_id'] != conversation_id:
                    raise Failure('mutation_conflict', 'Mutation key was already used for a different turn intent.')
                return prior['turn_id']
            if not self._sha256(intent_sha256):
                raise Failure('invalid_request', 'Turn intent digest is invalid; nothing was saved.')
            previous = self.db.execute('SELECT id FROM turns WHERE conversation_id=? ORDER BY sequence DESC LIMIT 1',
                                       (conversation_id,)).fetchone()
            actual = previous['id'] if previous else None
            if expected_previous_turn_id != actual:
                raise Failure('predecessor_conflict', 'Conversation changed before this turn was admitted; nothing was saved.')
            if self.db.execute("SELECT 1 FROM turns WHERE status='running'").fetchone() is not None:
                raise Failure('turn_busy', 'The development backend already has a foreground turn; nothing was saved.')
        ident = uuid.uuid4().hex
        try:
            with self.db:
                body = self._put('user_message', user)
                # Never reuse a deleted last position: delayed delivery anchors
                # and pagination retain their original sequence identity.
                self.db.execute("""INSERT INTO turns(id,conversation_id,user_id,status,created_at,sequence)
                    SELECT ?,?,?,'running',?,max(coalesce((SELECT max(sequence) FROM turns),0),
                    (SELECT sequence FROM exchange_sequence_floor WHERE singleton=1))+1""",
                    (ident, conversation_id, body, stamp()))
                if mutation_key is not None:
                    self.db.execute('INSERT INTO native_mutations VALUES(?,?,?,?,?,?)',
                        ('turn.submit', mutation_key, intent_sha256, conversation_id, ident, stamp()))
                if conversation.history_enabled:
                    self._history.start(self._row(ident))
                self.execution.admit(ident, conversation)
                self.skills.select(ident, selected_skill)
        except sqlite3.IntegrityError as error:
            raise Failure('turn_busy', 'This conversation already has a running turn; nothing was saved.') from error
        return ident

    def _row(self, turn_id: str):
        row = self.db.execute('SELECT * FROM turns WHERE id=?', (turn_id,)).fetchone()
        if row is None:
            raise Failure('missing_turn', 'Turn does not exist.')
        return row

    def history(self, turn_id: str, allowance: int) -> tuple[Message, ...]:
        current = self._row(turn_id)
        conversation = self.conversation(current['conversation_id'])
        rows = self.db.execute('SELECT * FROM recall_turns WHERE conversation_id=? AND sequence<? ORDER BY sequence',
            (current['conversation_id'], current['sequence']))
        messages = []
        for row in rows:
            additions = [Message('user', self._body(row['user_id']))]
            if row['status'] == 'completed':
                answer = self._body(row['answer_id'])
                reasoning = None
                if conversation.needs_reasoning_history:
                    # The empty-state frame plus raw bytes is a lower bound on
                    # escaped transport cost. Reject before loading an oversized
                    # state, then charge its exact representation below.
                    room = (allowance - message_cost(additions[0])
                            - message_cost(Message('assistant', answer, reasoning='')))
                    reasoning = self._reasoning(row, conversation, room)
                additions.append(Message('assistant', answer, reasoning=reasoning))
            else:
                additions.append(Message('system', f"Application record: preceding request ended {row['status']} ({row['reason']}); no completed assistant answer was delivered."))
            for message in additions:
                allowance -= message_cost(message)
                if allowance < 0:
                    raise Failure('context_limit', 'Exact conversation history does not fit with the saved generation reserve; no model call was sent. Start a new development conversation; originals remain readable.')
                messages.append(message)
        return tuple(messages)

    def _reasoning(self, turn, conversation, allowance, *, ledger=None):
        row = self.db.execute('''SELECT s.*,a.kind,a.bytes,a.sha256,
            p.rowid AS payload_rowid,typeof(p.body) AS payload_type,c.config_id AS current_config
            FROM provider_states s
            LEFT JOIN artifacts a ON a.id=s.reasoning_id
            LEFT JOIN artifact_payloads p ON p.artifact_id=s.reasoning_id
            JOIN conversations c ON c.id=? WHERE s.turn_id=?''',
            (conversation.id, turn['id'])).fetchone()
        if row is None:
            raise Failure('provider_state_unavailable',
                'A completed answer is missing required provider state; no model call was sent. Originals remain readable.')
        fields = (row['turn_id'], row['answer_id'], row['config_id'], row['reasoning_id'], row['format'])
        if (row['answer_id'] != turn['answer_id'] or row['config_id'] != row['current_config']
                or row['format'] != provider(conversation.profile.provider).reasoning_state_format
                or row['binding_sha256'] != state_binding(*fields)
                or row['kind'] != 'provider_reasoning' or row['payload_type'] != 'text'
                or type(row['bytes']) is not int
                or not 0 <= row['bytes'] <= Ledger(conversation.profile, 0).response_bytes):
            raise Failure('provider_state_integrity', 'Required provider-state identity or envelope is invalid; no model call was sent.')
        if row['bytes'] > allowance:
            raise Failure('context_limit', 'Exact history and required provider state do not fit with the saved generation reserve; no model call was sent. Originals remain readable.')
        # Incremental SQLite access checks byte length before reading/decoding
        # TEXT. A corrupt undersized envelope cannot force an unbounded load.
        if ledger is not None:
            ledger.spend_payload(row['bytes'])
        with self.db.blobopen('artifact_payloads', 'body', row['payload_rowid'], readonly=True) as blob:
            if len(blob) != row['bytes']:
                raise Failure('provider_state_integrity', 'Required provider-state payload size is invalid; no model call was sent.')
            raw = blob.read(row['bytes'])
        if hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise Failure('provider_state_integrity', 'Required provider-state payload is corrupt; no model call was sent.')
        try:
            return raw.decode('utf-8')
        except UnicodeError as error:
            raise Failure('provider_state_integrity', 'Required provider-state encoding is invalid; no model call was sent.') from error

    def _save_reasoning(self, turn, answer_id, reply):
        conversation = self.conversation(turn['conversation_id'])
        if not conversation.needs_reasoning_history:
            return
        if (reply.finish_reason != 'stop' or reply.tool_calls or not isinstance(reply.reasoning, str)
                or len((reply.text + reply.reasoning).encode()) > Ledger(conversation.profile, 0).response_bytes):
            raise Failure('provider_state_invalid', 'No complete bounded provider state was available; no completed answer was saved.')
        config_id = self.db.execute('SELECT config_id FROM conversations WHERE id=?', (conversation.id,)).fetchone()[0]
        reasoning_id = self._put('provider_reasoning', reply.reasoning)
        # Each supplier's registered format keeps prior DeepSeek bindings exact.
        fields = (turn['id'], answer_id, config_id, reasoning_id,
                  provider(conversation.profile.provider).reasoning_state_format)
        self.db.execute('INSERT INTO provider_states VALUES(?,?,?,?,?,?)', (*fields, state_binding(*fields)))

    def dispatched(self, turn_id: str, input_estimate: int, call_number: int = 1):
        maximum = self.execution.for_turn(turn_id).calls
        if type(call_number) is not int or not 1 <= call_number <= maximum:
            raise Failure('call_limit', 'Only the bounded working/final attempts are supported.')
        with self.db:
            changed = self.db.execute("UPDATE turns SET calls=?,input_estimate=? WHERE id=? AND status='running' AND calls=?",
                (call_number, input_estimate, turn_id, call_number - 1)).rowcount
            if changed != 1:
                raise Failure('turn_state', 'Turn cannot reserve another provider attempt.')
            self.db.execute('INSERT INTO model_attempts(turn_id,ordinal,input_estimate,state) VALUES(?,?,?,?)',
                (turn_id, call_number, input_estimate, 'reserved'))

    def observed(self, turn_id: str, call_number: int, reply: Reply) -> None:
        with self.db:
            changed = self.db.execute("UPDATE model_attempts SET input_tokens=?,output_tokens=?,finish_reason=?,state='received' WHERE turn_id=? AND ordinal=? AND state='reserved'",
                (reply.input_tokens, reply.output_tokens, reply.finish_reason, turn_id, call_number)).rowcount
            if changed != 1:
                raise Failure('turn_state', 'Provider receipt has no matching reserved attempt.')

    def context_event(self, turn_id: str, record: dict) -> None:
        with self.db:
            self.db.execute('INSERT INTO context_events(turn_id,record) VALUES(?,?)',
                (turn_id, json.dumps(record, ensure_ascii=True, separators=(',', ':'))))

    def context_rejected(self, turn_id: str, call_number: int) -> None:
        with self.db:
            changed = self.db.execute("UPDATE model_attempts SET state='context_rejected' WHERE turn_id=? AND ordinal=? AND state='reserved'",
                (turn_id, call_number)).rowcount
            if changed != 1:
                raise Failure('turn_state', 'Context rejection has no matching reserved attempt.')

    def note_argument_failure(self, turn_id, call_number, batch_position, call, validation_reason):
        if call.name in ('write_note', 'note_outcome'):
            self._argument_failure(turn_id, call_number, batch_position, call, validation_reason)

    def memory_argument_failure(self, turn_id, call_number, batch_position, call, validation_reason):
        if (call.name in ('search_memory', 'read_memory')
                and validation_reason in MEMORY_ARGUMENT_REASONS):
            self._argument_failure(turn_id, call_number, batch_position, call, validation_reason)

    def history_argument_failure(self, turn_id, call_number, batch_position, call, validation_reason):
        if (call.name in ('search_history', 'read_history')
                and validation_reason in HISTORY_ARGUMENT_REASONS):
            self._argument_failure(turn_id, call_number, batch_position, call, validation_reason)

    def _argument_failure(self, turn_id, call_number, batch_position, call, validation_reason):
        """Private refusal evidence, never a note outcome or model-facing artifact.

        First refusal per turn across notes, memory and history, attached to its already
        received attempt. Retain the schema-11 note_failure slot; it remains
        private, not a second history/body store or a model-visible projection.
        Native decoding already bounds these fields. Keep the smaller existing
        private capture allowance even when a full authored save may be larger.
        Never truncate, recode arguments, or inspect the full reply.
        """
        bound = NotePolicy().argument_bytes
        if (not isinstance(call.id, str) or not 1 <= len(call.id) <= 256
                or any(ord(c) < 33 or ord(c) > 126 for c in call.id)
                or not isinstance(validation_reason, str) or not 1 <= len(validation_reason) <= 64
                or any(c not in 'abcdefghijklmnopqrstuvwxyz_0123456789' for c in validation_reason)
                or type(batch_position) is not int or not 1 <= batch_position <= self.execution.for_turn(turn_id).operations):
            return
        raw = None
        if isinstance(call.arguments, str) and len(call.arguments) <= bound:
            try:
                raw = call.arguments.encode('utf-8')
            except UnicodeError:
                pass
        captured = raw is not None and len(raw) <= bound
        evidence = dict(validation_reason=validation_reason, tool_name=call.name,
            call_id=call.id, batch_position=batch_position,
            arguments_status='exact' if captured else 'outside_diagnostic_bound',
            arguments=call.arguments if captured else None,
            arguments_sha256=hashlib.sha256(raw).hexdigest() if captured else None)
        with self.db:
            self.db.execute('''UPDATE model_attempts SET note_failure=?
                WHERE turn_id=? AND ordinal=? AND state='received'
                AND finish_reason='tool_calls' AND note_failure IS NULL
                AND EXISTS(SELECT 1 FROM turns WHERE id=? AND status='running')
                AND NOT EXISTS(SELECT 1 FROM model_attempts WHERE turn_id=? AND note_failure IS NOT NULL)''',
                (json.dumps(evidence, ensure_ascii=True, separators=(',', ':')),
                 turn_id, call_number, turn_id, turn_id))

    def read_reference(self, conversation_id: str, offset: int, maximum: int, deadline: float) -> ReferencePage:
        if time.monotonic() >= deadline:
            raise Failure('timeout', 'Reference read inherited an expired deadline.')
        conversation = self.conversation(conversation_id)
        aid = conversation.reference_id
        row = self.db.execute('SELECT kind,bytes FROM artifacts WHERE id=?', (aid,)).fetchone()
        if row is None or row['kind'] != 'reference' or row['bytes'] > 4 * conversation.profile.context_limit:
            raise Failure('reference_unavailable', 'This conversation has no valid explicitly attached reference.')
        self.db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        try:
            page = exact_page(aid, self._body(aid), offset, maximum)
        except sqlite3.OperationalError as error:
            if time.monotonic() >= deadline:
                raise Failure('timeout', 'Reference read reached the inherited deadline.') from error
            raise
        finally:
            self.db.set_progress_handler(None, 0)
        if time.monotonic() >= deadline:
            raise Failure('timeout', 'Reference read reached the inherited deadline; no page was delivered.')
        return page

    def start_read(self, turn_id: str, call: ToolCall) -> None:
        turn = self._row(turn_id)
        aid = self.conversation(turn['conversation_id']).reference_id
        if not aid or turn['status'] != 'running' or turn['calls'] != 1:
            raise Failure('tool_limit', 'No read is permitted in this turn phase.')
        try:
            with self.db:
                self.db.execute('INSERT INTO reference_reads VALUES(?,?,?,?,?,NULL)',
                    (turn_id, aid, call.id, call.arguments, 'started'))
        except sqlite3.IntegrityError as error:
            raise Failure('tool_limit', 'A reference read was already reserved; no read was repeated.') from error

    def finish_read(self, turn_id: str, page: ReferencePage) -> None:
        result = page.envelope()
        del result['content']  # Exact source owns the body; receipt stores range/identity only.
        result['page_sha256'] = page.page_sha256
        with self.db:
            changed = self.db.execute("UPDATE reference_reads SET state='prepared',result=? WHERE turn_id=? AND reference_id=? AND state='started'",
                (json.dumps(result, ensure_ascii=False), turn_id, page.reference_id)).rowcount
            if changed != 1:
                raise Failure('turn_state', 'Reference result has no matching reserved read.')

    def submit_read(self, turn_id: str) -> None:
        with self.db:
            if self.db.execute("UPDATE reference_reads SET state='submitted' WHERE turn_id=? AND state='prepared'", (turn_id,)).rowcount != 1:
                raise Failure('turn_state', 'Reference receipt was not prepared for delivery.')

    def reads(self, turn_id: str) -> tuple[dict, ...]:
        self._row(turn_id)
        return tuple(dict(row) for row in self.db.execute('SELECT * FROM reference_reads WHERE turn_id=?', (turn_id,))) + self._history.operations(turn_id)

    def preview_note_removal(self, conversation_id, note_id):
        return self.note_removal.preview(conversation_id, note_id)

    def remove_note(self, conversation_id, note_id, preview_token, operation_id):
        return self.note_removal.apply(conversation_id, note_id, preview_token, operation_id)

    def note_removal_status(self, conversation_id, operation_id, *, include_intent=False):
        return self.note_removal.status(conversation_id, operation_id, include_intent=include_intent)

    def notebook_page(self, conversation_id, cursor=None, limit=20, collection='current'):
        return self.notebook.page(conversation_id, cursor, limit, collection)

    def notebook_read(self, conversation_id, note_id, revision_id, view_token, offset, maximum):
        return self.notebook.read(conversation_id, note_id, revision_id, view_token, offset, maximum)

    def note_removal_preview_page(self, conversation_id, note_id, start=0, limit=20, expected_token=None):
        return self.note_removal.preview_page(conversation_id, note_id, start, limit, expected_token)

    def history_snapshot(self, turn_id, ledger):
        return self._history.snapshot(turn_id, ledger)

    def delivery_epoch(self):
        return self.db.execute("SELECT coalesce(max(sequence),0) FROM background_deliveries WHERE status='presented'").fetchone()[0]

    def history_unit(self, snapshot, ledger, **selection):
        return self._history.unit(snapshot, ledger, **selection)

    def history_load(self, snapshot, unit, ledger):
        return self._history.load(snapshot, unit, ledger)

    def save_admission(self, snapshot, record):
        self._history.save_admission(snapshot, record)

    def admission(self, turn_id):
        return self._history.admission(turn_id)

    def history_operation(self, snapshot, name, args, ledger, maximum, *, budget=None):
        if name == 'search_history':
            return self._history.search(snapshot, args, ledger, maximum, budget=budget)
        if name == 'read_history':
            return self._history.read(snapshot, args, ledger, maximum)
        if name == 'read_reference':
            return self._history.reference(snapshot, args, ledger, maximum)
        raise Failure('unsupported_tool', 'No such history operation exists.')

    def start_operation(self, snapshot, ordinal, call, *, preflight_rejected=False):
        self._history.start_operation(snapshot, ordinal, call, preflight_rejected=preflight_rejected)

    def prepare_operation(self, snapshot, ordinal, result):
        self._history.prepared(snapshot, ordinal, result)

    def submit_operation(self, snapshot, ordinal):
        self._history.submitted(snapshot, ordinal)

    def note_outcomes(self, turn_id):
        """Bounded explicit inspection, not a foreground loop or authority bypass."""
        row = self._row(turn_id)
        conv = self.conversation(row['conversation_id'])
        if conv.memory_bank_id is None:
            return {'status': 'unavailable', 'reason': 'memory_unavailable'}
        ledger = Ledger(conv.profile, time.monotonic(), with_history=True, with_memory=True,
                        history_policy=conv.history_policy)
        try:
            snapshot = self.memory.snapshot(conv.id, conv.memory_bank_id, ledger)
            return self.notes.inspect_turn(snapshot, turn_id, ledger, limit=self.execution.for_turn(turn_id).operations)
        except Failure as error:
            return {'status': 'unavailable', 'reason': error.code}

    def finish(self, turn_id: str, status: str, *, reason=None, detail=None, reply: Reply | None = None) -> Turn:
        if status not in {'completed', 'failed', 'cancelled'}:
            raise ValueError('invalid terminal status')
        if status == 'completed' and (reply is None or not reply.text.strip()):
            raise ValueError('completed turn requires an answer')
        with self.db:
            turn = self._row(turn_id)
            if turn['status'] != 'running':
                raise Failure('turn_state', 'A terminal turn cannot be overwritten.')
            answer = self._put('assistant_message', reply.text) if status == 'completed' else None
            if answer is not None:
                # Same transaction as the answer/status: no completed answer can
                # become durable here without its required continuation state.
                self._save_reasoning(turn, answer, reply)
            partial = (self._put('partial_response', reply.text) if reply and reply.text and status != 'completed' else None)
            attempts = list(self.db.execute('SELECT input_tokens,output_tokens FROM model_attempts WHERE turn_id=? ORDER BY ordinal', (turn_id,)))
            totals = [(sum(row[i] for row in attempts) if all(row[i] is not None for row in attempts) else None)
                      for i in (0, 1)] if attempts else [reply.input_tokens if reply else None, reply.output_tokens if reply else None]
            self.db.execute('UPDATE turns SET answer_id=?,partial_id=?,status=?,reason=?,detail=?,input_tokens=?,output_tokens=?,finish_reason=?,finished_at=? WHERE id=?',
                (answer, partial, status, reason, detail, *totals, reply.finish_reason if reply else None, stamp(), turn_id))
            self._history.finish(self._row(turn_id), reply)
            self.db.execute("UPDATE history_operations SET state='failed',result=? WHERE turn_id=? AND state='started' AND name!='write_note'",
                (json.dumps({'status': 'not_delivered', 'reason': reason or 'operation_not_completed'}), turn_id))
            self.db.execute("UPDATE history_operations SET state='failed',result=? WHERE turn_id=? AND state='started' AND name='write_note'",
                (json.dumps({'status': 'delivery_unknown', 'reason': reason or 'operation_not_completed',
                             'effect': 'inspect_durable_note_outcome'}), turn_id))
            self.db.execute("UPDATE reference_reads SET state='not_delivered',result=? WHERE turn_id=? AND state='started'",
                (json.dumps({'status': 'not_delivered', 'reason': reason or 'read_not_completed'}), turn_id))
            # A terminal local failure is not an in-flight attempt. Preserve
            # unknown remote usage rather than inventing a received completion.
            self.db.execute("UPDATE model_attempts SET state='unobserved' WHERE turn_id=? AND state='reserved'", (turn_id,))
        return self._turn(self._row(turn_id))

    def _turn(self, row) -> Turn:
        return Turn(row['id'], row['conversation_id'], row['status'], self._body(row['user_id']),
            self._body(row['answer_id']), self._body(row['partial_id']), row['reason'], row['detail'], row['calls'], row['sequence'])

    def turn(self, turn_id: str) -> Turn:
        return self._turn(self._row(turn_id))

    def conversation_summaries(self, *, before_created: str | None = None,
                               before_id: str | None = None, limit: int = 20) -> tuple[dict, ...]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise Failure('invalid_request', 'Conversation page size is invalid.')
        if (before_created is None) != (before_id is None):
            raise Failure('invalid_cursor', 'Conversation cursor is incomplete.')
        query = 'SELECT id,created_at,config_id FROM conversations'
        args = []
        if before_created is not None:
            if not isinstance(before_created, str) or not self._identity(before_id):
                raise Failure('invalid_cursor', 'Conversation cursor is invalid.')
            query += ' WHERE (created_at,id)<(?,?)'
            args.extend((before_created, before_id))
        query += ' ORDER BY created_at DESC,id DESC LIMIT ?'
        args.append(limit)
        return tuple(dict(row) for row in self.db.execute(query, args))

    def turn_metadata_page(self, conversation_id: str, *, before_sequence: int | None = None,
                           limit: int = 20) -> tuple[dict, ...]:
        if not self._identity(conversation_id) or self.db.execute(
                'SELECT 1 FROM conversations WHERE id=?', (conversation_id,)).fetchone() is None:
            raise Failure('missing_conversation', 'Conversation does not exist in this candidate store.')
        if type(limit) is not int or not 1 <= limit <= 50:
            raise Failure('invalid_request', 'Turn page size is invalid.')
        if before_sequence is not None and (type(before_sequence) is not int or before_sequence < 1):
            raise Failure('invalid_cursor', 'Turn cursor is invalid.')
        query = '''SELECT sequence,id,conversation_id,status,user_id,answer_id,partial_id,reason,detail,
                   calls,input_estimate,input_tokens,output_tokens,finish_reason,created_at,finished_at
                   FROM turns WHERE conversation_id=?'''
        args = [conversation_id]
        if before_sequence is not None:
            query += ' AND sequence<?'
            args.append(before_sequence)
        query += ' ORDER BY sequence DESC LIMIT ?'
        args.append(limit)
        rows = [dict(row) for row in self.db.execute(query, args)]
        rows.reverse()
        return tuple(rows)

    def turn_metadata(self, turn_id: str) -> dict:
        row = self._row(turn_id)
        return dict(row)

    def active_turn_id(self):
        # The sequence ordering otherwise tempts SQLite to scan every completed
        # turn on each background authority check. Only running rows can matter.
        row = self.db.execute("SELECT id FROM turns INDEXED BY one_running_turn WHERE status='running' ORDER BY sequence LIMIT 1").fetchone()
        return row['id'] if row else None

    def imported_conversation(self, conversation_id):
        return imported(self, conversation_id) is not None

    def require_writable_conversation(self, conversation_id):
        require_writable(self, conversation_id)

    def configuration_id(self, conversation_id):
        return self.db.execute('SELECT config_id FROM conversations WHERE id=?', (conversation_id,)).fetchone()[0]

    def inspect_turn(self, turn_id: str) -> tuple[dict, ...]:
        row = self._row(turn_id)
        if imported(self, row["conversation_id"]):
            return ()  # No new-runtime usage, configuration or tool receipts existed.
        # Select only the saved profile projection; no prompt or transcript body
        # is materialized to produce ordinary inspection metadata.
        config = self.db.execute('''SELECT json_extract(p.body,'$.profile') AS profile,
            json_extract(p.body,'$.notes_enabled') AS notes FROM conversations c
            JOIN artifact_payloads p ON p.artifact_id=c.config_id WHERE c.id=?''', (row['conversation_id'],)).fetchone()
        admission = self.admission(turn_id)
        admission_summary = None
        if admission:
            admitted = len(admission.get('admitted_ids', []))
            admission_summary = {key: admission.get(key) for key in
                ('policy', 'status', 'reason', 'input_estimate', 'schema_estimate', 'notice_estimate')}
            admission_summary.update(earlier_turns=admission['earlier_turns'], admitted_turns=admitted,
                omitted_turns=admission.get('omitted_count', admission['earlier_turns'] - admitted))
        attempts = [dict(kind='model_attempt', ordinal=row['ordinal'], state=row['state'],
                         input_estimate=row['input_estimate'], input_tokens=row['input_tokens'],
                         output_tokens=row['output_tokens'], finish_reason=row['finish_reason'])
                    for row in self.db.execute('''SELECT ordinal,input_estimate,input_tokens,output_tokens,
                        finish_reason,state FROM model_attempts WHERE turn_id=? ORDER BY ordinal''', (turn_id,))]
        operations = [dict(kind='tool_delivery', ordinal=row['ordinal'], call_id=row['call_id'], name=row['name'], state=row['state'])
                      for row in self.db.execute('''SELECT ordinal,call_id,name,state FROM history_operations
                          WHERE turn_id=? ORDER BY ordinal''', (turn_id,))]
        summary = dict(kind='summary', profile=json.loads(config['profile']),
            estimator_label='Per-request input; reply reservation separate. Turn usage is cumulative. See accounting method.',
            admission=admission_summary, operations=len(operations))
        note_items = []
        if config['notes']:
            # Existing inspection rechecks current bank/revision authority and
            # exact receipts. Only its public metadata projection crosses native.
            notes = self.note_outcomes(turn_id)
            note_items.append(dict(kind='notes_access', status=notes['status'], reason=notes.get('reason')))
            for outcome in notes.get('outcomes', []):
                note_items.append(dict(kind='note_outcome', **{key: outcome.get(key) for key in
                    ('status', 'operation_key', 'note_id', 'revision_id', 'sha256', 'reason')}))
        context = [json.loads(row[0]) for row in self.db.execute(
            'SELECT record FROM context_events WHERE turn_id=? ORDER BY sequence', (turn_id,))]
        return tuple([summary] + attempts + context + operations + note_items)

    def context_usage(self, turn_id: str):
        """Small latest-request projection; never load inspection/body pages.

        Cumulative turn tokens are not occupancy. Use the actual last attempt,
        even if its usage is unknown, and its recorded allocation rather than a
        profile that the user may since have edited.
        """
        row = self.db.execute('''SELECT a.input_tokens,a.output_tokens,a.state,
            (SELECT json_extract(e.record,'$.context_limit') FROM context_events e
             WHERE e.turn_id=a.turn_id AND json_extract(e.record,'$.kind')='context_accounting'
             AND json_extract(e.record,'$.ordinal')=a.ordinal
             ORDER BY e.sequence DESC LIMIT 1) AS context_limit
            FROM model_attempts a WHERE a.turn_id=? ORDER BY a.ordinal DESC LIMIT 1''', (turn_id,)).fetchone()
        if (row is None or row['state'] != 'received'
                or any(type(row[key]) is not int or row[key] < 0 for key in ('input_tokens','output_tokens','context_limit'))
                or not row['context_limit']):
            return None
        return {key: row[key] for key in ('input_tokens','output_tokens','context_limit')}

    def body_view(self, conversation_id, turn_id, body_id, offset, maximum):
        return self._images.page(conversation_id, turn_id, body_id, offset, maximum)

    def image_read(self, conversation_id, turn_id, body_id, index, source_sha256):
        return self._images.image(conversation_id, turn_id, body_id, index, source_sha256)

    def body_page(self, conversation_id: str, turn_id: str, body_id: str,
                  offset: int, maximum: int) -> dict:
        if not self._identity(body_id) or type(offset) is not int or offset < 0 \
                or type(maximum) is not int or not 1 <= maximum <= 32768:
            raise Failure('invalid_request', 'Visible body page arguments are invalid.')
        row = self.db.execute('''SELECT t.user_id,t.answer_id,t.partial_id,a.kind,a.bytes,a.sha256,p.rowid AS payload_rowid
            FROM turns t JOIN artifacts a ON a.id=? JOIN artifact_payloads p ON p.artifact_id=a.id
            WHERE t.id=? AND t.conversation_id=? AND a.id IN (t.user_id,t.answer_id,t.partial_id)''',
            (body_id, turn_id, conversation_id)).fetchone()
        if row is None or row['kind'] not in ('user_message', 'assistant_message', 'partial_response'):
            raise Failure('body_unavailable', 'Visible body is unavailable in this conversation scope.')
        total = row['bytes']
        if offset > total:
            raise Failure('invalid_request', 'Visible body offset exceeds the exact body extent.')
        with self.db.blobopen('artifact_payloads', 'body', row['payload_rowid'], readonly=True) as blob:
            if len(blob) != total:
                raise Failure('store_integrity', 'Visible body extent does not match its envelope.')
            end = min(total, offset + maximum)
            chunks = []
            for start in range(offset // BODY_CHUNK_BYTES * BODY_CHUNK_BYTES, end, BODY_CHUNK_BYTES):
                blob.seek(start)
                chunk = blob.read(min(BODY_CHUNK_BYTES, total - start))
                saved = self.db.execute('SELECT sha256 FROM artifact_chunks WHERE artifact_id=? AND ordinal=?',
                    (body_id, start // BODY_CHUNK_BYTES)).fetchone()
                if saved is None or hashlib.sha256(row['sha256'].encode() + chunk).hexdigest() != saved['sha256']:
                    raise Failure('store_integrity', 'Visible body integrity verification failed.')
                chunks.append(chunk[max(0, offset - start):min(len(chunk), end - start)])
            raw = b''.join(chunks)
        if offset < total and raw and raw[0] & 0xC0 == 0x80:
            raise Failure('invalid_request', 'Visible body offset is not a UTF-8 boundary.')
        while raw:
            try:
                content = raw.decode('utf-8')
                break
            except UnicodeDecodeError as error:
                if error.reason == 'unexpected end of data':
                    raw = raw[:error.start]
                    continue
                raise Failure('store_integrity', 'Visible body encoding is invalid.') from error
        else:
            content = ''
        if not raw and offset < total:
            raise Failure('invalid_request', 'Body page is too small for the next UTF-8 code point.')
        end = offset + len(raw)
        return {'body_id': body_id, 'kind': row['kind'], 'sha256': row['sha256'],
                'total_bytes': total, 'offset': offset, 'end': end, 'content': content,
                'complete': end == total}

    def turns(self, conversation_id: str, *, after: int = 0, limit: int = 50) -> tuple[Turn, ...]:
        self.conversation(conversation_id)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('invalid history page')
        return tuple(self._turn(row) for row in self.db.execute(
            'SELECT * FROM turns WHERE conversation_id=? AND sequence>? ORDER BY sequence LIMIT ?',
            (conversation_id, after, limit)))
