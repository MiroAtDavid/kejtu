"""Current-turn reminder read authority; no scheduling or storage duplicate."""
from ..core.records import Failure
from ..ports.reminders import RemindersPort


class ReminderSession:
    def __init__(self, port: RemindersPort, conversation_id, turn_id, ledger, *, job_run_id=None):
        if port is None:
            raise Failure('reminder_unavailable', 'The granted reminder adapter is unavailable.')
        self.port, self.cid, self.tid, self.ledger = port, conversation_id, turn_id, ledger
        self.job_run_id = job_run_id
        self.revision = port.epoch()
        self.reads, self.notices = set(), set()
        self.check()

    def check(self):
        self.port.guard(self.cid, self.tid, self.ledger, *(() if self.job_run_id is None else (self.job_run_id,)))
        if self.port.epoch() != self.revision:
            raise Failure('reminder_state_changed', 'Reminders changed outside this turn; inspect current state. Committed effects remain saved.')

    def clear_reads(self):
        self.reads.clear()
        self.notices.clear()

    def history_actions(self, past_turn):
        self.check()
        return self.port.history_actions(past_turn, cid=self.cid, tid=self.tid, ledger=self.ledger)

    def observe(self, args, result):
        if result.get('status') != 'complete':
            return
        if args['action'] == 'read':
            self.reads.add(result['reminder']['read_ref'])
        if args['action'] == 'pending':
            self.notices.update((row['occurrence_id'], row['receipt']) for row in result['items'])

    def operation(self, args, maximum, budget):
        self.check()
        try:
            result = self.port.execute(args, cid=self.cid, tid=self.tid, ledger=self.ledger,
                                       budget=budget, reads=self.reads, notices=self.notices,
                                       **({'job_run_id':self.job_run_id} if self.job_run_id else {}))
            return dict(result, domain='reminders')
        except Failure as error:
            if error.code in ('history_work_limit', 'timeout', 'reminder_forbidden', 'reminder_state_changed'):
                raise
            return dict(status='unknown' if error.code in ('reminder_outcome_unknown', 'reminder_store_unavailable') else 'rejected',
                        reason=error.code, recovery=error.detail, domain='reminders')
        finally:
            self.revision = self.port.epoch()
