"""Immutable boundary values for the bounded development conversation core."""
from dataclasses import dataclass
import math
from .providers import provider

from .execution import HistoryPolicy


def message_byte_limit(context_limit: int) -> int:
    """Existing raw-message admission bound, separate from context/token fit."""
    return context_limit * 4


class Failure(Exception):
    """An application-authored reason, never an untrusted provider error body."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Profile:
    model: str
    endpoint: str
    context_limit: int
    generation_limit: int
    thinking_level: bool | str = False
    timeout_seconds: float = 120.0
    safety_margin: int = 512
    keep_loaded: bool = False
    provider: str = 'ollama'

    def __post_init__(self):
        if type(self.keep_loaded) is not bool:
            raise ValueError('keep_loaded must be a boolean')
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError('model must be explicit and nonempty')
        provider(self.provider).validate(self)
        for name in ('context_limit', 'generation_limit', 'safety_margin'):
            value = getattr(self, name)
            if type(value) is not int or value < (0 if name == 'safety_margin' else 1):
                raise ValueError(f'{name} must be an appropriate nonnegative integer')
        if self.generation_limit + self.safety_margin >= self.context_limit:
            raise ValueError('generation and margin must leave input room')
        if (type(self.timeout_seconds) not in (int, float)
                or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0):
            raise ValueError('timeout_seconds must be finite and positive')


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class Message:
    role: str
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    tool_name: str | None = None
    reasoning: str | None = None


def message_record(message: Message) -> dict:
    """Sparse transport envelope; ordinary R004 messages remain byte-shape exact."""
    result = {'role': message.role, 'content': message.content}
    if message.tool_calls:
        result['tool_calls'] = [dict(id=c.id, name=c.name, arguments=c.arguments) for c in message.tool_calls]
    for key in ('tool_call_id', 'tool_name', 'reasoning'):
        value = getattr(message, key)
        if value is not None:
            result[key] = value
    return result


@dataclass(frozen=True)
class Request:
    profile: Profile
    messages: tuple[Message, ...]
    deadline: float
    response_bytes: int
    tools: bool = False
    allow_tool_calls: bool = False
    history: bool = False
    reference: bool = False
    memory: bool = False
    operation_limit: int = 1
    notes: bool = False
    notes_write: bool = False
    hybrid_memory: bool = False
    tasks: bool = False
    # Request-local transport obligation, never a saved profile permission.
    # Runtime-verified adapters must recheck their contract before generation.
    require_context_rejection: bool = False
    # Private-review capability restriction; never a saved conversation setting.
    notebook_only: bool = False
    web_only: bool = False
    reminders: bool = False
    people: bool = False
    people_write: bool = False
    web: bool = False
    web_provider: str = "searxng"
    mcp: bool = False
    skills: bool = False
    workspace: bool = False


@dataclass(frozen=True)
class ModelProgress:
    """Provisional display progress; never a Reply, usage receipt or tool dispatch."""
    kind: str
    text: str = ''


@dataclass(frozen=True)
class Reply:
    text: str
    finish_reason: str
    reasoning: str = ''
    input_tokens: int | None = None
    output_tokens: int | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class Turn:
    id: str
    conversation_id: str
    status: str
    user: str
    answer: str | None
    partial: str | None
    reason: str | None
    detail: str | None
    calls: int
    sequence: int = 0


@dataclass(frozen=True)
class Conversation:
    id: str
    profile: Profile
    system_prompt: str
    reference_id: str | None = None
    history_enabled: bool = False
    history_policy: HistoryPolicy | None = None
    memory_bank_id: str | None = None
    notes_enabled: bool = False
    notes_writable: bool = False  # Observed permission, not a model/profile setting.
    hybrid_memory: bool = False  # Frozen capability contract, not a model setting.
    tasks_enabled: bool = False
    reminders_enabled: bool = False
    people_mode: str = 'off'

    @property
    def needs_reasoning_history(self) -> bool:
        """The characterized native projection, not a universal memory policy."""
        return ((self.reference_id is not None or self.history_enabled) and provider(self.profile.provider).preserve_reasoning
                and self.profile.thinking_level is not False)
