"""Foreground settings and immutable admission-time limits; no capability grants."""
from ...core.execution import HistoryPolicy, TurnLimits, MAX_FOREGROUND_OPERATIONS, DEFAULT_FOREGROUND_SECONDS
from ...core.records import Failure

EXECUTION_SEED = f'INSERT INTO execution_settings VALUES(1,{MAX_FOREGROUND_OPERATIONS},{DEFAULT_FOREGROUND_SECONDS},0);'

EXECUTION_SCHEMA = f'''
CREATE TABLE execution_settings(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 tool_calls INTEGER NOT NULL CHECK(tool_calls BETWEEN 1 AND 256),
 timeout_seconds INTEGER CHECK(timeout_seconds BETWEEN 1 AND 86400), revision INTEGER NOT NULL);
{EXECUTION_SEED}
CREATE TABLE turn_execution(turn_id TEXT PRIMARY KEY REFERENCES turns(id) ON DELETE CASCADE,
 operations INTEGER NOT NULL CHECK(operations BETWEEN 0 AND 256), seconds REAL NOT NULL CHECK(seconds>0));
'''


class ExecutionStore:
    def __init__(self, store):
        self.store, self.db = store, store.db

    def settings(self):
        row = self.db.execute('SELECT tool_calls,timeout_seconds,revision FROM execution_settings WHERE singleton=1').fetchone()
        return dict(row)

    def save(self, tool_calls, timeout_seconds, expected_revision):
        if (type(tool_calls) is not int or not 1 <= tool_calls <= MAX_FOREGROUND_OPERATIONS
                or timeout_seconds is not None and (type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 86400)
                or type(expected_revision) is not int or expected_revision < 0):
            raise Failure('execution_settings', 'Choose 1–256 tool calls and an optional time limit of 1–86400 seconds.')
        if self.store.active_turn_id() is not None:
            raise Failure('busy', 'Finish or stop the current reply before changing its execution settings.')
        with self.db:
            current = self.settings()
            if current['revision'] != expected_revision:
                raise Failure('settings_conflict', 'Execution settings changed; refresh before saving.')
            changed = (current['tool_calls'], current['timeout_seconds']) != (tool_calls, timeout_seconds)
            self.db.execute('UPDATE execution_settings SET tool_calls=?,timeout_seconds=?,revision=? WHERE singleton=1',
                (tool_calls, timeout_seconds, expected_revision + int(changed)))
            return self.settings()

    def legacy(self, conversation):
        operations = (conversation.history_policy.operations if conversation.history_enabled
                      else 1 if conversation.reference_id else 0)
        return TurnLimits(operations, conversation.profile.timeout_seconds)

    def admit(self, turn_id, conversation):
        # Called inside the same transaction as turn admission. Settings changes
        # never enlarge an already running turn or rewrite historical grants.
        settings = self.settings()
        limits = self.legacy(conversation)
        operations = limits.operations
        if conversation.history_enabled:
            operations = settings['tool_calls']
            if conversation.history_policy is HistoryPolicy.R007:
                operations = min(operations, HistoryPolicy.R007.operations)
        self.db.execute('INSERT INTO turn_execution VALUES(?,?,?)',
            (turn_id, operations, settings['timeout_seconds'] or limits.seconds))

    def for_turn(self, turn_id):
        turn = self.store._row(turn_id)
        conversation = self.store.conversation(turn['conversation_id'])
        row = self.db.execute('SELECT operations,seconds FROM turn_execution WHERE turn_id=?', (turn_id,)).fetchone()
        if row is None:
            return self.legacy(conversation)  # Pre-upgrade turns keep their recorded policy.
        try:
            limits = TurnLimits(row['operations'], row['seconds'])
            ceiling = (MAX_FOREGROUND_OPERATIONS if conversation.history_policy is HistoryPolicy.STANDARD
                       else self.legacy(conversation).operations)
            if limits.operations > ceiling:
                raise ValueError
            return limits
        except ValueError:
            raise Failure('execution_integrity', 'Saved turn limits do not match its execution grant.') from None
