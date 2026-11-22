"""Keep an application's rendered answer and its native replay text together.

Registered Responses and Anthropic state contain visible text and opaque reasoning.
Only visible text is editable: item identity, order, phase, encrypted reasoning
and native calls must survive presentation unchanged. Anthropic prefix bindings
stay unchanged so edited earlier text invalidates later thinking on projection. Other dialects retain their exact private state.
"""
from dataclasses import replace
import json

from .providers import provider
from .records import Failure


def response_text(state, profile):
    dialect = provider(profile.provider).transport
    if dialect not in ('responses', 'anthropic'):
        return None
    try:
        value = json.loads(state)
        if (set(value) != ({'format', 'output', 'bindings'} if dialect == 'anthropic' else {'format', 'output'})
                or value['format'] != provider(profile.provider).reasoning_state_format
                or not isinstance(value['output'], list)):
            raise ValueError
        parts = []
        # Keep item kinds aligned with adapters.models.openai._output and
        # Presentation.test_response_item_contract; full validation stays there.
        for item in value['output']:
            if dialect == 'anthropic':
                if item['type'] == 'text' and isinstance(item.get('text'), str):
                    parts.append(item)
                elif item['type'] not in ('thinking', 'redacted_thinking', 'tool_use'):
                    raise ValueError
            elif item['type'] == 'message':
                if item['role'] != 'assistant':
                    raise ValueError
                for part in item['content']:
                    if part['type'] != 'output_text' or not isinstance(part['text'], str):
                        raise ValueError
                    parts.append(part)
            elif item['type'] not in ('reasoning', 'function_call'):
                raise ValueError
        return value, parts, ''.join(part['text'] for part in parts)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise Failure('provider_state_integrity', 'The saved Responses state has an invalid text envelope.') from None


def present(reply, text, profile, maximum):
    if text == reply.text:
        return reply
    state = reply.reasoning
    parsed = response_text(state, profile)
    if parsed is not None:
        value, parts, original = parsed
        if original != reply.text or not parts:
            raise Failure('provider_state_integrity', 'The Responses state does not match the answer being presented.')
        # Both conversational renderers normalize line endings before editing
        # the tail. Normalize each part in place first so an earlier CRLF cannot
        # make the tail edit absorb commentary into a different message phase.
        if '\r' in original and '\r' not in text:
            skip_lf = False
            for part in parts:
                body = part['text']
                if not body:
                    continue
                part['text'] = (body[1:] if skip_lf and body.startswith('\n') else body).replace('\r\n', '\n').replace('\r', '\n')
                skip_lf = body.endswith('\r')
            original = original.replace('\r\n', '\n').replace('\r', '\n')
        # Replace the smallest enclosing text span. Keep untouched message parts
        # and their phase/order; never flatten commentary and final-answer items.
        start = 0
        while start < min(len(original), len(text)) and original[start] == text[start]:
            start += 1
        end, new_end = len(original), len(text)
        while end > start and new_end > start and original[end - 1] == text[new_end - 1]:
            end -= 1
            new_end -= 1
        # Tail rendering can trim whitespace from an earlier commentary item.
        # Insert prose where the replaced fence ended, not where that whitespace
        # began. An all-whitespace edit belongs to its last overlapping part.
        anchor = start + len(original[start:end].rstrip()) - 1
        if anchor < start:
            anchor = end - 1
        offset, inserted = 0, False
        for index, part in enumerate(parts):
            body = part['text']
            stop = offset + len(body)
            overlaps = stop > start and offset < end
            insertion = start == end and (offset <= start < stop or index == len(parts) - 1 and start == stop)
            if overlaps or insertion:
                destination = insertion or offset <= anchor < stop
                replacement = text[start:new_end] if destination and not inserted else ''
                part['text'] = body[:max(0, start - offset)] + replacement + body[max(0, end - offset):]
                inserted = inserted or destination
            offset = stop
        state = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    if len((text + state).encode()) > maximum:
        raise Failure('response_size', 'The presented answer and required provider state exceed the saved reply bound.')
    return replace(reply, text=text, reasoning=state)
