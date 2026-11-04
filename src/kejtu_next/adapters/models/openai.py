"""Stateless OpenAI Responses transport over the existing owned-child lifetime.

Only application-granted functions are sent. Encrypted reasoning and assistant
phase/order travel through the existing private reasoning-state owner, never the
visible answer. The saved profile and shared ledger retain budget authority.
"""
import asyncio
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request as HttpRequest, build_opener

from ...core.providers import provider
from ...core.records import Failure, ModelProgress, Profile, Reply, Request
from ...core.reference import tool_schema
from ...core.tools import schemas
from ...core.vision import wire_content, MAX_REQUEST_IMAGES
from ._secrets import CredentialText, credential_echo, validate_key
from ._streaming import sse, invalid
from ._responses_output import ResponseOutput
from ._tools import decode_calls
from ._transport import NoRedirect, request_bytes, worker as transport_worker

STATE_FORMAT = 'openai_responses_v1'


def bearer(value, subscription=False):
    if not subscription:
        return validate_key(value)
    if not isinstance(value, str) or not 1 <= len(value) <= 16384 or any(not 33 <= ord(c) <= 126 for c in value):
        raise Failure('credential_required', 'Sign in to ChatGPT in Settings → Models.')
    return value


def subscription_failure(code, status=None):
    messages = {
        'subscription_sharing_usage_limit_exceeded': 'The ChatGPT plan or Kejtu usage limit was reached. Manage usage in ChatGPT settings.',
        'subscription_sharing_user_not_eligible': 'ChatGPT plan usage is unavailable for this account or workspace.',
        'subscription_sharing_usage_unavailable': 'ChatGPT could not check plan usage. Try again later.',
        'subscription_sharing_user_unavailable': 'ChatGPT account information is temporarily unavailable. Try again later.',
        'subscription_sharing_unsupported_capability': 'ChatGPT rejected an unsupported request capability.',
        'subscription_sharing_route_not_supported': 'ChatGPT plan usage is not enabled for this request route.',
        'subscription_sharing_invalid_user': 'ChatGPT could not validate this account. Check the connection in Settings → Models.',
        'chatpass_v2_scope_not_authorized': 'This ChatGPT connection does not grant the required permission.',
        'chatpass_v2_invalid_authorization_context': 'ChatGPT rejected the connection’s authorization context.',
        'context_length_exceeded': 'The request exceeds this ChatGPT model’s context window.',
    }
    reason = ('provider_context_limit' if code == 'context_length_exceeded' else
              'chatgpt_usage_limit' if code == 'subscription_sharing_usage_limit_exceeded' else 'chatgpt_request')
    message = messages.get(code, 'ChatGPT could not complete this request. Check your account connection and try again.')
    suffix = f' ({code})' if code in messages else f' (HTTP {status})' if status else ''
    return Failure(reason, message + suffix + ' No automatic retry or API billing fallback was used.')


def tool_specs(values):
    if not values.get('tools'):
        return []
    if not values.get('history'):
        return [tool_schema()]
    flags = {name: values.get(name, False) for name in (
        'reference', 'memory', 'notes', 'notes_write', 'tasks', 'notebook_only',
        'web_only', 'reminders', 'people', 'people_write', 'web', 'mcp', 'skills', 'workspace')}
    return schemas(history=True, hybrid=values.get('hybrid_memory', False),
                   web_provider=values.get('web_provider', 'searxng'), **flags)


def _output(items, *, allow_tools, names, maximum, incomplete=False, subscription=False):
    """Validate an entire response before any native call can be dispatched."""
    if not isinstance(items, list):
        raise ValueError('missing output')
    text, calls = [], []
    identities = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('invalid output item')
        ident = item.get('id')
        if not isinstance(ident, str) or not ident or len(ident) > 256 or ident in identities:
            raise ValueError('invalid output identity')
        identities.add(ident)
        kind = item.get('type')
        if item.get('status') not in (None, 'completed'):
            # Incomplete output text is accepted separately for exhaustion; a
            # partial function must never reach the execution owner.
            if not incomplete or kind not in ('message', 'reasoning') or item.get('status') != 'incomplete':
                raise ValueError('unfinished native item')
        if kind == 'message':
            if item.get('role') != 'assistant' or item.get('phase') not in (None, 'commentary', 'final_answer'):
                raise ValueError('invalid assistant message')
            parts = item.get('content')
            if not isinstance(parts, list):
                raise ValueError('invalid message body')
            for part in parts:
                if not isinstance(part, dict):
                    raise ValueError('invalid message part')
                if part.get('type') == 'refusal':
                    raise Failure('provider_filtered', 'OpenAI declined this request; no completed answer was supplied.')
                if part.get('type') != 'output_text' or not isinstance(part.get('text'), str):
                    raise ValueError('unsupported output content')
                text.append(part['text'])
        elif kind == 'reasoning':
            if not isinstance(item.get('summary'), list):
                raise ValueError('invalid reasoning summary')
            if not incomplete and (not isinstance(item.get('encrypted_content'), str) or not item['encrypted_content']):
                raise ValueError('stateless reasoning is missing its encrypted state')
        elif kind == 'function_call':
            if subscription and item.get('namespace') != 'kejtu':
                raise ValueError('unexpected function namespace')
            calls.append(dict(id=item.get('call_id'), type='function',
                              function=dict(name=item.get('name'), arguments=item.get('arguments'))))
        else:
            # Hosted tools/async effects are not part of Kejtu's capability set.
            raise ValueError('unsupported output item')
    decoded = decode_calls(calls, 'openai', allowed=allow_tools, names=names, maximum=maximum)
    return ''.join(text), decoded


def decode_reply(body, *, allow_tools=False, names=('read_reference',), maximum=1, subscription=False):
    try:
        result = json.loads(body)
        if not isinstance(result, dict) or result.get('object') != 'response':
            raise ValueError('missing response envelope')
        status = result.get('status')
        if status == 'failed':
            code = (result.get('error') or {}).get('code')
            if subscription:
                raise subscription_failure(code)
            reason = 'provider_context_limit' if code == 'context_length_exceeded' else 'provider_interrupted'
            raise Failure(reason, 'OpenAI could not complete the response; no retry was sent.')
        if status == 'incomplete':
            reason = (result.get('incomplete_details') or {}).get('reason')
            if reason == 'content_filter':
                raise Failure('provider_filtered', 'OpenAI filtered this response.')
            if reason != 'max_output_tokens':
                raise ValueError('unknown incomplete response')
        elif status != 'completed' or result.get('error'):
            raise ValueError('response not completed')
        text, calls = _output(result['output'], allow_tools=allow_tools, names=names, maximum=maximum,
                              incomplete=status == 'incomplete', subscription=subscription)
        if status == 'incomplete' and calls:
            raise ValueError('incomplete function batch')
        if status == 'completed' and any(item.get('status') == 'incomplete' for item in result['output']):
            raise ValueError('incomplete item in completed response')
        usage = result.get('usage') or {}
        if not isinstance(usage, dict):
            raise ValueError('invalid usage')
        counts = [usage.get(key) for key in ('input_tokens', 'output_tokens', 'total_tokens')]
        if any(n is not None and (type(n) is not int or n < 0) for n in counts):
            raise ValueError('invalid token counter')
        if all(n is not None for n in counts) and counts[2] != counts[0] + counts[1]:
            raise ValueError('inconsistent token counters')
        details = usage.get('output_tokens_details')
        if details is not None:
            if not isinstance(details, dict):
                raise ValueError('invalid output breakdown')
            n = details.get('reasoning_tokens')
            if n is not None and (type(n) is not int or n < 0 or counts[1] is not None and n > counts[1]):
                raise ValueError('invalid reasoning counter')
        # Preserve the full ordered output, including encrypted reasoning and
        # phase. Reconstructing just text/function IDs loses the model contract.
        state = json.dumps(dict(format='chatgpt_responses_v1' if subscription else STATE_FORMAT, output=result['output']),
                           ensure_ascii=False, separators=(',', ':')) if result['output'] else ''
        return Reply(text, 'length' if status == 'incomplete' else 'tool_calls' if calls else 'stop',
                     state, counts[0], counts[1], calls)
    except Failure:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise Failure('invalid_response', 'OpenAI did not return a valid completed response envelope.') from None


def wire_input(messages, profile):
    result = []
    subscription = provider(profile.provider).subscription
    state_format = provider(profile.provider).reasoning_state_format
    images = 0
    for message in messages:
        role = message['role']
        try:
            content = wire_content(message, profile.provider, profile.model)
        except ValueError as error:
            raise Failure('image_input_unavailable', str(error)) from None
        if isinstance(content, list):
            images += sum(part['type'] == 'input_image' for part in content)
        if images > MAX_REQUEST_IMAGES:
            raise Failure('image_input_size', 'This request contains too many images. Continue with fewer pages.')
        if role == 'assistant' and message.get('reasoning'):
            try:
                state = json.loads(message['reasoning'])
                if set(state) != {'format', 'output'} or state['format'] != state_format:
                    raise ValueError('wrong reasoning dialect')
                # A saved private envelope may only replace its own exact
                # assistant text and calls, never project extra native actions.
                names = tuple(call['name'] for call in message.get('tool_calls', []))
                text, calls = _output(state['output'], allow_tools=bool(names), names=names,
                                      maximum=max(1, len(names)), subscription=subscription)
                expected = tuple((c['id'], c['name'], c['arguments']) for c in message.get('tool_calls', []))
                if text != message['content'] or tuple((c.id, c.name, c.arguments) for c in calls) != expected:
                    raise ValueError('reasoning identity mismatch')
                result.extend(state['output'])
            except (ValueError, TypeError, KeyError, Failure):
                raise Failure('provider_configuration', 'OpenAI reasoning state does not match its assistant message.') from None
        elif role == 'tool':
            # Responses accepts typed image output on the original function
            # result: retain its call identity and lower-authority tool role.
            result.append(dict(type='function_call_output', call_id=message['tool_call_id'], output=content))
        elif role in ('system', 'user', 'assistant'):
            if message['content'] or not message.get('tool_calls'):
                result.append(dict(role='developer' if subscription and role == 'system' else role, content=content))
            for call in message.get('tool_calls', []):
                item = dict(type='function_call', call_id=call['id'], name=call['name'], arguments=call['arguments'])
                if subscription:
                    item['namespace'] = 'kejtu'
                result.append(item)
        else:
            raise Failure('provider_configuration', 'Unsupported OpenAI message role.')
    return result


def response_stream(response, packet, progress):
    """Only provisional text leaves SSE; a valid terminal envelope releases tools."""
    text, size, identity, sequence = [], 0, None, -1
    output = ResponseOutput(packet['response_bytes'])
    try:
        for raw in sse(response, packet['response_bytes']):
            event = json.loads(raw)
            if not isinstance(event, dict) or not isinstance(event.get('type'), str):
                raise invalid()
            if 'sequence_number' in event:
                number = event['sequence_number']
                if type(number) is not int or number <= sequence:
                    raise invalid()
                sequence = number
            kind = event['type']
            if kind == 'response.created':
                value = event['response']
                if identity is not None or not isinstance(value.get('id'), str):
                    raise invalid()
                identity = value['id']
            elif kind == 'response.output_text.delta':
                delta = event['delta']
                if not isinstance(delta, str):
                    raise invalid()
                size += len(delta.encode())
                if size > packet['response_bytes']:
                    raise Failure('response_size', 'OpenAI progress exceeded the inherited byte bound.')
                text.append(delta)
                progress(ModelProgress('text', delta))
            elif kind == 'response.output_item.added':
                if identity is None:
                    raise invalid()
                output.item(event, complete=False)
                item = event.get('item')
                if item and item.get('type') in ('function_call', 'reasoning'):
                    progress(ModelProgress('phase', 'tools' if item['type'] == 'function_call' else 'thinking'))
            elif kind == 'response.output_item.done':
                if identity is None:
                    raise invalid()
                output.item(event, complete=True)
            elif kind in ('response.completed', 'response.incomplete', 'response.failed'):
                value = event['response']
                if identity is None or value.get('id') != identity or kind != 'response.' + value.get('status', ''):
                    raise invalid()
                if kind == 'response.completed':
                    value = output.completed(value)
                body = json.dumps(value, ensure_ascii=False).encode()
                if len(body) > packet['response_bytes']:
                    raise Failure('response_size', 'OpenAI response exceeded the inherited byte bound.')
                # Terminal content must agree with the text actually streamed.
                reply = decode_reply(body, allow_tools=bool(packet.get('allow_tool_calls')),
                    names=tuple(s['function']['name'] for s in tool_specs(packet)),
                    maximum=packet.get('operation_limit', 1), subscription=packet['profile']['provider'] == 'chatgpt')
                if reply.text != ''.join(text):
                    raise invalid()
                progress(ModelProgress('phase', 'finishing'))
                return body
            elif kind in ('error', 'response.refusal.delta', 'response.refusal.done'):
                if kind == 'error' and packet['profile']['provider'] == 'chatgpt':
                    raise subscription_failure(event.get('code'))
                raise Failure('provider_filtered' if 'refusal' in kind else 'provider_interrupted',
                              'OpenAI did not complete this response; no retry was sent.')
        raise invalid()
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise invalid() from None


def _check_secret(reply, key):
    fields = (reply.text, reply.reasoning) + tuple(v for c in reply.tool_calls for v in (c.id, c.name, c.arguments))
    if any(credential_echo(field, key) for field in fields):
        raise Failure('credential_exposure', 'Provider response contained a credential; no answer was saved.')


class OpenAIResponses:
    supports_streaming = True

    def __init__(self, provider_id, api_key, *, spawn=None):
        self.spec = provider(provider_id)
        if self.spec.transport != 'responses':
            raise ValueError('The provider requires another transport')
        self.rejects_context_overflow = self.spec.rejects_context_overflow
        self._api_key = bearer(api_key, self.spec.subscription)
        self.spawn = spawn or asyncio.create_subprocess_exec

    async def complete(self, request: Request):
        return await self._complete(request)

    async def stream(self, request: Request, progress):
        return await self._complete(request, progress)

    async def _complete(self, request, progress=None):
        if request.profile.provider != self.spec.id:
            raise Failure('provider_configuration', 'The adapter requires its own saved OpenAI profile.')
        body = await request_bytes(request, 'kejtu_next.adapters.models.openai', self.spec.label,
                                   spawn=self.spawn, api_key=self._api_key, progress=(progress or (lambda event: None)) if self.spec.subscription else progress)
        names = tuple(s['function']['name'] for s in tool_specs(vars(request)))
        reply = decode_reply(body, allow_tools=request.allow_tool_calls, names=names, maximum=request.operation_limit, subscription=self.spec.subscription)
        _check_secret(reply, self._api_key)
        return reply


def exchange(packet, *, opener=None, progress=None):
    profile = Profile(**packet['profile'])
    if profile.provider not in ('openai', 'chatgpt'):
        raise Failure('provider_configuration', 'The adapter requires an OpenAI profile.')
    subscription = profile.provider == 'chatgpt'
    key = bearer(packet.get('api_key'), subscription)
    payload = dict(model=profile.model, input=wire_input(packet['messages'], profile),
                   max_output_tokens=profile.generation_limit, stream=progress is not None,
                   store=False, truncation='disabled', reasoning=dict(
                       effort='none' if profile.thinking_level is False else profile.thinking_level,
                       context='current_turn'))
    if subscription:
        # ChatGPT plan inference rejects these API-only controls. Keep the
        # saved allowance as a local acceptance bound, never a claimed spend cap.
        payload.pop('max_output_tokens')
        payload.pop('truncation')
        payload['stream'] = True
        payload['include'] = ['reasoning.encrypted_content']
        progress = progress or (lambda event: None)
    native = tool_specs(packet)
    if native:
        # Existing tools have optional arguments and domain-owned validation.
        # Explicit non-strict mode preserves those schemas and permissions.
        payload['tools'] = [dict(type='function', **s['function'], strict=False) for s in native]
        if subscription:
            payload['tools'] = [dict(type='namespace', name='kejtu', description='Kejtu tools authorized for this request.', tools=payload['tools'])]
        payload['tool_choice'] = 'auto' if packet.get('allow_tool_calls') else 'none'
    encoded = json.dumps(payload, ensure_ascii=False).encode()
    if len(encoded) > 48 * 1024 * 1024:
        raise Failure('request_size', 'The OpenAI request exceeded the transport size bound.')
    request = HttpRequest(profile.endpoint.rstrip('/') + '/responses', data=encoded,
        headers={'Content-Type': 'application/json', 'Accept-Encoding': 'identity',
                 'Authorization': 'Bearer ' + key}, method='POST')
    opener = opener or build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=packet['timeout']) as response:
            if response.headers.get('Content-Encoding', 'identity') != 'identity':
                raise Failure('invalid_response', 'Compressed provider responses are not supported.')
            guard = CredentialText(key)
            def publish(event):
                if event.kind == 'text':
                    safe = guard.feed(event.text)
                    if safe:
                        progress(ModelProgress('text', safe))
                else:
                    progress(event)
            body = (response.read(packet['response_bytes'] + 1) if progress is None
                    else response_stream(response, packet, publish))
            if len(body) > packet['response_bytes']:
                raise Failure('response_size', 'OpenAI response exceeded the inherited byte bound.')
            reply = decode_reply(body, allow_tools=bool(packet.get('allow_tool_calls')),
                names=tuple(s['function']['name'] for s in native), maximum=packet.get('operation_limit', 1), subscription=packet['profile']['provider'] == 'chatgpt')
            _check_secret(reply, key)
            if subscription and (reply.output_tokens is None or reply.output_tokens > profile.generation_limit):
                raise Failure('provider_limit', 'ChatGPT output exceeded the saved reply allowance or omitted usage. No tool calls from this response were executed. This connection cannot impose a server-side generation cap.')
            if progress is not None:
                tail = guard.finish()
                if tail:
                    progress(ModelProgress('text', tail))
    except HTTPError as error:
        status, detail = error.code, {}
        try:
            raw = error.read(8193)
            envelope = json.loads(raw) if len(raw) <= 8192 else {}
            if isinstance(envelope, dict) and isinstance(envelope.get('error'), dict):
                detail = envelope['error']
        except (ValueError, OSError):
            pass
        finally:
            error.close()
        code = detail.get('code')
        if subscription:
            raise subscription_failure(code, status) from None
        reason = {401: 'provider_auth', 403: 'provider_auth', 402: 'provider_balance',
                  429: 'provider_rate_limit'}.get(status, 'provider_http')
        if status == 400 and code == 'context_length_exceeded':
            reason = 'provider_context_limit'
        elif status == 400 and code == 'content_filter':
            reason = 'provider_filtered'
        elif status == 429 and code in ('insufficient_quota', 'billing_hard_limit_reached'):
            reason = 'provider_balance'
        raise Failure(reason, 'OpenAI rejected the request; no retry was sent.') from None
    except (URLError, TimeoutError, OSError):
        raise Failure('provider_unavailable', 'OpenAI transport failed; no retry was sent.') from None
    return body


def worker():
    return transport_worker(exchange)


if __name__ == '__main__':
    if sys.argv[1:] != ['--worker']:
        raise SystemExit('This module is an internal transport worker.')
    raise SystemExit(worker())
