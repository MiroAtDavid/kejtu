"""Shared bounded Chat Completions dialect; supplier policy comes from the registry."""
import asyncio
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request as HttpRequest, build_opener

from ...core.records import Failure, ModelProgress, Profile, Reply, Request
from ...core.reference import tool_schema
from ...core.tools import schemas
from ._transport import NoRedirect, request_bytes, worker as transport_worker
from ._tools import decode_calls, wire_messages
from ._streaming import chat_completion_stream
from ._secrets import CredentialText, validate_key, credential_echo
from ...core.providers import provider


def decode_reply(body: bytes, *, allow_tools: bool = False, names=('read_reference',), maximum=1, provider_id='deepseek') -> Reply:
    try:
        result = json.loads(body)
        if not isinstance(result, dict) or result.get('object') != 'chat.completion':
            raise ValueError('missing completion envelope')
        choices = result['choices']
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ValueError('one choice required')
        choice = choices[0]
        if type(choice.get('index')) is not int or choice['index'] != 0:
            raise ValueError('invalid choice index')
        message = choice['message']
        if not isinstance(message, dict) or message.get('role') != 'assistant':
            raise ValueError('missing assistant message')
        finish = choice.get('finish_reason')
        if message.get('function_call') or (finish == 'tool_calls' and not allow_tools):
            raise Failure('unsupported_tool', 'Provider returned a tool request; no tool was executed.')
        calls = decode_calls(message.get('tool_calls'), provider_id, allowed=allow_tools, names=names, maximum=maximum)
        if bool(calls) != (finish == 'tool_calls'):
            raise ValueError('tool finish mismatch')
        if finish == 'content_filter':
            raise Failure('provider_filtered', 'The provider filtered the response; no completed answer was supplied.')
        if finish in ('insufficient_system_resource', 'aborted'):
            raise Failure('provider_interrupted', 'The provider interrupted generation; no completed answer was supplied.')
        if finish not in ('stop', 'length', 'tool_calls'):
            raise ValueError('unknown finish reason')
        # Nullable content is valid for reasoning-only exhaustion, not an empty success.
        text, reasoning = message.get('content'), message.get('reasoning_content')
        if any(v is not None and not isinstance(v, str) for v in (text, reasoning)):
            raise ValueError('invalid text')
        usage = result.get('usage')
        if usage is None:
            usage = {}
        if not isinstance(usage, dict):
            raise ValueError('invalid usage')
        counts = [usage.get(k) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')]
        if any(n is not None and (type(n) is not int or n < 0) for n in counts):
            raise ValueError('invalid usage counter')
        if all(n is not None for n in counts) and counts[2] != counts[0] + counts[1]:
            raise ValueError('inconsistent total')
        details = usage.get('completion_tokens_details')
        if details is not None:
            if not isinstance(details, dict):
                raise ValueError('invalid output breakdown')
            thought_count = details.get('reasoning_tokens')
            if thought_count is not None and (type(thought_count) is not int or thought_count < 0
                    or counts[1] is not None and thought_count > counts[1]):
                raise ValueError('invalid reasoning counter')
        # completion_tokens already includes reasoning; never add its breakdown.
        return Reply(text or '', finish, reasoning or '', counts[0], counts[1], calls)
    except Failure:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        raise Failure('invalid_response', 'The provider did not return a valid completed response envelope.') from error


class ChatCompletions:
    supports_streaming = True
    def __init__(self, provider_id: str, api_key: str, *, spawn=None):
        self.spec = provider(provider_id)
        self.rejects_context_overflow = self.spec.rejects_context_overflow
        if self.spec.transport != 'chat_completions':
            raise ValueError('The provider requires another transport')
        self._api_key = validate_key(api_key)
        self.spawn = spawn or asyncio.create_subprocess_exec

    async def complete(self, request: Request) -> Reply:
        return await self._complete(request)

    async def stream(self, request: Request, progress) -> Reply:
        return await self._complete(request, progress)

    async def _complete(self, request: Request, progress=None) -> Reply:
        if request.profile.provider != self.spec.id:
            raise Failure('provider_configuration', 'The adapter requires its own saved provider profile.')
        body = await request_bytes(request, 'kejtu_next.adapters.models.chat_completions', self.spec.label,
                                   spawn=self.spawn, api_key=self._api_key, progress=progress)
        names = tuple(s['function']['name'] for s in schemas(history=True, reference=request.reference, memory=request.memory,
                      notes=request.notes, notes_write=request.notes_write,hybrid=request.hybrid_memory,tasks=request.tasks,notebook_only=request.notebook_only,web_only=request.web_only,reminders=request.reminders,people=request.people,people_write=request.people_write,web=request.web,web_provider=request.web_provider,mcp=request.mcp,skills=request.skills,workspace=request.workspace)) if request.history else ('read_reference',)
        reply = decode_reply(body, allow_tools=request.allow_tool_calls, names=names, maximum=request.operation_limit, provider_id=self.spec.id)
        # JSON wire escapes are already decoded. Also check one layer of supported
        # literal escapes in each field; arbitrary encoded secrets are not covered.
        fields = (reply.text, reply.reasoning) + tuple(value for c in reply.tool_calls for value in (c.id, c.name, c.arguments))
        if any(credential_echo(field, self._api_key) for field in fields):
            raise Failure('credential_exposure', 'Provider response contained a credential; no answer was saved.')
        return reply


def thinking_parameters(profile):
    policy = provider(profile.provider).thinking_for(profile.model)
    level = profile.thinking_level
    if policy.dialect == 'deepseek':
        result = {'thinking': {'type': 'disabled' if level is False else 'enabled'}}
        if level is not False:
            result['reasoning_effort'] = 'high' if level is True or level == 'medium' else level
        return result
    if policy.dialect == 'effort':
        return {'reasoning_effort': level}
    if policy.dialect == 'always':
        return {}
    if policy.dialect == 'preserved':
        return {'thinking': {'type': 'enabled', 'keep': 'all'} if level is True else {'type': 'disabled'}}
    raise Failure('provider_configuration', 'Unsupported Chat Completions thinking contract.')


def exchange(packet: dict, *, opener=None, progress=None) -> bytes:
    profile = Profile(**packet['profile'])
    spec = provider(profile.provider)
    if spec.transport != 'chat_completions':
        raise Failure('provider_configuration', 'The provider requires another transport.')
    api_key = validate_key(packet.get('api_key'))
    payload = {'model': profile.model, 'messages': wire_messages(packet['messages'], profile.provider, model=profile.model), 'stream': False,
               'max_tokens': profile.generation_limit}
    payload.update(thinking_parameters(profile))
    if progress is not None:
        payload.update(stream=True, stream_options={'include_usage': True})
    if packet.get('tools'):
        payload['tools'] = schemas(history=True, reference=packet.get('reference', False), memory=packet.get('memory', False),
                                  notes=packet.get('notes', False), notes_write=packet.get('notes_write', False),hybrid=packet.get('hybrid_memory',False),tasks=packet.get('tasks',False),notebook_only=packet.get('notebook_only',False),web_only=packet.get('web_only',False),reminders=packet.get('reminders',False),people=packet.get('people',False),people_write=packet.get('people_write',False),web=packet.get('web',False),web_provider=packet.get('web_provider','searxng'),mcp=packet.get('mcp',False),skills=packet.get('skills',False),workspace=packet.get('workspace',False)) if packet.get('history') else [tool_schema()]
        payload['tool_choice'] = 'auto' if packet.get('allow_tool_calls') else 'none'
    encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    if len(encoded) > 48 * 1024 * 1024:
        raise Failure('request_size', 'The provider request is too large. Continue with fewer document pages or smaller images.')
    request = HttpRequest(profile.endpoint.rstrip('/') + '/chat/completions',
        data=encoded,
        headers={'Content-Type': 'application/json', 'Accept-Encoding': 'identity',
                 'Authorization': 'Bearer ' + api_key}, method='POST')
    # urllib's default HTTPS handler verifies certificate/hostname. Do not supply
    # an unverified TLS context, discover ambient proxies or follow any redirect.
    opener = opener or build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=packet['timeout']) as response:
            if response.headers.get('Content-Encoding', 'identity') != 'identity':
                raise Failure('invalid_response', 'Compressed provider responses are not supported by this bounded adapter.')
            if progress is None:
                body = response.read(packet['response_bytes'] + 1)
            else:
                guard = CredentialText(api_key)
                def publish(event):
                    if event.kind == 'text':
                        safe = guard.feed(event.text)
                        if safe:
                            progress(ModelProgress('text', safe))
                    else:
                        if event.kind == 'reset':
                            guard.pending = ''
                        progress(event)
                body = chat_completion_stream(response, packet, publish)
                names = tuple(item['function']['name'] for item in payload.get('tools', []))
                reply = decode_reply(body, allow_tools=bool(packet.get('allow_tool_calls')),
                    names=names, maximum=packet.get('operation_limit', 1), provider_id=profile.provider)
                fields = (reply.text, reply.reasoning) + tuple(value for call in reply.tool_calls
                    for value in (call.id, call.name, call.arguments))
                if any(credential_echo(field, api_key) for field in fields):
                    raise Failure('credential_exposure', 'Provider response contained a credential; no answer was saved.')
                tail = guard.finish()
                if tail and not reply.tool_calls:
                    progress(ModelProgress('text', tail))
    except HTTPError as error:
        status = error.code
        context_overflow = False
        filtered = False
        if status == 400:
            try:
                raw = error.read(8193)
                envelope = json.loads(raw) if len(raw) <= 8192 else {}
                detail = envelope.get('error', {})
                if isinstance(detail, dict):
                    filtered = detail.get('type') == 'content_filter'
                    message = detail.get('message', '')
                    context_overflow = (detail.get('code') == 'context_length_exceeded' or
                        isinstance(message, str) and (
                            'maximum context length' in message.lower() or
                            'context length exceeded' in message.lower() or
                            'input token exceed' in message.lower() or
                            'input token length too long' in message.lower() or
                            'prompt tokens + max_tokens exceeds' in message.lower()))
            except (ValueError, OSError, AttributeError):
                pass
        error.close()
        if filtered:
            raise Failure('provider_filtered', 'The provider filtered this request.') from None
        if context_overflow:
            raise Failure('provider_context_limit', 'The provider explicitly rejected oversized input.') from None
        reason = {401: 'provider_auth', 403: 'provider_auth', 402: 'provider_balance', 429: 'provider_rate_limit'}.get(status, 'provider_http')
        raise Failure(reason, 'The provider returned an HTTP error; no retry was sent.') from None
    except (URLError, TimeoutError, OSError):
        raise Failure('provider_unavailable', 'Provider transport failed; no retry was sent.') from None
    if len(body) > packet['response_bytes']:
        raise Failure('response_size', 'Provider response exceeded the inherited byte bound.')
    return body


def worker() -> int:
    return transport_worker(exchange)


if __name__ == '__main__':
    if sys.argv[1:] != ['--worker']:
        raise SystemExit('This module is an internal transport worker.')
    raise SystemExit(worker())
