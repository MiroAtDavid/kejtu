"""Shared turn ledger. Estimates are explicit, not calibrated tokenizer guarantees."""
from dataclasses import dataclass, field, replace
from contextlib import contextmanager
import json
import math
import time
from typing import Callable
from .providers import provider

from .records import Failure, Message, Profile, Reply, Request, message_record
from .reference import tool_schema
from .tools import schemas
from .execution import ExecutionLimits, HistoryPolicy, TurnLimits, select_history_policy
from .context import RequestAccounting


def message_cost(message: Message) -> int:
    # A conservative development estimate. A future tokenizer owner must calibrate
    # framing/model templates; do not describe this byte heuristic as measured use.
    if message.tool_calls or message.tool_call_id or message.tool_name or message.reasoning is not None:
        return len(json.dumps(message_record(message), ensure_ascii=True).encode()) + 256
    return len(message.content.encode('utf-8')) + 32


@dataclass(frozen=True)
class SearchBudget:
    """One ledger-issued fit value for an unsubmitted structured search result.

    maximum_bytes bounds candidate construction, not a promise of admission.
    Storage retains its own whole-hit paging and cursor authority; only this
    shared cost owner measures the actual escaped tool envelope. The captured
    room already excludes the variation reserve. No mutable ledger or IO here.
    """
    maximum_bytes: int
    template: Message
    fits_request: Callable[[Message], bool] | None = None

    def fits(self, result: dict) -> bool:
        text = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
        if self.fits_request:
            return self.fits_request(replace(self.template, content=text))
        return message_cost(replace(self.template, content=text)) <= self.maximum_bytes


@dataclass
class Ledger:
    profile: Profile
    started: float
    calls: int = 0
    input_estimate: int = 0
    with_reference: bool = False
    reads: int = 0
    generation_debit: int = 0
    with_history: bool = False
    sql_steps: int = 0
    payload_bytes: int = 0
    history_policy: HistoryPolicy | None = None
    with_memory: bool = False
    with_notes: bool = False
    notes_write: bool = False
    hybrid_memory: bool = False
    with_tasks: bool = False
    accounting: RequestAccounting | None = None
    execution_limits: ExecutionLimits | None = None
    turn_limits: TurnLimits | None = None
    notebook_only: bool = False
    web_only: bool = False
    with_reminders: bool = False
    with_people: bool = False
    people_write: bool = False
    with_web: bool = False
    web_provider: str = "searxng"
    with_mcp: bool = False
    with_skills: bool = False
    with_workspace: bool = False
    _prefix: tuple[Message, ...] = field(default=(), init=False)
    _settled: int = field(default=0, init=False)
    _operation_checkpoint: int = field(default=0, init=False)
    _summary_pending: bool = field(default=False, init=False)
    _context_probe_pending: bool = field(default=False, init=False)
    _recall_search_depth: int = field(default=0, init=False)
    _recall_sql_start: int = field(default=0, init=False)
    _recall_index_size: int | None = field(default=None, init=False)
    _review_work_depth: int = field(default=0, init=False)
    _paused_seconds: float = field(default=0, init=False)
    _research_interrupted: bool = field(default=False, init=False)
    _research_yields: int = field(default=0, init=False)
    _yielded_ordinal: int = field(default=0, init=False)
    _interrupted_seconds: float = field(default=0, init=False)

    def __post_init__(self):
        if self.turn_limits is not None and (type(self.turn_limits) is not TurnLimits or self.execution_limits is not None):
            raise ValueError('Foreground and background limits have separate admission owners.')
        if type(self.notebook_only) is not bool or self.notebook_only and (
                self.execution_limits is None or not self.with_memory or self.with_reference or self.with_tasks or self.with_reminders or self.with_people or self.with_web or self.with_mcp or self.with_skills):
            raise ValueError('Private-review tools require an explicit bounded memory procedure.')
        if type(self.web_only) is not bool or self.web_only and (self.execution_limits is None or self.notebook_only or self.with_reference or self.with_notes or self.notes_write or self.people_write):
            raise ValueError('Background assignments need explicit limits and read-only internal context.')
        if self.execution_limits is not None and type(self.execution_limits) is not ExecutionLimits:
            raise ValueError('Use validated procedure limits with the shared Ledger.')
        if type(self.with_tasks) is not bool or self.with_tasks and not self.with_memory:
            raise ValueError('tasks require the memory grant')
        if type(self.with_people) is not bool or type(self.people_write) is not bool or self.with_people and not self.with_memory or self.people_write and not self.with_people:
            raise ValueError('people require explicit memory/read/write grants')
        if type(self.with_reminders) is not bool or self.with_reminders and not self.with_memory:
            raise ValueError('reminders require the memory grant')
        if type(self.hybrid_memory) is not bool or self.hybrid_memory and not self.with_memory:
            raise ValueError('hybrid recall requires the memory grant')
        if type(self.with_memory) is not bool or self.with_memory and not self.with_history:
            raise ValueError('memory requires the shared history execution grant')
        if type(self.with_web) is not bool or self.with_web and not self.with_history:
            raise ValueError('Web requires the shared execution grant')
        if type(self.with_skills) is not bool or self.with_skills and not self.with_history:
            raise ValueError('Skills require the shared execution grant')
        if type(self.with_mcp) is not bool or self.with_mcp and not self.with_history:
            raise ValueError('MCP requires the shared execution grant')
        if type(self.with_workspace) is not bool or self.with_workspace and (not self.with_history or self.notebook_only or self.web_only or self.execution_limits is not None):
            raise ValueError('Workspace tools require foreground history execution.')
        self.history_policy = select_history_policy(self.history_policy, enabled=self.with_history)
        if (type(self.with_notes) is not bool or type(self.notes_write) is not bool
                or self.with_notes and not self.with_memory or self.notes_write and not self.with_notes):
            raise ValueError('notes require an explicit memory/notes grant')

    @property
    def max_operations(self) -> int:
        available = self.history_policy.operations if self.with_history else 1 if self.with_reference else 0
        if self.turn_limits:
            available = (self.turn_limits.operations if self.history_policy is HistoryPolicy.STANDARD
                         else min(available, self.turn_limits.operations))
        if self.execution_limits:
            # A named procedure owns its explicit allowance. The old standard
            # history policy is only a fallback; restricted R007 stays restricted.
            return (self.execution_limits.operations if self.history_policy is HistoryPolicy.STANDARD
                    else min(available, self.execution_limits.operations))
        return available

    @property
    def max_calls(self) -> int:
        if self.execution_limits:
            inherited = (self.history_policy.calls if self.with_history and self.history_policy is HistoryPolicy.R007
                         else 2 if self.with_reference and not self.with_history else self.execution_limits.calls)
            return min(inherited, self.execution_limits.calls) + self._research_yields
        return self.max_operations + 1

    @property
    def schema_cost(self) -> int:
        if self.with_history:
            return len(json.dumps(schemas(history=True, reference=self.with_reference, memory=self.with_memory,
                                          notes=self.with_notes, notes_write=self.notes_write, hybrid=self.hybrid_memory, tasks=self.with_tasks,
                                          notebook_only=self.notebook_only, web_only=self.web_only, reminders=self.with_reminders, people=self.with_people, people_write=self.people_write, web=self.with_web, web_provider=self.web_provider, mcp=self.with_mcp, skills=self.with_skills, workspace=self.with_workspace), ensure_ascii=True).encode()) + 32
        return len(json.dumps(tool_schema(), ensure_ascii=True).encode()) + 32 if self.with_reference else 0

    @property
    def working_room(self) -> int:
        # Adaptive requests fit each actual tool batch before dispatch. They do
        # not reserve another generation-sized block of input in advance.
        return (0 if self.accounting else max(self.profile.generation_limit, 6058)) if self.with_history else 0

    @property
    def sql_limit(self) -> int:
        # Source work scales with authorized operations, not the model's input
        # window. Reading the same archive must not become harder at 16K. Two
        # initial slices cover history admission and automatic discovery; model
        # operations share the remaining finite allowance, without resetting it.
        return self.recall_query_sql_limit * (self.max_operations + 2) + self.sql_continuation_reserve

    @property
    def recall_query_sql_limit(self) -> int:
        # One costly query cannot consume every later lookup's work. This is a
        # SQLite VM-work bound, independent of result/context admission and time.
        # Exact ranking must visit the eligible archive. A constant ceiling would
        # turn ordinary growth into a hidden storage limit. Reserve linear work
        # for indexed segments (including keyword-only receipts), with headroom
        # for eligibility and ranking; small archives retain the existing floor.
        # The population is frozen once per ledger, so searches cannot replenish
        # this slice or the total turn budget. Deadline and result bounds still apply.
        return max(1_000_000, 64 * (self._recall_index_size or 0))

    @property
    def recall_index_size(self) -> int | None:
        return self._recall_index_size

    def admit_recall_index(self, segments: int) -> None:
        if type(segments) is not int or segments < 0:
            raise ValueError('Invalid recall index population.')
        if self._recall_index_size is None:
            if self._recall_search_depth:
                raise ValueError('Recall work must be sized before a search starts.')
            self._recall_index_size = segments

    @property
    def sql_continuation_reserve(self) -> int:
        # Four bounded metadata checks per operation also cover one-operation
        # model replies, which check authority more often than a single batch.
        return max(32768 if self.notebook_only else 0, 4096 * (self.max_operations + 1))

    @property
    def payload_limit(self):
        return max(16 * self.profile.context_limit, 2 * self.response_bytes)

    @property
    def review_work_deadline(self):
        # Research reports can take longer than ordinary private reviews. Keep
        # finishing time INSIDE the one saved allowance, including small jobs.
        if self.web_only:
            return self.deadline - min(120, (self.deadline - self.started - self._paused_seconds - self._interrupted_seconds) / 3)
        return self.deadline - min(30, (self.deadline - self.started) / 4)

    def yield_research_request(self, seconds):
        """Replace only an owner-interrupted model attempt's work allowance.

        Actual calls and full generation reservations remain charged and recorded.
        The saved tool limit, input window and successful-work allowance do not
        grow. A provider timeout/failure never receives this interruption credit.
        """
        if (not self.web_only or self.execution_limits is None or self._settled != self.calls - 1
                or self._yielded_ordinal == self.calls or not math.isfinite(seconds) or seconds < 0):
            raise Failure('turn_state', 'Only an owned pending research request may yield.')
        self._yielded_ordinal = self.calls
        self._research_yields += 1
        self._interrupted_seconds += seconds

    def interrupted_research_request(self):
        """Settle a joined provider cancellation without refunding its debit.

        No usage is invented: the full request/generation reservation stays
        charged. Only research may spend its existing remainder on reporting.
        """
        if not self.web_only or self.execution_limits is None or self._settled != self.calls - 1:
            raise Failure('turn_state', 'No pending research request can be settled.')
        self._settled += 1
        self._research_interrupted = True

    def exclude_research_pause(self, seconds):
        """Idle waiting consumes no active-work time; all other debits survive."""
        if not self.web_only or self.execution_limits is None or seconds < 0:
            raise Failure('turn_state', 'Only research can exclude an owned idle pause.')
        self._paused_seconds += seconds

    @contextmanager
    def review_work(self):
        """Tool work cannot consume a private review's closing allowance.

        Authority checks and outcome inspection outside this scope still debit
        the same hard ledger. No reserve resets, extra calls or permission bypass.
        """
        if not (self.notebook_only or self.web_only):
            yield
            return
        self._review_work_depth += 1
        try:
            if time.monotonic() >= self.review_work_deadline:
                raise Failure('review_time_limit', 'Tool work ended to leave time for the private report.')
            self.spend_sql(0)
            self.spend_payload(0)
            yield
        finally:
            self._review_work_depth -= 1

    @contextmanager
    def recall_search_work(self):
        # Discovery may stop without invalidating already delivered evidence.
        # Leave room for normal authority checks and truthful refusal delivery.
        # This reserve stays INSIDE the same turn allowance; writes and exact reads still use
        # its hard limit. A full refusal batch is covered by the execution tests.
        if not self._recall_search_depth:
            self._recall_sql_start = self.sql_steps
        self._recall_search_depth += 1
        try:
            self.spend_sql(0)
            yield
        finally:
            self._recall_search_depth -= 1

    def spend_sql(self, amount: int = 100) -> None:
        self.sql_steps += amount
        if self.sql_steps > self.sql_limit:
            raise Failure('history_work_limit', 'History SQL work allowance exhausted; no scan fallback ran.')
        if self._recall_search_depth and self.sql_steps > self.sql_limit - self.sql_continuation_reserve:
            raise Failure('recall_search_limit',
                'Recall search reached its remaining work allowance; no result from this search was delivered.')
        if self._recall_search_depth and self.sql_steps - self._recall_sql_start > self.recall_query_sql_limit:
            raise Failure('recall_query_limit',
                'This lookup exceeded its database-work allowance; no partial ranking was delivered.')
        if self._review_work_depth and self.sql_steps > self.sql_limit - self.sql_continuation_reserve:
            raise Failure('review_sql_limit', 'Tool work ended to preserve source checks and the private report.')

    def spend_payload(self, amount: int) -> None:
        if amount < 0:
            raise Failure('history_work_limit', 'Invalid source payload allowance.')
        if (self._review_work_depth and self.payload_bytes + amount >
                self.payload_limit - min(131072, self.payload_limit // 4)):
            raise Failure('review_payload_limit', 'Source reads ended to preserve the private report allowance.')
        if self.payload_bytes + amount > self.payload_limit:
            raise Failure('history_work_limit', 'History payload allowance exhausted before allocation.')
        self.payload_bytes += amount

    @property
    def deadline(self) -> float:
        # Explicit turn/job duration is the one shared deadline, including final
        # reporting. A copied legacy model timeout must not silently shorten it.
        seconds = (self.turn_limits.seconds if self.turn_limits else
                   self.execution_limits.seconds if self.execution_limits else self.profile.timeout_seconds)
        return self.started + seconds + self._paused_seconds + self._interrupted_seconds

    @property
    def input_allowance(self) -> int:
        return self.profile.context_limit - self.profile.generation_limit - self.profile.safety_margin - 64 - self.schema_cost

    @property
    def response_bytes(self) -> int:
        # One transport bound, proportional to the saved generation allowance.
        # Covers JSON framing/escaping and reasoning; not an answer-token promise.
        return 65536 + 32 * self.profile.generation_limit

    def remaining_input(self, messages: tuple[Message, ...]) -> int:
        if self.accounting:
            # Provider input counts ALREADY include tool definitions. Cancel the
            # legacy separate schema debit here, never add it to measured usage.
            return self.input_allowance + self.schema_cost - self.accounting.measure(messages)
        return self.input_allowance - sum(message_cost(m) for m in messages)

    @property
    def require_context_rejection(self) -> bool:
        return bool(self.accounting and self.accounting.provider_first and self.accounting.counter is None)

    def validate_configuration(self, system_prompt: str) -> None:
        # Creation needs the actual prompt, not only the profile's scalar values.
        # Reuse admission's accounting, including both message frames, and reserve
        # room for the smallest nonempty UTF-8 input. This does not admit a turn.
        minimum = (Message('system', system_prompt), Message('user', 'x'))
        if self.remaining_input(minimum) < 0:
            if self.can_probe_context(minimum):
                # Reuse the turn owner's existing capability/transport gate.
                # Creation performs no IO and certifies no fit; the actual turn
                # must re-evaluate its own adapter, input and full reply reserve.
                return
            if (provider(self.profile.provider).rejects_context_overflow
                    and not (self.accounting and self.accounting.counter is not None)
                    and len(system_prompt.encode()) <= 4 * self.profile.context_limit):
                # Creation is not a provider request or proof of fit. The
                # request owner may make one bounded provider-first attempt.
                return
            raise Failure('configuration_context_limit',
                'Saved prompt and generation reserve leave no room for even a one-byte message; no conversation was saved.')

    def admit(self, messages: tuple[Message, ...], now: float) -> Request:
        if now >= self.deadline:
            raise Failure('timeout', 'Turn deadline expired before the provider call.')
        if self.calls:
            raise Failure('call_limit', 'Initial admission already occurred; no retry was sent.')
        remaining = self.remaining_input(messages)
        if remaining < 0 and not self.can_probe(messages):
            raise Failure('context_limit', 'Exact conversation and generation reserve do not fit; no model call was sent. Originals remain readable.')
        self.input_estimate = self.request_cost(messages)
        self.calls = 1  # Reserve the full call/combined output ceiling before IO.
        self.generation_debit = self.profile.generation_limit
        self._prefix = messages
        return Request(self.profile, messages, self.deadline, self.response_bytes,
                       self.with_reference or self.with_history,
                       (self.with_reference or self.with_history) and self.max_operations > 0 and self.max_calls > 1,
                       self.with_history, self.with_reference, self.with_memory, self.max_operations,
                       self.with_notes, self.notes_write, self.hybrid_memory, self.with_tasks,
                       require_context_rejection=self.require_context_rejection, notebook_only=self.notebook_only, web_only=self.web_only, reminders=self.with_reminders, people=self.with_people, people_write=self.people_write, web=self.with_web, web_provider=self.web_provider, mcp=self.with_mcp, skills=self.with_skills, workspace=self.with_workspace)

    def request_cost(self, messages):
        return self.accounting.measure(messages) if self.accounting else self.input_allowance - self.remaining_input(messages)

    def can_probe(self, messages):
        return not self.calls and self.can_probe_context(messages)

    def can_probe_context(self, messages, *, accounting=None):
        """Same bounded capability gate for initial and explicit checkpoint IO.

        This is permission to measure at the provider, never proof of token fit.
        Call/deadline/generation bounds are still enforced by the request owner.
        """
        accounting = accounting or self.accounting
        if not accounting or not accounting.provider_first or accounting.counter is not None:
            return False
        accounting.measure(messages)
        return (accounting.last_mode == 'unmeasured_byte_bound'
                and sum(message_cost(m) for m in messages) <= 4 * self.profile.context_limit)

    def context_request(self, messages, now, *, summary=False, probe=False):
        """Explicit compression boundary; consumes the existing finite ledger.

        Unlike an ordinary continuation this may replace a prefix, only after
        the application has established a complete safe checkpoint. Reserve one
        final request when issuing a tools-disabled summary call.
        """
        if now >= self.deadline:
            raise Failure('timeout', 'Context checkpoint exceeded the turn deadline.')
        if self._settled != self.calls or self.calls >= self.max_calls - (1 if summary else 0):
            raise Failure('call_limit', 'No call allowance remains for context checkpoint and answer.')
        if self.remaining_input(messages) < 0 and not (probe and self.can_probe_context(messages)):
            raise Failure('context_limit', 'Context checkpoint does not fit the shared allowance.')
        if self.generation_debit + self.profile.generation_limit > self.max_calls * self.profile.generation_limit:
            raise Failure('generation_limit', 'Context checkpoint exhausted generation allowance.')
        self.calls += 1
        self.generation_debit += self.profile.generation_limit
        self._prefix = messages
        self.input_estimate = self.request_cost(messages)
        # A tools-disabled summary does not submit the completed pending batch.
        # Keep its unsubmitted-work marker for the eventual continuation.
        if not summary:
            self._operation_checkpoint = self.reads
        self._summary_pending = summary
        self._context_probe_pending = False
        return Request(self.profile, messages, self.deadline, self.response_bytes,
            False if summary else self.with_history,
            False if summary else self.reads < self.max_operations and self.calls < self.max_calls,
            False if summary else self.with_history,
            False if summary else self.with_reference,
            False if summary else self.with_memory, self.max_operations,
            False if summary else self.with_notes,
            False if summary else self.notes_write,
            False if summary else self.hybrid_memory,
            False if summary else self.with_tasks,
            require_context_rejection=self.require_context_rejection, notebook_only=False if summary else self.notebook_only, web_only=False if summary else self.web_only,
            reminders=False if summary else self.with_reminders,
            people=False if summary else self.with_people, people_write=False if summary else self.people_write, web=False if summary else self.with_web, web_provider=self.web_provider, mcp=False if summary else self.with_mcp, skills=False if summary else self.with_skills, workspace=False if summary else self.with_workspace)

    def resume_context(self, messages, now):
        """Resume an already-authored pending batch after a settled summary.

        This replaces only the prefix. It grants no call/operation/generation
        allowance and cannot be used during an unresolved provider attempt.
        """
        if not self._summary_pending or self._settled != self.calls:
            raise Failure('turn_state', 'No settled summary checkpoint is available.')
        if now >= self.deadline:
            raise Failure('timeout', 'Context checkpoint exceeded the turn deadline.')
        self._prefix = messages
        self._summary_pending = False
        self._context_probe_pending = True

    def procedure_request(self, messages, now):
        """Next explicit tools-disabled decision/review phase, never a new budget.

        Replaces a procedure input checkpoint, not a foreground conversation
        prefix. The application must revalidate every retained source first.
        """
        if self.execution_limits is None or self.with_history or self.with_reference or not self.calls:
            raise Failure('call_limit', 'No tools-disabled procedure continuation is authorized.')
        return self.context_request(messages, now, probe=self.can_probe_context(messages))

    def care_request(self, messages, now):
        """One complete cleanup group per saved operation and provider attempt.

        Previous groups have durable outcomes before replacing their input. No
        tool schemas, prefix compression or additional generation allowance.
        """
        if not self.notebook_only or self.execution_limits is None or self.reads >= self.max_operations:
            raise Failure('tool_limit', 'No complete cleanup group fits the saved operation allowance.')
        request = self.context_request(messages, now, probe=self.can_probe_context(messages))
        self.reads += 1
        return replace(request, tools=False, allow_tool_calls=False, history=False,
            reference=False, memory=False, notes=False, notes_write=False, hybrid_memory=False, notebook_only=False)

    def rejected_context(self):
        # An explicit input rejection settles sequencing, NOT unknown billing.
        # Keep the full reserved generation debit; no invented zero-token usage.
        if self._settled != self.calls - 1:
            raise Failure('turn_state', 'Context rejection has no pending attempt.')
        self._settled += 1

    def reserve_history_operation(self, now: float) -> None:
        if now >= self.deadline:
            raise Failure('timeout', 'Turn deadline expired before history access.')
        if (not self.with_history or self.reads >= self.max_operations
                or not 1 <= self.calls < self.max_calls or self._settled != self.calls):
            raise Failure('tool_limit', 'Saved operation allowance exhausted; no additional operation ran.')
        self.reads += 1

    def can_finish_history(self, messages):
        """One terminal, effects-disabled request; no speculative tool budget.

        A conservative appended-byte estimate may exceed the window while the
        native request fits. Reuse the bounded provider-rejection gate with a
        fresh measurement, never a byte/token ratio or an enlarged allowance.
        The application must verify returned usage before accepting the answer.
        """
        if self.remaining_input(messages) >= 0:
            return True
        if not self.accounting:
            return False
        fresh = RequestAccounting(self.profile, self.accounting.schema,
            counter=self.accounting.counter, provider_first=self.accounting.provider_first)
        return self.can_probe_context(messages, accounting=fresh)

    def next_history_request(self, messages: tuple[Message, ...], now: float, *, probe=False, final=False, interrupted=False) -> Request:
        if now >= self.deadline:
            raise Failure('timeout', 'Turn deadline expired before continuation.')
        if interrupted and not (self.web_only and self._research_interrupted and messages == self._prefix):
            raise Failure('turn_state', 'Only an interrupted research request can resume its exact input.')
        if (not self.with_history or self.calls >= self.max_calls
                or self.reads <= self._operation_checkpoint and not (interrupted or final and (self.notebook_only or self.web_only))
                or self._settled != self.calls):
            raise Failure('call_limit', 'Saved call allowance exhausted; no additional call ran.')
        if messages[:len(self._prefix)] != self._prefix:
            raise Failure('prefix_changed', 'The frozen history prefix changed; continuation stopped.')
        # A settled checkpoint can remeasure a rewritten prefix. A terminal
        # request can measure exact paired refusals with tools disabled. Ordinary
        # tool continuations still cannot execute against speculative room.
        if self.remaining_input(messages) < 0 and not (
                probe and self._context_probe_pending and self.can_probe_context(messages)
                or final and self.can_finish_history(messages)):
            raise Failure('result_context_limit', 'Exact exchange does not fit; no continuation was sent.')
        if self.max_calls * self.profile.generation_limit - self.generation_debit < self.profile.generation_limit:
            raise Failure('generation_limit', 'The protected final allowance is unavailable.')
        self.calls += 1
        self.generation_debit += self.profile.generation_limit
        self.input_estimate = self.request_cost(messages)
        self._prefix = messages
        self._context_probe_pending = False
        # Several operations may share one response. Each is charged separately;
        # a continuation still needs new work and a settled provider attempt.
        self._operation_checkpoint = self.reads
        self._research_interrupted = False
        return Request(self.profile, messages, self.deadline, self.response_bytes, True,
                       not final and self.calls < self.max_calls and self.reads < self.max_operations,
                       True, self.with_reference, self.with_memory, self.max_operations,
                       self.with_notes, self.notes_write, self.hybrid_memory, self.with_tasks,
                       require_context_rejection=self.require_context_rejection, notebook_only=self.notebook_only, web_only=self.web_only, reminders=self.with_reminders, people=self.with_people, people_write=self.people_write, web=self.with_web, web_provider=self.web_provider, mcp=self.with_mcp, skills=self.with_skills, workspace=self.with_workspace)

    def account(self, reply: Reply) -> None:
        if not isinstance(reply, Reply) or self._settled != self.calls - 1:
            raise Failure('invalid_response', 'Provider reply does not match the reserved call.')
        if len((reply.text + reply.reasoning).encode()) > self.response_bytes:
            raise Failure('response_size', 'Provider output exceeded the inherited byte bound.')
        if reply.output_tokens is not None:
            if type(reply.output_tokens) is not int or not 0 <= reply.output_tokens <= self.profile.generation_limit:
                raise Failure('provider_limit', 'Provider reported output beyond the requested combined generation limit.')
            self.generation_debit -= self.profile.generation_limit - reply.output_tokens
        self._settled += 1

    def reserve_read(self, now: float) -> None:
        if now >= self.deadline:
            raise Failure('timeout', 'Turn deadline expired before the read.')
        if not self.with_reference or self.reads >= self.max_operations or self.calls != 1 or self._settled != 1:
            raise Failure('tool_limit', 'This turn permits one read before its final call; no additional read ran.')
        self.reads = 1

    def search_budget(self, messages: tuple[Message, ...], refusal: Message, *, shares: int = 1) -> SearchBudget:
        room = self.remaining_input(messages) - 128
        if (self.remaining_input(messages + (refusal,)) < 128 if self.accounting else message_cost(refusal) > room):
            raise Failure('result_context_limit',
                'Even a search refusal does not fit with the final reserve; no search ran.')
        # Remaining web searches share this batch's available room. Recompute
        # after every result so unused room carries forward, without changing
        # the window or reserving another generation allowance. The caller has
        # already included every pending result envelope in `messages`.
        if type(shares) is not int or shares < 1:
            raise ValueError('Search shares must be a positive integer.')
        ceiling = room // shares
        measured = self.request_cost(messages) if self.accounting else 0
        return SearchBudget(ceiling, replace(refusal, content=''),
            (lambda candidate: self.request_cost(messages + (candidate,)) - measured <= ceiling)
            if self.accounting else None)

    def page_allowance(self, messages: tuple[Message, ...], receipt: Message) -> int:
        # Reserve a full typed receipt plus envelope variation, then allow worst-
        # case seven-byte double JSON escaping per source byte (e.g. NUL becomes
        # six characters inside the envelope and its backslash is escaped again).
        # Measure the actual final
        # representation again before append; never alter an admitted message.
        room = self.remaining_input(messages + (receipt,)) - 128
        if room < 0:
            raise Failure('result_context_limit', 'Even a reference receipt does not fit with the final reserve; no read ran.')
        return room // 7

    def read_allowance(self, messages: tuple[Message, ...], refusal: Message) -> int:
        """Bound one prospective read; actual escaped result fit is checked later.

        Source IO already has independent identity/payload/deadline bounds. This
        ceiling limits the candidate page before allocation, not a promise that
        its contents fit after JSON escaping. Keep the truthful refusal and the
        same variation reserve available before performing any source operation.
        """
        room = self.remaining_input(messages + (refusal,)) - 128
        if room < 0:
            raise Failure('result_context_limit',
                'Even a reference receipt does not fit with the final reserve; no read ran.')
        return room

    def fit_read_result(self, messages: tuple[Message, ...], template: Message, result: dict) -> dict | None:
        """Fit only this unsubmitted exact page, never the frozen input prefix.

        Binary search is over Unicode characters of the already bounded page;
        offsets/continuations remain UTF-8 bytes. The source body, identity, hash
        and total length are untouched. No source IO or model-specific ratio.
        None means even the attributed refusal cannot fit: caller may use its
        already reserved minimal refusal, never discard earlier input instead.
        """
        room = self.remaining_input(messages) - 128
        def fits(page):
            text = json.dumps(page, ensure_ascii=False, separators=(',', ':'))
            if self.accounting:
                return self.remaining_input(messages + (replace(template, content=text),)) >= 128
            return message_cost(replace(template, content=text)) <= room
        if fits(result):
            return result
        if result.get('reason') is not None:
            return None  # Preserve source errors; never turn one into a page.
        content, offset, end, total = (result[k] for k in ('content', 'offset', 'end', 'total_bytes'))
        if (not isinstance(content, str) or any(type(n) is not int for n in (offset, end, total))
                or not 0 <= offset <= end <= total or offset + len(content.encode()) != end):
            raise Failure('invalid_tool_result', 'Exact read result has inconsistent byte boundaries.')
        refused = dict(result, status='not_delivered', content='', end=offset,
                       next_offset=None, reason='insufficient_result_capacity')
        best = refused if fits(refused) else None
        # Full result was checked above. Below EOF, metadata sizes and escaped
        # payload cost grow monotonically. Excluding full length avoids an EOF
        # next_offset/status change being mistaken for a monotone transition.
        low, high = 1, len(content) - 1
        while low <= high:
            middle = (low + high) // 2
            prefix = content[:middle]
            boundary = offset + len(prefix.encode())
            candidate = dict(result, status='page', content=prefix, end=boundary,
                             next_offset=boundary, reason=None)
            if fits(candidate):
                best, low = candidate, middle + 1
            else:
                high = middle - 1
        return best

    def fit_provenance_result(self, messages: tuple[Message, ...], template: Message, result: dict) -> dict | None:
        """Fit a bounded provenance candidate without cutting an edge or rereading.

        The adapter bounds and authorizes the candidate. This is the same final
        representation/cost and variation reserve as exact body fitting; no
        worst-case escaping multiplier is an additional admission authority.
        The continuation is the last delivered edge, never an undelivered one.
        """
        edges, offset = result.get('results'), result.get('offset')
        if (not isinstance(edges, list) or type(offset) is not int or offset < 0
                or type(result.get('delivered_count')) is not int
                or result['delivered_count'] != len(edges)):
            raise Failure('invalid_tool_result', 'Provenance page has inconsistent edge boundaries.')
        previous = offset
        for edge in edges:
            ident = edge.get('edge_id') if isinstance(edge, dict) else None
            if type(ident) is not int or ident <= previous:
                raise Failure('invalid_tool_result', 'Provenance edges must be in increasing continuation order.')
            previous = ident
        room = self.remaining_input(messages) - 128
        def fits(page):
            text = json.dumps(page, ensure_ascii=False, separators=(',', ':'))
            if self.accounting:
                return self.remaining_input(messages + (replace(template, content=text),)) >= 128
            return message_cost(replace(template, content=text)) <= room
        if fits(result):
            return result
        if result.get('reason') not in (None, 'result_capacity'):
            return None  # Do not turn a source/authority failure into evidence.
        refused = dict(result, status='not_delivered', results=[], delivered_count=0,
                       next_offset=offset, reason='result_capacity')
        best = refused if fits(refused) else None
        # Full page was tested separately: EOF metadata need not be monotone.
        # Below it, growing whole-edge prefixes and edge-id cursors are monotone.
        low, high = 1, len(edges) - 1
        while low <= high:
            middle = (low + high) // 2
            candidate = dict(result, status='page', results=edges[:middle],
                             delivered_count=middle, next_offset=edges[middle - 1]['edge_id'],
                             reason='result_capacity')
            if fits(candidate):
                best, low = candidate, middle + 1
            else:
                high = middle - 1
        return best

    def finish_request(self, messages: tuple[Message, ...], now: float) -> Request:
        if now >= self.deadline:
            raise Failure('timeout', 'Turn deadline expired before final generation; read receipts remain available.')
        if self.calls != 1 or self.calls >= self.max_calls or self.reads != 1 or self._settled != 1 or not self.with_reference:
            raise Failure('call_limit', 'No additional model call is permitted.')
        if messages[:len(self._prefix)] != self._prefix:
            raise Failure('prefix_changed', 'An admitted message changed; the tool chain stopped.')
        if self.remaining_input(messages) < 0:
            raise Failure('result_context_limit', 'Exact tool exchange does not fit with the final generation reserve; no final call was sent.')
        if self.max_calls * self.profile.generation_limit - self.generation_debit < self.profile.generation_limit:
            raise Failure('generation_limit', 'The protected final allowance is unavailable.')
        self.input_estimate = self.request_cost(messages)
        self.calls += 1
        self.generation_debit += self.profile.generation_limit
        return Request(self.profile, messages, self.deadline, self.response_bytes, True, False,
                       require_context_rejection=self.require_context_rejection)
