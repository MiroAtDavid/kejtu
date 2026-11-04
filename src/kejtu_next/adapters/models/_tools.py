"""Bounded native batches; execution policy remains with the shared ledger."""
import json

from ...core.budget import message_cost
from ...core.providers import provider as provider_spec
from ...core.records import Failure, Message, ToolCall
from ...core.vision import wire_content, MAX_REQUEST_IMAGES


def decode_calls(value, provider: str, *, allowed: bool, names=('read_reference',), maximum=1) -> tuple[ToolCall, ...]:
    if not value:
        return ()
    if not allowed:
        raise Failure('unsupported_tool', 'Provider returned a tool request; no tool was executed.')
    try:
        if type(maximum) is not int or maximum < 1 or not isinstance(value, list) or len(value) > maximum:
            raise ValueError('native batch bound')
        calls, identities = [], set()
        for position, raw in enumerate(value, 1):
            if not isinstance(raw, dict):
                raise ValueError('native shape')
            function = raw['function']
            if not isinstance(function, dict) or function['name'] not in names:
                raise ValueError('unknown function')
            if provider_spec(provider).unique_tool_ids:
                ident, arguments = raw['id'], function['arguments']
                if raw.get('type') != 'function' or not isinstance(arguments, str):
                    raise ValueError('native shape')
            else:
                # Ollama pairing is ordered/name-based. Local receipt identities
                # must still distinguish repeated functions within this reply.
                ident = function['name'] + '_' + str(position)
                if not isinstance(function['arguments'], dict):
                    raise ValueError('native arguments')
                arguments = json.dumps(function['arguments'], ensure_ascii=False, separators=(',', ':'))
            if (not isinstance(ident, str) or not 1 <= len(ident) <= 256
                    or any(ord(c) < 33 or ord(c) > 126 for c in ident)
                    or ident in identities):
                raise ValueError('call identity bound or duplicate identity')
            # The transport already bounds the complete response using the saved
            # generation allowance. Domain owners validate argument size/content
            # and can return recoverable whole-batch feedback. A second, smaller
            # per-tool ceiling here used to reject valid edits before that owner.
            arguments.encode('utf-8')
            identities.add(ident)
            calls.append(ToolCall(ident, function['name'], arguments))
        return tuple(calls)
    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        raise Failure('invalid_tool_call', 'A bounded batch of valid supported native calls was required; no operation in this batch ran.') from error


def _ollama_system_prefix(messages: list[dict]) -> list[dict]:
    """One system slot; exact later metadata is anchored to wire-message indices.

    RVN/Qwen's native template refuses a system role anywhere except index zero.
    App history notices/identities therefore share that first slot. An anchor is
    the zero-based index of the following message in the projected wire list
    (which includes system at zero), not a new archive/history ordinal. No user,
    assistant or tool body moves into this higher-authority prefix.
    """
    if not any(row['role'] == 'system' for row in messages[1:]):
        return messages
    if messages[0]['role'] != 'system':
        raise Failure('provider_configuration', 'Ollama system projection requires an initial system message.')
    if set(messages[0]) != {'role', 'content'}:
        raise Failure('provider_configuration', 'Ollama cannot fold a system message with additional native fields.')
    first = dict(messages[0])
    result = [first]
    parts = [first['content']]
    for row in messages[1:]:
        if row['role'] != 'system':
            result.append(row)
            continue
        if set(row) != {'role', 'content'}:
            raise Failure('provider_configuration', 'Ollama cannot fold a system message with additional native fields.')
        framing = f'\n\n[before message {len(result)}]\n'
        # Spend only the frame already charged for this removed system slot.
        # This reuses the ledger's estimator; no second window or admission rule.
        original_cost = message_cost(Message('system', row['content']))
        if len((framing + row['content']).encode('utf-8')) > original_cost:
            raise Failure('context_limit', 'Ollama system framing exceeds its admitted message cost.')
        parts.extend((framing, row['content']))
    first['content'] = ''.join(parts)
    return result


def wire_messages(messages: list[dict], provider: str, *, model=None) -> list[dict]:
    result = []
    images = 0
    for message in messages:
        try:
            content = wire_content(message, provider, model)
        except ValueError as error:
            raise Failure('image_input_unavailable', str(error)) from None
        if isinstance(content, list):
            images += sum(part['type'] == 'image_url' for part in content)
        if images > MAX_REQUEST_IMAGES:
            raise Failure('image_input_size', 'This request contains too many images. Continue with fewer pages.')
        row = {'role': message['role'], 'content': content}
        if 'reasoning' in message:
            row['reasoning_content' if provider_spec(provider).transport == 'chat_completions' else 'thinking'] = message['reasoning']
        if 'tool_calls' in message:
            if provider_spec(provider).transport == 'chat_completions':
                row['tool_calls'] = [{'id': c['id'], 'type': 'function',
                    'function': {'name': c['name'], 'arguments': c['arguments']}} for c in message['tool_calls']]
            else:
                row['tool_calls'] = [{'function': {'name': c['name'],
                    'arguments': json.loads(c['arguments'])}} for c in message['tool_calls']]
        if message['role'] == 'tool':
            key = 'tool_call_id' if provider_spec(provider).transport == 'chat_completions' else 'tool_name'
            row[key] = message[key]
        result.append(row)
    return _ollama_system_prefix(result) if provider == 'ollama' else result
