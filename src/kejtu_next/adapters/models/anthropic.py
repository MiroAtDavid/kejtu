"""Native Anthropic Messages; existing grants, ledger and owned-child transport.

Signed thinking and ordered content use the private provider-state owner. No
hosted tools, automatic retries, account login or independent execution path.
"""
import asyncio
import hashlib
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request as HttpRequest, build_opener

from ...core.providers import provider
from ...core.records import Failure, ModelProgress, Profile, Reply, message_record
from ...core.vision import wire_content, MAX_REQUEST_IMAGES
from ._secrets import CredentialText, credential_echo, validate_key
from ._streaming import Assembly, sse, invalid
from ._tools import decode_calls, system_prefix, tool_specs
from ._transport import NoRedirect, request_bytes, worker as transport_worker

STATE_FORMAT = 'anthropic_messages_v1'
API_VERSION = '2026-10-21'


def headers(key):
    return {'Authorization': 'Bearer ' + validate_key(key), 'anthropic-version': API_VERSION,
            'Content-Type': 'application/json', 'Accept-Encoding': 'identity'}


def _content(blocks, *, allow_tools=False, names=('read_reference',), maximum=1):
    if not isinstance(blocks, list):
        raise ValueError('missing content')
    text, calls = [], []
    for block in blocks:
        kind = block['type']
        if kind == 'text':
            if not isinstance(block.get('text'), str):
                raise ValueError('invalid text')
            text.append(block['text'])
        elif kind == 'thinking':
            if (not isinstance(block.get('thinking'), str)
                    or not isinstance(block.get('signature'), str) or not block['signature']):
                raise ValueError('missing signed thinking')
        elif kind == 'redacted_thinking':
            if not isinstance(block.get('data'), str) or not block['data']:
                raise ValueError('missing redacted thinking')
        elif kind == 'tool_use':
            if not isinstance(block.get('input'), dict):
                raise ValueError('invalid tool input')
            calls.append(dict(id=block.get('id'), type='function', function=dict(
                name=block.get('name'), arguments=json.dumps(block['input'], ensure_ascii=False, separators=(',', ':'), allow_nan=False))))
        else:
            # Server tools and fallback are not requested. Never reinterpret an
            # unknown block as a completed local tool or silently lose evidence.
            raise ValueError('unsupported content')
    return ''.join(text), decode_calls(calls, 'anthropic', allowed=allow_tools, names=names, maximum=maximum)


def decode_reply(body, *, allow_tools=False, names=('read_reference',), maximum=1, context=None):
    try:
        result = json.loads(body)
        if result['type'] != 'message' or result['role'] != 'assistant' or not isinstance(result['id'], str) or not result['id']:
            raise ValueError('invalid message envelope')
        stop = result['stop_reason']
        if stop == 'refusal':
            raise Failure('provider_filtered', 'Anthropic declined this request.')
        if stop not in ('end_turn', 'stop_sequence', 'tool_use', 'max_tokens', 'model_context_window_exceeded'):
            raise ValueError('incomplete message')
        text, calls = _content(result['content'], allow_tools=allow_tools, names=names, maximum=maximum)
        if bool(calls) != (stop == 'tool_use'):
            raise ValueError('unfinished or inconsistent tool batch')
        usage = result['usage']
        counts = [usage.get(k, 0) for k in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens')]
        if (not {'input_tokens', 'output_tokens'} <= set(usage)
                or any(type(n) is not int or n < 0 for n in counts)):
            raise ValueError('invalid usage')
        state = json.dumps(dict(format=STATE_FORMAT, output=result['content'], bindings=_bindings(result['content'], context)), ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        finish = 'tool_calls' if calls else 'length' if stop in ('max_tokens', 'model_context_window_exceeded') else 'stop'
        return Reply(text, finish, state, sum(counts[:3]), counts[3], calls)
    except Failure:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
        raise Failure('invalid_response', 'Anthropic did not return a valid completed message.') from None


def wire_messages(messages, profile, tools=()):
    result, images, states = [], 0, {}
    for message in messages:
        role = message['role']
        if role == 'system':
            result.append(dict(role='system', content=message['content']))
            continue
        try:
            content = wire_content(message, 'anthropic', profile.model)
            if isinstance(content, list):
                images += sum(part['type'] == 'image' for part in content)
            if images > MAX_REQUEST_IMAGES:
                raise Failure('image_input_size', 'This request contains too many images. Continue with fewer pages.')
            if role == 'assistant' and message.get('reasoning'):
                state = json.loads(message['reasoning'])
                if (set(state) != {'format', 'output', 'bindings'} or state['format'] != STATE_FORMAT
                        or not isinstance(state['bindings'], list) or len(state['bindings']) != len(state['output'])
                        or any(b is not None and (not isinstance(b, str) or len(b) != 64
                            or any(c not in '0123456789abcdef' for c in b)) for b in state['bindings'])):
                    raise ValueError('wrong provider state')
                expected = message.get('tool_calls', [])
                text, calls = _content(state['output'], allow_tools=bool(expected),
                    names=tuple(c['name'] for c in expected), maximum=max(1, len(expected)))
                if text != message['content'] or tuple((c.id, c.name, c.arguments) for c in calls) != tuple((c['id'], c['name'], c['arguments']) for c in expected):
                    raise ValueError('provider state identity mismatch')
                blocks = state['output']
            else:
                blocks = content if isinstance(content, list) else [dict(type='text', text=content)] if content else []
                if role == 'assistant':
                    for call in message.get('tool_calls', []):
                        blocks.append(dict(type='tool_use', id=call['id'], name=call['name'], input=json.loads(call['arguments'])))
            if role == 'tool':
                blocks = [dict(type='tool_result', tool_use_id=message['tool_call_id'], content=content)]
                role = 'user'
            elif role not in ('user', 'assistant'):
                raise ValueError('unsupported message role')
            # The API combines consecutive same-role messages. Keep tool results
            # first and ordered; never change their call IDs or promote them.
            if result and role == 'user' and result[-1]['role'] == role:
                result[-1]['content'].extend(blocks)
            else:
                row = dict(role=role, content=blocks)
                result.append(row)
                if role == 'assistant' and message.get('reasoning'):
                    states[id(row)] = state['bindings']
        except (ValueError, TypeError, KeyError, Failure) as error:
            if message.get('reasoning'):
                raise Failure('provider_state_integrity', 'Anthropic state does not match its assistant message.') from None
            if isinstance(error, Failure):
                raise
            raise Failure('provider_configuration', 'Anthropic message projection is invalid.') from None
    # Reuse the system-slot projection already needed by local models. Only
    # application system messages move; user and tool bodies keep their roles.
    if result and result[0]['role'] != 'system':
        result.insert(0, dict(role='system', content=''))
    result = system_prefix(result, 'Anthropic')
    system, rows = (result[0]['content'], result[1:]) if result else ('', [])
    digest = _prefix(system, tools, [])
    projected = []
    for row in rows:
        current = digest.copy()
        _feed(current, 'role', row['role'])
        bindings = states.get(id(row))
        kept = []
        for index, block in enumerate(row['content']):
            if block['type'] in ('thinking', 'redacted_thinking') and (
                    bindings is None or bindings[index] != current.hexdigest()):
                # Recall refresh, history selection and presentation can change
                # the prefix. Never replay opaque thoughts about removed input.
                # Keep exact text/calls/results and the original private record.
                continue
            kept.append(block)
            _feed(current, 'block', block)
        if kept:
            projected.append(dict(role=row['role'], content=kept))
            digest = current
    return system, projected


def _feed(digest, kind, value):
    raw = json.dumps([kind, value], ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    digest.update(str(len(raw)).encode() + b':' + raw)


def _prefix(system, tools, messages):
    digest = hashlib.sha256()
    _feed(digest, 'system', system)
    _feed(digest, 'tools', list(tools))
    for row in messages:
        _feed(digest, 'role', row['role'])
        for block in row['content']:
            _feed(digest, 'block', block)
    return digest


def _bindings(blocks, context):
    # A digest, not a second history store. Compare the actual admitted request,
    # including tools and images, before reusing each opaque thinking block.
    if context is None:
        return [None] * len(blocks)
    system, messages, tools = context
    digest = _prefix(system, tools, messages)
    _feed(digest, 'role', 'assistant')
    bindings = []
    for block in blocks:
        bindings.append(digest.hexdigest())
        _feed(digest, 'block', block)
    return bindings


def request_context(profile, messages, native):
    tools = [dict(name=s['function']['name'], description=s['function']['description'],
                  input_schema=s['function']['parameters']) for s in native]
    system, messages = wire_messages(messages, profile, tools)
    return system, messages, tools


def message_stream(response, packet, progress):
    assembly = Assembly(packet['response_bytes'], progress)
    message, active, partial, ended = None, None, [], False
    final_usage = False
    try:
        for raw in sse(response, packet['response_bytes']):
            event = json.loads(raw)
            kind = event['type']
            if kind == 'ping':
                continue
            if kind == 'error':
                raise Failure('provider_interrupted', 'Anthropic interrupted the stream; no retry was sent.')
            if kind == 'message_start':
                if message is not None:
                    raise invalid()
                message = event['message']
                if message['content'] != [] or message.get('stop_reason') is not None:
                    raise invalid()
            elif message is None:
                raise invalid()
            elif kind == 'content_block_start':
                if active is not None or ended or type(event['index']) is not int or event['index'] != len(message['content']):
                    raise invalid()
                active = event['index']
                block = event['content_block']
                if block['type'] not in ('text', 'thinking', 'redacted_thinking', 'tool_use'):
                    raise invalid()
                assembly.charge(json.dumps(block, ensure_ascii=False))
                if block['type'] == 'tool_use':
                    if block.get('input') != {}:
                        raise invalid()
                    assembly.tool()
                elif block['type'] == 'text' and block.get('text'):
                    progress(ModelProgress('text', block['text']))
                elif block['type'] in ('thinking', 'redacted_thinking'):
                    progress(ModelProgress('phase', 'thinking'))
                message['content'].append(block)
                partial = []
            elif kind in ('content_block_delta', 'content_block_stop'):
                if active is None or type(event['index']) is not int or event['index'] != active:
                    raise invalid()
                block = message['content'][active]
                if kind == 'content_block_stop':
                    if partial:
                        block['input'] = json.loads(''.join(partial))
                    active = None
                    continue
                delta = event['delta']
                field = {'text_delta': ('text', 'text'), 'thinking_delta': ('thinking', 'thinking'),
                         'signature_delta': ('thinking', 'signature'), 'input_json_delta': ('tool_use', 'partial_json')}.get(delta['type'])
                if field is None or block['type'] != field[0]:
                    raise invalid()
                piece = delta[field[1]]
                assembly.charge(piece)
                if field[1] == 'partial_json':
                    partial.append(piece)
                else:
                    block[field[1]] = block.get(field[1], '') + piece
                    if field[1] == 'text' and piece:
                        progress(ModelProgress('text', piece))
            elif kind == 'message_delta':
                if active is not None:
                    raise invalid()
                delta = event['delta']
                if delta.get('stop_reason') is not None:
                    if ended:
                        raise invalid()
                    ended = True
                    message['stop_reason'] = delta['stop_reason']
                    message['stop_sequence'] = delta.get('stop_sequence')
                if 'usage' in event:
                    usage = event['usage']
                    if 'output_tokens' in usage:
                        count = usage['output_tokens']
                        if type(count) is not int or count < message['usage'].get('output_tokens', 0):
                            raise invalid()
                        final_usage = True
                    message['usage'].update(usage)
            elif kind == 'message_stop':
                if active is not None or not ended or not final_usage:
                    raise invalid()
                return assembly.encoded(message)
            else:
                # Unknown state-changing events need a reviewed projection.
                raise invalid()
    except Failure:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
        raise invalid() from None
    raise invalid()


def _check_secret(reply, key):
    if any(credential_echo(value, key) for value in (reply.text, reply.reasoning,
            *(c.arguments for c in reply.tool_calls), *(c.name for c in reply.tool_calls), *(c.id for c in reply.tool_calls))):
        raise Failure('credential_exposure', 'Anthropic returned credential material; the reply was not accepted.')


class AnthropicMessages:
    supports_streaming = True

    def __init__(self, provider_id, api_key, *, spawn=None):
        if provider_id != 'anthropic':
            raise Failure('provider_configuration', 'Use the Anthropic provider.')
        self.spec = provider(provider_id)
        self.rejects_context_overflow = self.spec.rejects_context_overflow
        self._api_key = validate_key(api_key)
        self.spawn = spawn or asyncio.create_subprocess_exec

    async def complete(self, request):
        return await self._complete(request)

    async def stream(self, request, progress):
        return await self._complete(request, progress)

    async def _complete(self, request, progress=None):
        if request.profile.provider != 'anthropic':
            raise Failure('provider_configuration', 'Use a saved Anthropic profile.')
        native = tool_specs(vars(request))
        context = request_context(request.profile, [message_record(row) for row in request.messages], native)
        body = await request_bytes(request, 'kejtu_next.adapters.models.anthropic', 'Anthropic',
                                   spawn=self.spawn, api_key=self._api_key, progress=progress)
        reply = decode_reply(body, allow_tools=request.allow_tool_calls,
            names=tuple(s['function']['name'] for s in native), maximum=request.operation_limit, context=context)
        _check_secret(reply, self._api_key)
        return reply


def exchange(packet, *, opener=None, progress=None):
    profile = Profile(**packet['profile'])
    if profile.provider != 'anthropic':
        raise Failure('provider_configuration', 'Use an Anthropic profile.')
    key = validate_key(packet.get('api_key'))
    native = tool_specs(packet)
    system, messages, tools = request_context(profile, packet['messages'], native)
    payload = dict(model=profile.model, messages=messages, max_tokens=profile.generation_limit,
                   stream=progress is not None, thinking=dict(type='adaptive', display='omitted'),
                   output_config=dict(effort=profile.thinking_level))
    if system:
        payload['system'] = system
    if native:
        payload['tools'] = tools
        payload['tool_choice'] = dict(type='auto' if packet.get('allow_tool_calls') else 'none')
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
    if len(encoded) > 32 * 1024 * 1024:
        raise Failure('request_size', 'The Anthropic request exceeded its transport size bound.')
    request = HttpRequest(profile.endpoint.rstrip('/') + '/messages', data=encoded, headers=headers(key), method='POST')
    opener = opener or build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=packet['timeout']) as response:
            if response.headers.get('Content-Encoding', 'identity') != 'identity':
                raise invalid()
            guard = CredentialText(key)
            def publish(event):
                if event.kind == 'text':
                    safe = guard.feed(event.text)
                    if safe:
                        progress(ModelProgress('text', safe))
                else:
                    progress(event)
            body = (response.read(packet['response_bytes'] + 1) if progress is None
                    else message_stream(response, packet, publish))
            if len(body) > packet['response_bytes']:
                raise Failure('response_size', 'Anthropic response exceeded the inherited byte bound.')
            reply = decode_reply(body, allow_tools=bool(packet.get('allow_tool_calls')),
                names=tuple(s['function']['name'] for s in native), maximum=packet.get('operation_limit', 1))
            _check_secret(reply, key)
            if progress is not None:
                tail = guard.finish()
                if tail:
                    progress(ModelProgress('text', tail))
            return body
    except HTTPError as error:
        status = error.code
        # Do not expose bodies that may echo user material or credentials.
        error.close()
        reason = {401: 'provider_auth', 403: 'provider_auth', 402: 'provider_balance',
                  429: 'provider_rate_limit'}.get(status, 'provider_http')
        raise Failure(reason, 'Anthropic rejected the request; no retry was sent.') from None
    except (URLError, TimeoutError, OSError):
        raise Failure('provider_unavailable', 'Anthropic transport failed; no retry was sent.') from None


if __name__ == '__main__':
    if sys.argv[1:] != ['--worker']:
        raise SystemExit('This module is an internal transport worker.')
    raise SystemExit(transport_worker(exchange))
