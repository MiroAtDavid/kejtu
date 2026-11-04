"""Request measurements, never a second allocator or persistent text cache."""
from dataclasses import asdict, dataclass
import hashlib
import json

from .records import Message, message_record
from .reference import tool_schema
from .tools import schemas
from .vision import projection, image_token_bound


def request_schema(*, history=False, reference=False, memory=False, notes=False,
                   notes_write=False, hybrid=False, tasks=False, notebook_only=False, web_only=False, reminders=False, people=False, people_write=False, web=False, web_provider='searxng', mcp=False, skills=False, workspace=False):
    return (schemas(history=True, reference=reference, memory=memory, notes=notes,
                    notes_write=notes_write, hybrid=hybrid, tasks=tasks, notebook_only=notebook_only, web_only=web_only, reminders=reminders, people=people, people_write=people_write, web=web, web_provider=web_provider, mcp=mcp, skills=skills, workspace=workspace) if history else
            [tool_schema()] if reference else [])


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()


def identity(profile, schema):
    return digest([asdict(profile), schema, 'context-v1'])


def message_ids(messages):
    return tuple(digest(message_record(message)) for message in messages)


def text_bound(message: Message, *, provider=None):
    """Conservative incremental estimate, NOT a tokenizer or JSON wire size.

    Transport escaping is decoded before tokenization. Charge actual strings,
    explicit framing, and the fields sent by either supported provider instead.
    No universal bytes/token ratio or model-name calibration is inferred.
    """
    text, images = projection(message.role, message.content, message.tool_name)
    # Inline bytes are transport/storage, not language tokens. This is the
    # provider-aware image estimate, inside the existing ledger.
    total = len(text.encode()) + 32 + sum(image_token_bound(image, provider) for image in images)
    if message.reasoning is not None:
        total += len(message.reasoning.encode()) + 16
    for call in message.tool_calls:
        total += sum(len(value.encode()) for value in (call.id, call.name, call.arguments)) + 64
    if message.tool_call_id:
        total += len(message.tool_call_id.encode()) + 16
    if message.tool_name:
        total += len(message.tool_name.encode()) + 16
    return total


@dataclass(frozen=True)
class UsageAnchor:
    request_identity: str
    messages: tuple[str, ...]
    input_tokens: int

    def matches(self, request_identity, messages):
        return (request_identity == self.request_identity and len(messages) >= len(self.messages)
                and message_ids(messages[:len(self.messages)]) == self.messages)


class RequestAccounting:
    """Validated counter first; otherwise measured prefix + bounded additions.

    A counter is injected by a provider adapter only when validated against its
    complete request representation. No tokenizer dependency is guessed here.
    Anchors contain hashes/counts only; mutation of ANY prefix item invalidates it.
    The ledger continues to own allocation, generation and deadlines.
    """
    def __init__(self, profile, schema, *, counter=None, provider_first=False):
        self.profile, self.schema = profile, schema
        self.identity = identity(profile, schema)
        self.counter = counter
        self.provider_first = provider_first
        self.anchor = None
        self.last_mode = 'unmeasured_byte_bound'

    def measure(self, messages):
        if self.counter is not None:
            count = self.counter(self.profile, messages, self.schema)
            if type(count) is not int or count < 0:
                raise ValueError('Invalid validated request count')
            self.last_mode = 'validated_counter'
            return count
        if self.anchor and self.anchor.matches(self.identity, messages):
            delta = messages[len(self.anchor.messages):]
            self.last_mode = 'provider_measured' if not delta else 'provider_anchor_plus_byte_bound'
            return self.anchor.input_tokens + sum(text_bound(message, provider=self.profile.provider) for message in delta)
        self.last_mode = 'unmeasured_byte_bound'
        return sum(text_bound(message, provider=self.profile.provider) for message in messages) + len(json.dumps(self.schema, ensure_ascii=False).encode()) + 32

    def observe(self, messages, reply):
        if type(reply.input_tokens) is int and reply.input_tokens >= 0:
            self.anchor = UsageAnchor(self.identity, message_ids(messages), reply.input_tokens)
        else:
            # Never substitute cumulative usage or completion usage for input.
            self.anchor = None

    def receipt(self, messages):
        estimate = self.measure(messages)
        return dict(method=self.last_mode, request_input=estimate,
            request_fingerprint=digest([self.identity, message_ids(messages)]),
            generation_reserved=self.profile.generation_limit,
            safety_margin=self.profile.safety_margin, framing_allowance=64,
            context_limit=self.profile.context_limit)
