"""People read authority and receipts inside the shared operation loop."""
import hashlib
import json

from ..core.records import Failure
from ..ports.people import PeoplePort


class PeopleSession:
    def __init__(self, port: PeoplePort, conversation_id, turn_id, ledger):
        if port is None:
            raise Failure('people_unavailable', 'The granted people adapter is unavailable.')
        self.port, self.cid, self.tid, self.ledger = port, conversation_id, turn_id, ledger
        self.revision, self.reads = port.epoch(ledger), set()
        self.check()

    def check(self):
        self.port.guard(self.cid, ledger=self.ledger)
        if self.revision != self.port.epoch(self.ledger):
            raise Failure('people_state_changed', 'People or their source permissions changed; inspect current state in a new turn. Saved effects remain.')

    def observe(self, args, result):
        if args['action'] == 'read' and result.get('status') == 'complete':
            self.reads.update(row['read_ref'] for row in result['items'] if row.get('read_ref'))

    def history_actions(self, turn_id):
        self.check()
        return self.port.history_actions(turn_id, self.ledger)

    def operation(self, args, maximum, budget):
        self.check()
        key = 'people:' + self.tid + ':' + hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        try:
            return self.port.execute(args, conversation_id=self.cid, turn_id=self.tid, operation_key=key,
                                     ledger=self.ledger, maximum=maximum, budget=budget, read_refs=self.reads)
        except Failure as error:
            if error.code in ('history_work_limit', 'timeout', 'people_forbidden', 'people_state_changed'):
                raise
            return dict(domain='people', status='unknown' if error.code in ('people_outcome_unknown', 'people_store_unavailable')
                        else 'rejected', reason=error.code, recovery=error.detail)
        finally:
            self.revision = self.port.epoch(self.ledger)

    def automatic(self, question, maximum):
        """Only indexed matching names; explicit reads own facts and family paths."""
        from ..core.people import terms
        query = ' '.join(terms(question)).encode()[:256].decode('utf-8', errors='ignore')
        if not query or maximum < 1:
            return None
        self.check()
        try:
            result = self.port.execute(dict(action='find',query=query,limit=6),conversation_id=self.cid,
                turn_id=self.tid,operation_key=None,ledger=self.ledger,maximum=maximum)
            return result if result['items'] else None
        except Failure as error:
            if error.code in ('people_result_capacity',):
                return None
            raise
