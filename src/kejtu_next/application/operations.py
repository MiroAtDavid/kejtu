"""One bounded native-tool continuation loop for foreground and private review.

Origins supply their own receipts and authority checks. No fabricated turn or
second note writer, allocator, semantic router or retry policy lives here.
"""
import asyncio
import json
from ..core.records import Failure, Message
from ..core.providers import provider
from ..core.context import RequestAccounting
from ..core.reference import arguments
from ..core.tools import HistoryArgumentFailure, history_arguments
from ..core.memory import MemoryArgumentFailure, memory_arguments
from ..core.notes import NoteArgumentFailure, note_arguments
from ..core.note_fields import PURPOSES, BASES, THREAD_STATES
from ..core.tasks import arguments as task_arguments
from ..core.research import arguments as research_arguments
from ..core.reminder_tools import arguments as reminder_arguments
from ..core.people import arguments as people_arguments, FIELDS as PEOPLE_FIELDS, WRITES as PEOPLE_WRITES

from ..core.web import arguments as web_arguments
from ..core.mcp import arguments as mcp_arguments
from ..core.skills import arguments as skill_arguments, TOOLS as SKILL_TOOLS
from ..core.workspace import arguments as workspace_arguments


PEOPLE_READS = set(PEOPLE_FIELDS) - set(PEOPLE_WRITES)


class OperationLoop:
    history_tools = False
    reference_id = None
    checkpoint = None
    research = None
    reminders = None
    people = None
    web = None
    mcp = None
    skills = None
    workspace = None
    progress = None
    before_operation = None
    clock_context = None

    def check_operations(self):
        """Origin-specific live authority in addition to each storage transaction."""

    def compression_ranges(self, messages):
        return []

    def stopping_reason(self, result):
        return None

    def closing_messages(self, messages, reason):
        return messages

    def closing_reserve(self):
        return ()

    async def continue_with(self, request, reply, call_model):
        ledger = self.ledger
        clock = asyncio.get_running_loop().time
        while reply.tool_calls or reply.finish_reason == 'tool_calls':
            if not request.allow_tool_calls:
                raise Failure('tool_limit', 'The final phase requested another operation; nothing ran.')
            if not reply.tool_calls or reply.finish_reason != 'tool_calls':
                raise Failure('invalid_tool_call', 'A complete native batch was required; nothing ran.')
            oversized = len(reply.tool_calls) > ledger.max_operations - ledger.reads
            if oversized and not (ledger.notebook_only or ledger.web_only):
                raise Failure('tool_limit', 'Native batch exceeds the remaining operation allowance; no operation in this batch ran.')
            # Preflight the entire batch before any operation. Do not silently
            # execute a valid prefix when a later request is malformed/ungranted.
            identities, parsed, preflight_errors = set(), [], {}
            for position, call in enumerate(reply.tool_calls, 1):
                if call.id in identities or (provider(ledger.profile.provider).unique_tool_ids and call.id in self.call_ids):
                    raise Failure('invalid_tool_call', 'Duplicate provider call identity; no operation in this batch ran.')
                identities.add(call.id)
                if self.workspace and call.name in ('university','media'):
                    try:
                        args=workspace_arguments(call)
                        self.workspace.preflight(call.name,args)
                    except Failure as error:
                        preflight_errors[position-1]=dict(status='not_executed',reason=error.code,recovery=error.detail)
                        args=None
                elif self.skills and call.name in SKILL_TOOLS:
                    try:
                        args = skill_arguments(call)
                        self.skills.preflight(call.name,args)
                    except Failure as error:
                        preflight_errors[position-1] = dict(status='not_executed',reason=error.code,recovery=error.detail)
                        args = None
                elif self.mcp and call.name in ('mcp_tools','mcp_call','mcp_read'):
                    try:
                        args = mcp_arguments(call)
                        self.mcp.preflight(call.name, args)
                    except Failure as error:
                        preflight_errors[position-1] = dict(status='not_executed', reason=error.code, recovery=error.detail)
                        args = None
                elif self.web and call.name in ('web_search','web_read'):
                    try:
                        args = web_arguments(call, self.web.provider.name)
                    except Failure as error:
                        preflight_errors[position-1] = dict(status='not_executed',reason=error.code,recovery=error.detail+' No call in this batch ran.')
                        args = None
                elif call.name == 'read_reference' and self.reference_id:
                    args = arguments(call)
                elif self.history_tools and call.name in ('search_history', 'read_history'):
                    try:
                        args = history_arguments(call)
                    except HistoryArgumentFailure as error:
                        try:
                            self.argument_failure('history', ledger.calls,
                                position, call, error.validation_reason)
                        except Exception:
                            pass  # Private diagnostic IO cannot authorize or block recovery.
                        preflight_errors[position - 1] = {
                            'status': 'not_executed', 'reason': error.code,
                            'recovery': 'search_history requires query (at most 256 UTF-8 bytes) and cursor (empty string for first page). '
                                'read_history requires the returned 32-hex artifact_id, integer offset >= 0 and max_bytes >= 1. '
                                'No extra fields. No call in this batch ran.'}
                        args = None
                elif self.people and call.name == 'people':
                    try:
                        args = people_arguments(call)
                    except Failure as error:
                        preflight_errors[position-1] = dict(status='not_executed',reason=error.code,recovery=error.detail)
                        args = None
                elif self.reminders and call.name == 'reminders':
                    try:
                        args = reminder_arguments(call)
                    except Failure as error:
                        preflight_errors[position-1] = dict(status='not_executed', reason=error.code, recovery=error.detail)
                        args = None
                elif self.research and call.name == 'research_tasks':
                    try:
                        args = research_arguments(call)
                    except Failure as error:
                        preflight_errors[position-1] = dict(status='not_executed',reason=error.code,recovery=error.detail)
                        args = None
                    if args and args['action'] in ('save','pause','resume','disposition') and not request.notes_write and not ledger.web_only:
                        raise Failure('research_forbidden','Task writing is not granted; no operation in this batch ran.')
                elif self.tasks and call.name == 'personal_tasks':
                    try:
                        args = task_arguments(call)
                    except Failure as error:
                        preflight_errors[position-1] = dict(status='not_executed',reason=error.code,
                            recovery=error.detail + ' No call in this batch ran.')
                        args = None
                        parsed.append(args)
                        continue
                    if args['action'] in ('save','transition') and not request.notes_write and not ledger.web_only:
                        raise Failure('task_forbidden', 'Task writing is not granted; no operation in this batch ran.')
                elif self.memory and call.name in ('search_memory', 'read_memory'):
                    try:
                        args = memory_arguments(call, defaults=bool(self.notes and request.notes_write),hybrid=request.hybrid_memory)
                    except MemoryArgumentFailure as error:
                        # Read-only callers need the same bounded correction
                        # path as note authors. Validation/grants stay strict;
                        # the shared preflight rejects every call in this batch.
                        try:
                            self.argument_failure('memory', ledger.calls,
                                position, call, error.validation_reason)
                        except Exception:
                            # Evidence is private/best-effort, never an execution
                            # gate or an excuse to run any of the rejected batch.
                            pass
                        guidance = {
                            'collection_scope': 'collection is all/current/historical and applies only to scope:notebook or memories; omit collection for other scopes.',
                            'role_scope': 'role is user/assistant and requires scope:conversations; omit role for other scopes.',
                            'read_ref': 'read_memory requires the exact read_ref returned by search_memory; copy it unchanged, not a record ID or an invented reference.',
                        }.get(error.validation_reason)
                        if guidance is None:
                            guidance = ('search_memory requires query; read_memory requires the returned read_ref. Paging fields are optional.'
                                if self.notes and request.notes_write else
                                'search_memory requires query, mode (current/historical), and cursor (empty string for first page). '
                                'read_memory requires the returned read_ref, integer offset >= 0 and max_bytes >= 1. Use only the supplied fields.')
                        preflight_errors[position - 1] = {
                            'status': 'not_executed', 'reason': error.code,
                            'recovery': guidance + ' No call in this batch ran.'}
                        args = None
                elif self.notes and (call.name == 'note_outcome' or call.name == 'write_note' and request.notes_write):
                    try:
                        args = note_arguments(call)
                    except NoteArgumentFailure as error:
                        try:
                            self.argument_failure('note', ledger.calls,
                                position, call, error.validation_reason)
                        except Exception:
                            # Private evidence is best-effort. It must not decide
                            # whether a refusal is recoverable or permit a write.
                            pass
                        metadata_values = {'purpose': PURPOSES, 'basis': BASES, 'thread_status': THREAD_STATES}
                        if error.validation_reason in metadata_values:
                            preflight_errors[position - 1] = {
                                'status': 'not_executed', 'reason': error.code,
                                'invalid_fields': [error.validation_reason],
                                'allowed_values': list(metadata_values[error.validation_reason]),
                                'recovery': 'Use an allowed value for this note field, or omit it if optional. '
                                    'No call in this batch ran. You may correct the call or finish your answer without saving.'}
                            args = None
                            parsed.append(args)
                            continue
                        if error.validation_reason.startswith('tags') or error.validation_reason=='flags':
                            preflight_errors[position - 1] = {
                                'status':'not_executed','reason':'invalid_tool_arguments',
                                'invalid_fields':[error.validation_reason],
                                'recovery':'Topics:0–5, no calendar dates or typed state; reuse suggestions or choose a useful new label. '
                                    'flags:coverage-partial/user-rejected. No call in this batch ran.'}
                            args=None
                            parsed.append(args)
                            continue
                        if error.validation_reason in {'save_fields', 'read_ref','metadata_fields'}:
                            preflight_errors[position - 1] = {
                                'status': 'not_executed', 'reason': 'invalid_tool_arguments',
                                'recovery': 'Save/body edit: text; edit needs a complete current read_ref. Metadata only: read_ref + fields, no text. '
                                    'No operation key, revision, quote or edit mode is required. No call in this batch ran.'}
                            args = None
                            parsed.append(args)
                            continue
                        if not error.missing_identifiers and error.validation_reason not in {'note_id', 'expected_revision'}:
                            raise
                        field_kind = 'missing_fields' if error.missing_identifiers else 'invalid_fields'
                        preflight_errors[position - 1] = {
                            'status': 'not_executed', 'reason': 'invalid_tool_arguments',
                            field_kind: list(error.missing_identifiers) if error.missing_identifiers else [error.validation_reason],
                            # Fixed schema names only: no supplied value, target
                            # existence, private diagnostic, or inferred identity.
                            'recovery': 'search_memory(current) -> read_memory(read_ref). '
                                'Use full 32-hex record_id as note_id, revision_id as expected_revision; '
                                'read exact tail for append newlines. No call in this batch ran.'}
                        args = None
                else:
                    raise Failure('unsupported_tool', 'No such capability was granted; no operation in this batch ran.')
                if args is not None and ledger.web_only:
                    allowed_actions = {
                        'personal_tasks': {'list','read'},
                        'research_tasks': {'list','read','findings','runs','result'},
                        'people': PEOPLE_READS,
                        'reminders': {'list','read','pending','outcome'},
                    }
                    if call.name in allowed_actions and args.get('action') not in allowed_actions[call.name]:
                        preflight_errors[position-1] = dict(status='not_executed', reason='background_read_only',
                            recovery='This task may read internal context but cannot change it. No operation in this batch ran.')
                        args = None
                parsed.append(args)
            self.call_ids.update(identities)
            messages = request.messages + (Message('assistant', reply.text,
                tool_calls=reply.tool_calls, reasoning=reply.reasoning),)
            def tail(call, result):
                return Message('tool', json.dumps(result, ensure_ascii=False, separators=(',', ':')),
                               tool_call_id=call.id, tool_name=call.name)
            refused = {'status': 'not_delivered', 'reason': 'result_context_limit', 'snapshot': self.origin_id}
            # Sizing placeholders are never sent or recorded as executed results.
            # Keep space for EVERY remaining pairing, using the larger refusal.
            # A recoverable preflight refusal rejects the WHOLE batch. Pair all
            # original calls without executing even a valid prefix or lookup.
            rejected = tuple(preflight_errors.get(i, {'status': 'not_executed',
                'reason': 'batch_preflight_failed'}) for i in range(len(reply.tool_calls)))
            pending = tuple(tail(c, rejected[i] if preflight_errors else
                dict(refused, reason='insufficient_result_capacity')) for i, c in enumerate(reply.tool_calls))
            if oversized:
                # Pair the entire refused batch without running/charging a
                # prefix. Only the existing final model call is available.
                messages += tuple(tail(c, dict(status='not_executed', reason='tool_limit',
                    recovery='No operation in this batch ran. Finish the private report from earlier results.'))
                    for c in reply.tool_calls)
                messages = self.closing_messages(messages, 'tool_limit')
                self.check_operations()
                request = ledger.next_history_request(messages, clock(), final=True)
                reply = await call_model(self.origin_id, ledger, request)
                if ledger.remaining_input(request.messages) < 0:
                    raise Failure('context_measurement_unavailable', 'The private closing report lacks usable input accounting.')
                continue
            remeasure = False
            checkpointed = False
            finalizing = False
            if ledger.remaining_input(messages + pending + self.closing_reserve()) < 128:
                eligible = self.compression_ranges(request.messages)
                if (not self.checkpoint or self.checkpoint.used or not eligible
                        or ledger.max_calls - ledger.calls < 2):
                    if preflight_errors:
                        # Invalid batches retain their preflight boundary. The
                        # terminal escape is for valid work that ran out of
                        # room, not another malformed-argument recovery attempt.
                        raise Failure('result_context_limit', 'Native batch and its result envelopes do not fit; no operation in this batch ran.')
                    # Do not lose gathered evidence just because another native
                    # batch cannot fit. Pair ALL proposed calls as not executed,
                    # retain the exact thinking chain, and spend only the
                    # existing final call. This path can never authorize effects.
                    finalizing = remeasure = True
                    rejected = tuple(preflight_errors.get(i, dict(status='not_executed',
                        reason='result_context_limit', recovery='No call in this batch ran. '
                        'Search work has ended because context is tight. Answer now from delivered evidence; '
                        'state any gaps and do not claim these operations completed.'))
                        for i in range(len(reply.tool_calls)))
                    pending = tuple(tail(c, rejected[i]) for i,c in enumerate(reply.tool_calls))
                    if not ledger.can_finish_history(messages + pending):
                        raise Failure('result_context_limit', 'Native batch and its result envelopes do not fit; no operation in this batch ran.')
                else:
                    removed = {i for start, end in eligible for i in range(start,end)}
                    required = tuple(m for i,m in enumerate(messages) if i not in removed) + pending
                    fresh = RequestAccounting(ledger.profile, ledger.accounting.schema, counter=ledger.accounting.counter)
                    if (not ledger.accounting.provider_first and fresh.measure(required) + ledger.profile.generation_limit
                            + ledger.profile.safety_margin + 64 > ledger.profile.context_limit):
                        raise Failure('result_context_limit', 'Protected pending batch alone cannot fit; no summary or operation ran.')
                    compact = await self.checkpoint.compress(request.messages,
                        eligible, phase='mid_turn')
                    if self.workspace:
                        self.workspace.clear_reads()
                    if self.tasks:
                        self.tasks.reads.clear()
                    if self.research:
                        self.research.reads.clear()
                    if self.people:
                        self.people.reads.clear()
                    if self.reminders:
                        self.reminders.clear_reads()
                    # The model already authored the pending batch. No operation
                    # from it has run; keep its complete native call state exact.
                    messages = compact + (messages[-1],)
                    if ledger.remaining_input(messages + pending) < 128:
                        # Rewriting the prefix invalidates its usage anchor. Do not
                        # pretend its byte bound is a measured overflow, or execute
                        # a write against unmeasured receipt capacity. Pair every
                        # untouched native call truthfully, then let the existing
                        # capability-gated checkpoint request establish fresh usage.
                        remeasure = True
                        rejected = tuple(preflight_errors.get(i, dict(status='not_executed',
                            reason='context_checkpoint', recovery='No call in this batch ran. '
                            'Context was compressed; retry the needed operation after this feedback. '
                            'An edit still requires a complete current read; a summary is not a read.'))
                            for i in range(len(reply.tool_calls)))
                        pending = tuple(tail(c, rejected[i]) for i,c in enumerate(reply.tool_calls))
                        if not ledger.can_probe_context(messages + pending):
                            raise Failure('result_context_limit', 'Compressed context cannot fit the pending batch; no operation in it ran.')
                    ledger.resume_context(compact, clock())
                    checkpointed = True
                    self.context_ranges = []
                    self.initial_length = len(compact)
            ordinals, admitted_reads, task_reads, reminder_reads, people_reads, research_reads, workspace_reads = [], [], [], [], [], [], []
            capacity_refused = False
            end_work_reason = None
            for index, (call, args) in enumerate(zip(reply.tool_calls, parsed)):
                await asyncio.sleep(0)  # Allow cancellation between sequential IO operations.
                if self.before_operation:
                    await self.before_operation()
                self.check_operations()
                if self.mcp:
                    self.mcp.check()
                if self.memory:
                    self.memory.check()
                if self.tasks:
                    self.tasks.check()
                if self.research:
                    self.research.check()
                if self.reminders:
                    self.reminders.check()
                if self.people:
                    self.people.check()
                if (preflight_errors or remeasure) and self.notes:
                    self.notes.check()
                sizing = messages + pending[index + 1:] + self.closing_reserve()
                if self.skills:self.skills.check()
                if self.workspace:self.workspace.check()
                workspace_tool=call.name in ('university','media')
                skilled = call.name in SKILL_TOOLS
                connected = call.name in ('mcp_tools','mcp_call','mcp_read')
                searching = call.name in ('search_history', 'search_memory', 'web_search') or connected or skilled
                task = call.name == 'personal_tasks'
                research = call.name == 'research_tasks'
                reminder = call.name == 'reminders'
                people = call.name == 'people'
                note = call.name in ('write_note', 'note_outcome') or task or reminder or people or research or workspace_tool
                if preflight_errors or remeasure or end_work_reason:
                    budget, allowance = None, 0  # Whole feedback fitted or capability-gated; no result body to read.
                else:
                    shares = (sum(c.name == 'web_search' for c in reply.tool_calls[index:])
                              if call.name == 'web_search' else 1)
                    budget = ledger.search_budget(sizing, tail(call, refused), shares=shares) if searching or note else None
                    allowance = (budget.maximum_bytes if searching or note
                                 else ledger.read_allowance(sizing, tail(call, refused)))
                ledger.reserve_history_operation(clock())
                if preflight_errors or remeasure or end_work_reason:
                    self.start_operation(ledger.reads, call, preflight_rejected=True)
                else:
                    self.start_operation(ledger.reads, call)
                ordinals.append(ledger.reads)
                if end_work_reason:
                    result = dict(status='not_executed', reason=end_work_reason,
                        recovery='Earlier work reached the review limit. This operation did not run. Finish the private report.')
                elif preflight_errors or remeasure:
                    # The full paired feedback was admitted above. It consumes
                    # ordinary operation/call capacity, not a separate retry loop.
                    result = rejected[index]
                elif allowance < (256 if searching else 1):
                    result = dict(refused, reason='insufficient_result_capacity')
                else:
                    try:
                        with ledger.review_work():
                            if workspace_tool:
                                result=await self.workspace.operation(call.name,args,allowance,budget)
                            elif skilled:
                                if self.progress:self.progress.activity(self.origin_id,'reading')
                                result = self.skills.operation(call.name,args,allowance,budget)
                            elif connected:
                                if self.progress:
                                    self.progress.activity(self.origin_id,'connecting')
                                result = await self.mcp.operation(call.name,args,allowance,budget)
                            elif self.web and call.name in ('web_search','web_read'):
                                if self.progress:
                                    self.progress.activity(self.origin_id,'searching' if call.name=='web_search' else 'reading')
                                result = await self.web.operation(call.name,args,allowance,budget)
                            elif people:
                                result = self.people.operation(args, allowance, budget)
                            elif research:
                                result = self.research.operation(args, allowance, budget)
                            elif task:
                                result = self.tasks.operation(args, allowance, budget)
                            elif reminder:
                                result = self.reminders.operation(args, allowance, budget)
                            elif note:
                                result = self.notes.operation(call, args, allowance, budget)
                            elif call.name in ('search_memory', 'read_memory'):
                                if call.name == 'search_memory':
                                    if args.get('scope') not in ('tags','tag_cleanup'):
                                        await self.memory.prepare(args['query'])
                                result = self.memory.operation(call.name, args, allowance, budget=budget)
                            else:
                                try:
                                    result = self.source_operation(call.name, args, ledger,
                                        allowance, budget=budget)
                                except Failure as error:
                                    if call.name == 'read_history' and error.code == 'history_source_unavailable':
                                        recovery = 'Use an artifact_id from search_history in this conversation.'
                                        if self.memory:
                                            recovery += (' For other conversations or notes, use search_memory '
                                                'then read_memory with its returned read_ref.')
                                    elif call.name == 'search_history' and error.code == 'invalid_history_cursor':
                                        recovery = ('Start with cursor:"". To continue, copy next_cursor exactly '
                                            'from search_history for the same query in this turn.')
                                    else:
                                        raise
                                    # These denied reads need no notebook capability. Keep
                                    # source/cursor validation strict, reveal no membership,
                                    # and never reroute or retry the call. Feedback consumes
                                    # the same operation and context budgets as any result.
                                    result = {'status': 'not_delivered', 'reason': error.code,
                                        'recovery': recovery}
                    except Failure as error:
                        if not (ledger.notebook_only or ledger.web_only) or error.code not in (
                                'review_sql_limit', 'review_payload_limit', 'review_time_limit'):
                            raise
                        result = dict(status='not_delivered', reason=error.code,
                            recovery='Tool work ended at the review limit. Finish from earlier results and state what remains unchecked.')
                if remeasure or end_work_reason:
                    pass  # Complete no-effect feedback is admitted by the checkpoint gate above.
                elif preflight_errors:
                    # Do not replace precise not-executed status with a source
                    # read refusal or imply a durable note outcome was created.
                    if ledger.remaining_input(sizing + (tail(call, result),)) < 128:
                        raise Failure('result_context_limit', 'Preflight feedback does not fit; no operation ran.')
                elif note:
                    # Storage pre-reserved the full receipt; never replace a
                    # committed effect with a generic non-delivery result.
                    if not budget.fits(result):
                        if result.get('status') in ('committed', 'unchanged', 'unknown') or workspace_tool and result.get('status') in ('saved','observed'):
                            raise Failure('note_result_capacity', 'Durable note outcome could not be delivered; inspect it with show.')
                        result = refused
                elif searching:
                    result = result if budget.fits(result) else refused
                else:
                    fit = ledger.fit_provenance_result if result.get('kind') == 'provenance' else ledger.fit_read_result
                    fitted = fit(sizing, tail(call, {}), result)
                    if fitted is None and 'recovery' in result:
                        # Only optional application guidance may be omitted;
                        # retain the precise source refusal if it fits.
                        fitted = fit(sizing, tail(call, {}), {k: v for k, v in result.items() if k != 'recovery'})
                    result = fitted or refused
                if not remeasure and ledger.remaining_input(sizing + (tail(call, result),)) < 128:
                    if note:
                        raise Failure('note_result_capacity', 'Note outcome delivery could not fit; inspect durable outcomes.')
                    result = refused
                self.prepare_operation(ledger.reads, result)
                end_work_reason = end_work_reason or self.stopping_reason(result)
                capacity_refused |= result.get('status') in ('not_delivered', 'rejected') and result.get('reason') in (
                    'result_context_limit', 'insufficient_result_capacity', 'result_capacity',
                    'task_result_capacity', 'note_result_capacity', 'reminder_result_capacity', 'people_result_capacity')
                if workspace_tool and not (preflight_errors or remeasure):
                    workspace_reads.append((call.name,args,result))
                if people and not (preflight_errors or remeasure):
                    people_reads.append((args, result))
                if reminder and not (preflight_errors or remeasure):
                    reminder_reads.append((args, result))
                if task and not (preflight_errors or remeasure):
                    task_reads.append((args, result))
                if research and not (preflight_errors or remeasure):
                    research_reads.append((args, result))
                if self.notes and not (preflight_errors or remeasure) and call.name == 'read_memory':
                    # A later call in THIS batch was authored before these read
                    # results existed. It must not gain whole-note overwrite
                    # authority from a read the model has not yet received.
                    admitted_reads.append((args, result))
                output = tail(call, result)
                if self.clock_context and index == len(reply.tool_calls) - 1:
                    # One fresh reading after the batch, beside its final result.
                    # Earlier prefix bytes and stored operation/source data stay exact.
                    output = self.clock_context.update(output, result, ledger, sizing, headroom=128)
                messages += (output,)
            # Submit only a complete batch. Earlier prepared receipts remain
            # truthful if a later read, budget check or authority check fails.
            self.check_operations()
            if (capacity_refused and not checkpointed and self.checkpoint
                    and not self.checkpoint.used and ledger.max_calls - ledger.calls >= 2):
                eligible = self.compression_ranges(request.messages)
                if eligible:
                    # A capacity refusal is already pressure: do not wait for
                    # repeated smaller-page requests to consume the final calls.
                    # Retain this entire completed batch exactly. Only approved
                    # older spans change; no IO or committed effect is replayed.
                    compact = await self.checkpoint.compress(request.messages, eligible, phase='mid_turn', optional=True)
                    changed = compact != request.messages
                    if changed and self.workspace:
                        self.workspace.clear_reads()
                    if changed and self.tasks:
                        self.tasks.reads.clear()
                    if changed and self.research:
                        self.research.reads.clear()
                    if changed and self.people:
                        self.people.reads.clear()
                    if changed and self.reminders:
                        self.reminders.clear_reads()
                    messages = compact + messages[len(request.messages):]
                    ledger.resume_context(compact, clock())
                    checkpointed = changed
                    if changed:
                        self.context_ranges = []
                        self.initial_length = len(compact)
            if ledger.notebook_only or ledger.web_only:
                reason = (end_work_reason or ('result_context_limit' if finalizing else
                    'review_time_limit' if clock() >= ledger.review_work_deadline else
                    'call_limit' if ledger.calls + 1 >= ledger.max_calls else
                    'tool_limit' if ledger.reads >= ledger.max_operations else None))
                if reason:
                    messages = self.closing_messages(messages, reason)
                    finalizing = True
            request = ledger.next_history_request(messages, clock(), probe=checkpointed, final=finalizing)
            if self.memory:
                self.memory.check()
            if preflight_errors and self.notes:
                self.notes.check()
            for ordinal in ordinals:
                self.submit_operation(ordinal)
            for name, args, result in workspace_reads:
                self.workspace.observe(name,args,result)
            for args, result in admitted_reads:
                # Only the final fitted exact pages in the next stable request
                # authorize a subsequent model-authored save. No source reread.
                self.notes.observe(args, result)
            for args, result in research_reads:
                self.research.observe(args, result)
            for args, result in task_reads:
                self.tasks.observe(args, result)
            for args, result in people_reads:
                self.people.observe(args, result)
            for args, result in reminder_reads:
                self.reminders.observe(args, result)
            reply = await call_model(self.origin_id, ledger, request)
            if (checkpointed or finalizing) and ledger.remaining_input(request.messages) < 0:
                # A provider-first send is permission to measure, not evidence
                # of fit. Missing usage must not authorize another effect or a
                # successful final answer when the conservative bound still fails.
                raise Failure('context_measurement_unavailable',
                    'Context continuation returned no usable input count and could not be conservatively fitted. '
                    'Inspect retained operation receipts; nothing was replayed.')
        return reply
