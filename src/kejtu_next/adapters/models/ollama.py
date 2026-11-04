"""One bounded Ollama call in an owned, cancellable transport process."""
import asyncio
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request as HttpRequest, build_opener

from ...core.records import Failure, Profile, Reply, Request
from ...core.reference import tool_schema
from ...core.tools import schemas
from ._transport import NoRedirect, request_bytes, worker as transport_worker
from ._tools import decode_calls, wire_messages
from ._ollama_context import characterized, verified
from ._streaming import ollama_stream


def decode_reply(body: bytes, *, allow_tools: bool = False, names=('read_reference',), maximum=1) -> Reply:
    try:
        result = json.loads(body)
        if not isinstance(result, dict) or result.get('done') is not True:
            raise ValueError('missing completion envelope')
        message = result['message']
        if not isinstance(message, dict) or message.get('role') != 'assistant':
            raise ValueError('missing assistant message')
        calls = decode_calls(message.get('tool_calls'), 'ollama', allowed=allow_tools, names=names, maximum=maximum)
        text, reasoning = message.get('content', ''), message.get('thinking', '')
        if not isinstance(text, str) or not isinstance(reasoning, str):
            raise ValueError('invalid text')
        usage = [result.get('prompt_eval_count'), result.get('eval_count')]
        if any(n is not None and (type(n) is not int or n < 0) for n in usage):
            raise ValueError('invalid usage')
        finish = result.get('done_reason')
        if finish not in {'stop', 'length'}:
            raise ValueError('unknown finish reason')
        if calls and finish != 'stop':
            raise ValueError('incomplete tool call')
        return Reply(text, 'tool_calls' if calls else finish, reasoning, *usage, calls)
    except Failure:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        raise Failure('invalid_response', 'Ollama did not return a valid completed response envelope.') from error


class Ollama:
    supports_streaming = True
    def __init__(self, *, spawn=None):
        self.spawn = spawn or asyncio.create_subprocess_exec

    async def context_capability(self, profile: Profile, deadline: float) -> bool:
        if not characterized(profile):
            return False
        # No chat/debug-render/model load request. Reuse the owned transport's
        # deadline, byte bound, cancellation and child reaping for metadata.
        raw = await request_bytes(Request(profile, (), deadline, 1024),
            'kejtu_next.adapters.models._ollama_context', 'Ollama metadata', spawn=self.spawn)
        try:
            result = json.loads(raw)
            return isinstance(result, dict) and result.get('verified') is True
        except (ValueError, UnicodeError, RecursionError):
            raise Failure('invalid_response', 'Runtime verification returned invalid metadata.') from None

    async def complete(self, request: Request) -> Reply:
        return await self._complete(request)

    async def stream(self, request: Request, progress) -> Reply:
        return await self._complete(request, progress)

    async def _complete(self, request: Request, progress=None) -> Reply:
        if request.profile.provider != 'ollama':
            raise Failure('provider_configuration', 'Ollama requires its own saved provider profile.')
        body = await request_bytes(request, 'kejtu_next.adapters.models.ollama', 'Ollama', spawn=self.spawn, progress=progress)
        names = tuple(s['function']['name'] for s in schemas(history=True, reference=request.reference, memory=request.memory,
                      notes=request.notes, notes_write=request.notes_write,hybrid=request.hybrid_memory,tasks=request.tasks,notebook_only=request.notebook_only,web_only=request.web_only,reminders=request.reminders,people=request.people,people_write=request.people_write,web=request.web,web_provider=request.web_provider,mcp=request.mcp,skills=request.skills,workspace=request.workspace)) if request.history else ('read_reference',)
        return decode_reply(body, allow_tools=request.allow_tool_calls, names=names, maximum=request.operation_limit)


def _context_rejection(raw: bytes) -> bool:
    """Decode only the explicit typed context rejection, not arbitrary HTTP 400.

    Some Ollama runtimes wrap the runner's JSON envelope in an error string.
    Unwrap at most that one known layer, with a fixed total byte bound. Counts
    corroborate input overflow; they never become request accounting anchors.
    """
    if len(raw) > 8192:
        return False
    try:
        envelope = json.loads(raw)
        error = envelope.get('error') if isinstance(envelope, dict) else None
        if isinstance(error, str):
            nested = json.loads(error)
            error = nested.get('error') if isinstance(nested, dict) else None
        if not isinstance(error, dict):
            return False
        size, capacity = error.get('n_prompt_tokens'), error.get('n_ctx')
        return (type(error.get('code')) is int and error['code'] == 400
                and error.get('type') == 'exceed_context_size_error'
                and type(size) is int and type(capacity) is int and size > capacity > 0)
    except (ValueError, UnicodeError, RecursionError):
        return False


def exchange(packet: dict, *, opener=None, progress=None) -> bytes:
    profile = Profile(**packet['profile'])
    if profile.provider != 'ollama':
        raise Failure('provider_configuration', 'Ollama requires its own saved provider profile.')
    cap = packet['response_bytes']
    payload = {'model': profile.model, 'messages': wire_messages(packet['messages'], 'ollama', model=profile.model), 'stream': False,
        'think': profile.thinking_level,
        # Preserve the reference runtime's no-loss request contract. Server
        # defaults may discard history/shift context; never request that fallback.
        # These flags alone are NOT validated runtime capability or token counts:
        # unknown servers still receive conservative admission, never blind probes.
        'truncate': False, 'shift': False,
        # Saved request policy, not exclusive server ownership or a billing timer.
        'keep_alive': -1 if profile.keep_loaded else 600,
        'options': {'num_ctx': profile.context_limit, 'num_predict': profile.generation_limit}}
    if packet.get('tools'):
        payload['tools'] = schemas(history=True, reference=packet.get('reference', False), memory=packet.get('memory', False),
                                  notes=packet.get('notes', False), notes_write=packet.get('notes_write', False),hybrid=packet.get('hybrid_memory',False),tasks=packet.get('tasks',False),notebook_only=packet.get('notebook_only',False),web_only=packet.get('web_only',False),reminders=packet.get('reminders',False),people=packet.get('people',False),people_write=packet.get('people_write',False),web=packet.get('web',False),web_provider=packet.get('web_provider','searxng'),mcp=packet.get('mcp',False),skills=packet.get('skills',False),workspace=packet.get('workspace',False)) if packet.get('history') else [tool_schema()]
    if progress is not None:
        payload['stream'] = True
    request = HttpRequest(profile.endpoint.rstrip('/') + '/api/chat',
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Accept-Encoding': 'identity'}, method='POST')
    # Explicit loopback URL only; never consult ambient proxies or follow a redirect.
    opener = opener or build_opener(ProxyHandler({}), NoRedirect())
    if packet.get('require_context_rejection'):
        # The request carries its obligation, not mutable adapter/session state.
        # Recheck immediately before POST, including continuations/summaries.
        # A changed/unknown runtime cannot inherit a previous admission decision.
        if not verified(profile, packet['timeout'], opener=opener):
            raise Failure('provider_configuration', 'The characterized no-loss runtime is unavailable; no generation was sent.')
    try:
        with opener.open(request, timeout=packet['timeout']) as response:
            if response.headers.get('Content-Encoding', 'identity') != 'identity':
                raise Failure('invalid_response', 'Compressed provider responses are not supported by this bounded adapter.')
            body = response.read(cap + 1) if progress is None else ollama_stream(response, packet, progress)
    except HTTPError as error:
        context_overflow = False
        try:
            if error.code == 400 and error.headers.get('Content-Encoding', 'identity') == 'identity':
                context_overflow = _context_rejection(error.read(8193))
        except (OSError, ValueError, TypeError, AttributeError):
            # Unreadable/malformed bodies remain generic HTTP failures. Reading
            # is bounded by the owned worker's original absolute deadline.
            pass
        finally:
            error.close()
        if context_overflow:
            raise Failure('provider_context_limit', 'Ollama explicitly rejected oversized input.') from None
        raise Failure('provider_http', 'Provider returned an HTTP error; no retry was sent.') from None
    except (URLError, TimeoutError, OSError) as error:
        raise Failure('provider_unavailable', 'Provider transport failed.') from error
    if len(body) > cap:
        raise Failure('response_size', 'Provider response exceeded the inherited byte bound.')
    return body


def worker() -> int:
    return transport_worker(exchange)


if __name__ == '__main__':
    if sys.argv[1:] != ['--worker']:
        raise SystemExit('This module is an internal transport worker.')
    raise SystemExit(worker())
