"""Complete application-selected groups on the existing shared run ledger."""
import time

from ..core.care import messages, plan
from ..core.records import Failure
from .memory import MemorySession


class NotebookCare:
    def __init__(self, care, run_id, ledger, memory, notes, *, fresh, wall, clock=time.monotonic):
        self.care, self.run_id, self.ledger, self.notes = care, run_id, ledger, notes
        self.memory = MemorySession(memory, None, ledger, job_run_id=run_id)
        self.fresh, self.wall, self.clock = fresh, wall, clock

    async def run(self, complete):
        ledger = self.ledger
        self.care.sync(self.run_id, self.memory.snapshot, ledger)
        groups = []
        # A group that is too large still consumes one selection opportunity;
        # exceptionally large notes cannot monopolise an unlimited local loop.
        for _ in range(min(ledger.max_calls, ledger.max_operations)):
            self.fresh(); self.memory.check()
            if self.clock() >= ledger.review_work_deadline:
                break
            selected = self.care.select(self.run_id, self.memory.snapshot, ledger)
            if not selected:
                break
            sources = []
            source_limited = False
            for candidate in selected:
                try:
                    value = self.care.read(self.memory.snapshot, [candidate], ledger)[0]
                except Failure as error:
                    if error.code != 'care_source_size': raise
                    source_limited = True
                    break
                proposed = messages(sources+[value])
                if ledger.remaining_input(proposed) < 0 and not ledger.can_probe_context(proposed):
                    break
                sources.append(value)
            if not sources:
                descriptors = self.care.read(self.memory.snapshot, selected[:1], ledger, body=False)
                fresh_capacity = ledger.payload_limit-min(131072,ledger.payload_limit//4)
                if source_limited and descriptors[0]['total_bytes'] < fresh_capacity-4096:
                    # Earlier groups spent this slice's payload allowance. Keep
                    # this revision pending for a fresh bounded slice; it is not
                    # an intrinsically oversized or semantically reviewed note.
                    break
                ident = self.care.begin(self.run_id, self.memory.snapshot, descriptors, ledger, self.wall())
                self.care.record(ident, self.memory.snapshot, ledger, status='deferred', now=self.wall(),
                    outcome=dict(reason='complete_source_does_not_fit', checked=0))
                groups.append(ident)
                continue
            ident = self.care.begin(self.run_id, self.memory.snapshot, sources, ledger, self.wall())
            request = ledger.care_request(messages(sources), self.clock())
            reply = None
            try:
                reply = await complete(request, 'notebook_care')
                proposal = plan(reply, sources, revision_bytes=self.notes.policy.revision_bytes)
            except Failure as error:
                if error.code not in ('care_plan_invalid', 'provider_context_limit'):
                    raise
                reason = 'care_response_incomplete' if reply is not None and reply.finish_reason == 'length' else error.code
                self.care.retry(ident, self.memory.snapshot, ledger, reason=reason, now=self.wall())
                groups.append(ident)
                continue
            self.fresh(); self.memory.check()
            status = 'reviewed' if proposal['decision']=='unchanged' else 'deferred' if proposal['decision']=='defer' else 'proposed'
            if proposal['decision']=='change' and ledger.notes_write:
                scope = self.notes.scope_job(self.memory.snapshot, self.run_id, ledger)
                try:
                    _, advanced = self.care.apply(scope, ident, proposal, sources, ledger, self.wall())
                    self.memory.snapshot = advanced
                    self.memory.check()
                except Failure as error:
                    if error.code not in ('note_quota', 'care_provenance_limit'):
                        raise
                    self.care.record(ident, self.memory.snapshot, ledger, status='blocked', now=self.wall(),
                        outcome=dict(reason=error.code, checked=len(sources)))
            else:
                self.care.record(ident, self.memory.snapshot, ledger, status=status, now=self.wall(),
                    outcome=dict(reason='semantic_'+proposal['decision'], checked=len(sources)))
            groups.append(ident)
        return dict(origin='private_notebook_care_not_delivered', groups=groups,
                    calls=ledger.calls, operations=ledger.reads, **self.care.status(self.care.group(groups[-1])['job_id'])
                    ) if groups else dict(origin='private_notebook_care_not_delivered', groups=[],calls=0,operations=0)
