"""Task read authority and writes inside the existing foreground turn."""
import hashlib
import json

from ..core.records import Failure
from ..ports.tasks import TasksPort


class TaskSession:
    def __init__(self, port: TasksPort, conversation_id, turn_id, ledger):
        if port is None:
            raise Failure('tasks_unavailable', 'The granted task adapter is unavailable.')
        self.port, self.conversation_id, self.turn_id, self.ledger = port, conversation_id, turn_id, ledger
        self.revision = port.epoch()
        self.reads = set()

    def check(self):
        if self.port.epoch() != self.revision:
            raise Failure('task_state_changed', 'Tasks changed outside this turn; retained actions remain saved. Read current state in a new turn.')

    def observe(self, args, result):
        if result.get('status') != 'complete':
            return
        if args['action'] == 'read':
            self.reads.add(result['task']['read_ref'])
        elif args['action'] == 'list':
            # Lists contain the same complete current records as reads. The
            # operation loop calls observe only after the whole fitted batch
            # is admitted for the next model request, never during execution.
            # Omitted pages and old/history projections confer no authority.
            self.reads.update(item['read_ref'] for item in result['items'])

    def history_actions(self, turn_id):
        return self.port.history_actions(turn_id,self.ledger)

    def operation(self, args, maximum, budget):
        self.check()
        digest = hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        try:
            result = self.port.execute(args, conversation_id=self.conversation_id, turn_id=self.turn_id,
                operation_key='task:'+self.turn_id+':'+digest, ledger=self.ledger,
                maximum=maximum, budget=budget, read_refs=self.reads)
            self.revision = self.port.epoch()
            return result
        except Failure as error:
            if error.code in ('history_work_limit','timeout','task_state_changed'):
                raise
            return dict(status='unknown' if error.code in ('task_outcome_unknown','task_store_unavailable') else 'rejected',
                reason=error.code, recovery=error.detail)
