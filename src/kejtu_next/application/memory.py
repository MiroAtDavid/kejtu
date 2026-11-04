"""One granted memory view shared by foreground turns and private review runs."""
from ..core.records import Failure
from ..ports.memory import MemoryPort


class MemorySession:
    def __init__(self, port: MemoryPort | None, conversation, ledger, *, job_run_id=None, research=False):
        if port is None:
            raise Failure('memory_unavailable', 'The granted memory adapter is unavailable; no model call was sent.')
        self.port, self.ledger = port, ledger
        self.snapshot = (port.job_snapshot(job_run_id, ledger) if conversation is None and job_run_id is not None
                         else (port.research_snapshot if research else port.snapshot)(conversation.id, conversation.memory_bank_id, ledger))
        self.search_limited = False
        self.preparation_limited = False

    def check(self):
        self.port.check(self.snapshot, self.ledger)

    async def prepare(self, query):
        self.preparation_limited = False
        if self.snapshot.hybrid and not self.search_limited:
            try:
                with self.ledger.recall_search_work():
                    await self.port.prepare(self.snapshot,query,self.ledger)
            except Failure as error:
                if error.code == 'recall_query_limit':
                    self.preparation_limited = True
                    return
                if error.code != 'recall_search_limit':
                    raise
                self.search_limited = True

    async def automatic(self, query, maximum):
        if not self.snapshot.hybrid or maximum < 512:
            return None
        question = query
        query = self.port.automatic_query(self.snapshot,query,self.ledger)
        await self.prepare(query)
        result = self.operation('search_memory',dict(query=query,mode='current',cursor='',
            _automatic=True,_preference_question=question),maximum)
        return result if (result.get('results') or result.get('retrieval',{}).get('indexing_pending')
                          or result.get('reason') in {'recall_search_limit', 'recall_query_limit'}) else None

    def advance(self, result):
        before, after = self.snapshot, result.memory
        if (before.job_run_id != after.job_run_id or before.hybrid != after.hybrid or before.archive_epoch != after.archive_epoch
                or before.conversation_id != after.conversation_id or before.bank_id != after.bank_id
                or before.grant_epoch != after.grant_epoch or after.epoch < before.epoch
                or after.epoch != before.epoch and (result.outcome.get('status') != 'committed'
                    or result.outcome.get('bank_epoch') != after.epoch)):
            raise Failure('memory_source_changed', 'Invalid own-commit view transition; continuation stopped.')
        # Never fetch a newer epoch. A concurrent external change/revocation must
        # fail this exact returned version, even after our own save committed.
        self.port.check(after, self.ledger)
        self.snapshot = after

    def operation(self, name, args, maximum, *, budget=None):
        try:
            if name == 'search_memory':
                if self.preparation_limited:
                    self.preparation_limited = False
                    self.check()
                    return self._query_refusal()
                if self.search_limited:
                    self.check()  # A search refusal never bypasses revoked authority.
                    return self._search_refusal()
                with self.ledger.recall_search_work():
                    return self.port.search(self.snapshot, args, self.ledger, maximum, budget=budget)
            return self.port.read(self.snapshot, args, self.ledger, maximum)
        except Failure as error:
            if error.code == 'recall_query_limit':
                self.check()
                return self._query_refusal()
            if error.code == 'recall_search_limit':
                self.search_limited = True
                self.check()
                return self._search_refusal()
            # Invalid source/index/handle is a truthful tool result. Hard turn
            # limits, timeout and changed authority still stop the whole turn.
            if error.code in {'history_work_limit', 'timeout', 'memory_source_changed'}:
                raise
            if error.code == 'invalid_memory_reference' and name == 'search_memory':
                return {'status': 'not_delivered', 'reason': error.code,
                    'recovery': 'No search results were delivered. For the first page use cursor="" (an empty string). '
                                'For later pages copy next_cursor exactly from the previous result; never invent a cursor.'}
            return {'status': 'not_delivered', 'reason': error.code}

    @staticmethod
    def _query_refusal():
        return {'status': 'not_delivered', 'reason': 'recall_query_limit',
            'recovery': 'This lookup exceeded its database-work allowance. No partial results were delivered. '
                        'A narrower query or notebook inventory page can still be requested within this turn; '
                        'this is not evidence of absence.'}

    @staticmethod
    def _search_refusal():
        return {'status': 'not_delivered', 'reason': 'recall_search_limit',
            'recovery': 'No results from this search were delivered. Further memory searches are unavailable in this turn. '
                        'Answer using the evidence already delivered and state what remains unverified; this is not evidence of absence.'}
