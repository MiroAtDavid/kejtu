"""Independent private notebook procedure through the shared native tool loop."""
import hashlib
import json
import re

from ..core.notes import intent_digest, note_arguments, operation_key
from ..core.records import Failure, Message
from ..core.tools import result_receipt
from .memory import MemorySession
from .notes import NoteSession
from .operations import OperationLoop


CLOSING = Message('system',
    'The private review has reached its work allowance. '
    'No further tools are available. Write the final private report now using only results already delivered. '
    'State which notes were actually read, findings, confirmed changes (if any), and what remains unchecked. '
    'If no complete note was read, say that no substantive review was possible. '
    'This is a partial review, not proof that the notebook is clean. '
    'Do not print tool calls or claim refused operations ran.')


def observed(reply):
    """Provider-attempt identity, not extra durable copies of authored note text.

    Exact committed note bodies belong to note revisions; admitted read bodies
    belong to their source records. The final private account is stored once as
    the run result. Failed tool arguments/thoughts are not another notebook.
    """
    digest = lambda text: hashlib.sha256(text.encode()).hexdigest()
    return dict(finish_reason=reply.finish_reason, input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens, text_sha256=digest(reply.text),
        reasoning_sha256=digest(reply.reasoning), tool_calls=[
            dict(id=c.id, name=c.name, arguments_sha256=digest(c.arguments)) for c in reply.tool_calls])


class IdleReview(OperationLoop):
    def __init__(self, jobs, ident, conversation, ledger, *, memory, notes, event, fresh):
        self.jobs, self.origin_id, self.ledger = jobs, ident, ledger
        self.kind = jobs.check_run(ident).kind
        self.memory = MemorySession(memory, conversation, ledger, job_run_id=ident)
        self.notes = NoteSession(notes, self.memory, None, ledger, job_run_id=ident,
            existing_only=self.kind == 'notebook_maintenance')
        self.tasks = None
        self.call_ids = set()
        self.event, self.fresh = event, fresh
        self.limit_reason = None

    def stopping_reason(self, result):
        if result.get('status') not in ('not_delivered', 'not_executed', 'rejected'):
            return None  # An ordinary bounded result page still permits exact reads.
        reason = result.get('reason')
        return reason if reason in {'recall_search_limit', 'review_sql_limit',
            'review_payload_limit', 'review_time_limit', 'result_context_limit',
            'insufficient_result_capacity', 'result_capacity', 'note_result_capacity'} else None

    def closing_messages(self, messages, reason):
        self.limit_reason = reason
        return messages + self.closing_reserve()

    def closing_reserve(self):
        return (CLOSING,)

    def check_operations(self):
        self.fresh()
        self.memory.check()

    def argument_failure(self, kind, call_number, position, call, reason):
        self.event('argument_failure', dict(ordinal=call_number, position=position, kind=kind,
            reason=reason, arguments_sha256=hashlib.sha256(call.arguments.encode()).hexdigest()))

    def start_operation(self, ordinal, call, *, preflight_rejected=False):
        if preflight_rejected:
            arguments = dict(preflight_rejected=True,
                             arguments_sha256=hashlib.sha256(call.arguments.encode()).hexdigest())
        elif call.name == 'write_note':
            args = note_arguments(call)
            arguments = dict(operation_key=operation_key(None, args, job_run_id=self.origin_id),
                             intent_sha256=intent_digest(args))
        else:
            arguments = json.loads(call.arguments)
        self.event('operation_started', dict(ordinal=ordinal, call_number=self.ledger.calls,
            call_id=call.id, name=call.name, arguments=arguments))

    def prepare_operation(self, ordinal, result):
        self.event('operation_prepared', dict(ordinal=ordinal, result=result_receipt(result)))

    def submit_operation(self, ordinal):
        self.event('operation_submitted', dict(ordinal=ordinal))

    async def run(self, request, complete):
        self.check_operations()
        reply = await complete(request, self.kind)
        async def call_model(origin, ledger, request):
            if origin != self.origin_id or ledger is not self.ledger:
                raise Failure('job_origin', 'A private review cannot borrow another run or ledger.')
            self.check_operations()
            return await complete(request, self.kind)
        reply = await self.continue_with(request, reply, call_model)
        self.check_operations()
        if reply.finish_reason != 'stop' or reply.tool_calls or not reply.text.strip() or len(reply.text.encode()) > 32768:
            raise Failure('job_review', 'Private review did not produce a complete bounded closing account; inspect durable note outcomes.')
        # Some providers leak native control markup into content after tools are
        # disabled. A transport-level stop is not a usable closing account. Do
        # not interpret or execute these strings, retry, or undo earlier writes.
        # Keep the historical-result display in sync via review-output.json.
        if re.search(r'</?[|｜]+DSML[|｜]+', reply.text):
            raise Failure('job_review_tool_markup',
                'The model returned tool instructions instead of a review report. '
                'Those printed instructions did not run; inspect earlier note outcomes separately.')
        # Storage pages stay bounded; a larger run must retain every receipt,
        # not fail after its writes or silently show only the first fifty.
        outcomes, cursor = [], ''
        while True:
            receipts = self.notes.port.inspect_run(self.memory.snapshot, self.origin_id, self.ledger,
                limit=min(50, max(1, self.ledger.max_operations - len(outcomes))), after=cursor)
            outcomes.extend(receipts['outcomes'])
            cursor = receipts['next_cursor']
            if cursor is None:
                break
            if len(outcomes) >= self.ledger.max_operations:
                raise Failure('job_outcomes', 'Run outcomes exceed the admitted operation allowance.')
        return dict(origin='private_notebook_maintenance_not_delivered' if self.kind == 'notebook_maintenance'
                    else 'private_idle_review_not_delivered', text=reply.text,
            calls=self.ledger.calls, operations=self.ledger.reads, note_outcomes=outcomes,
            completion='limited' if self.limit_reason else 'complete', limit_reason=self.limit_reason)
