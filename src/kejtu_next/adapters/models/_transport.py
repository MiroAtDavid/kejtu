"""Shared owned-process lifetime, not a second execution or provider-policy owner."""
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import signal
import sys
import time
from urllib.request import HTTPRedirectHandler

from ...core.records import Failure, ModelProgress, Request, message_record


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


async def request_bytes(request: Request, module: str, provider: str, *, spawn, api_key=None, progress=None) -> bytes:
    remaining = request.deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError
    packet = {'profile': asdict(request.profile),
        'messages': [message_record(m) for m in request.messages],
        'response_bytes': request.response_bytes, 'timeout': remaining,
        'deadline': request.deadline}
    if request.require_context_rejection:
        packet['require_context_rejection'] = True
    if request.tools:
        packet['tools'] = True
        packet['operation_limit'] = request.operation_limit
        packet['allow_tool_calls'] = request.allow_tool_calls
        if request.history:
            packet['history'] = True
            if request.web_only:
                packet['web_only'] = True
            if request.notebook_only:
                packet['notebook_only'] = True
            packet['reference'] = request.reference
            if request.mcp:
                packet['mcp'] = True
            if request.skills:
                packet['skills'] = True
            if request.workspace:
                packet['workspace'] = True
            if request.web:
                packet['web'] = True
                packet['web_provider'] = request.web_provider
            if request.memory:
                packet['memory'] = True
                if request.hybrid_memory:
                    packet['hybrid_memory'] = True
                if request.tasks:
                    packet['tasks'] = True
                if request.people:
                    packet['people'] = True
                    packet['people_write'] = request.people_write
                if request.reminders:
                    packet['reminders'] = True
            if request.notes:
                packet['notes'] = True
                packet['notes_write'] = request.notes_write
    # Credentials never become Profile/Request values, argv, environment or receipts.
    # This pipe and the two processes contain plaintext; this is not a host sandbox.
    if api_key is not None:
        packet['api_key'] = api_key
    if progress is not None:
        packet['stream'] = True
        packet['operation_limit'] = request.operation_limit
    encoded = json.dumps(packet, ensure_ascii=False).encode('utf-8')
    process = None
    try:
        environment = {'PYTHONPATH': str(Path(__file__).resolve().parents[3]),
                       'PYTHONDONTWRITEBYTECODE': '1'}
        if sys.platform == 'win32':
            from ._windows_transport_deadline import system_environment
            environment.update(system_environment())
        async with asyncio.timeout_at(request.deadline):
            process = await spawn(sys.executable, '-B', '-S', '-P', '-m', module, '--worker',
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=environment)
            process.stdin.write(encoded)
            await process.stdin.drain()
            process.stdin.close()
            body = bytearray()
            frames = bytearray()
            wire_bytes = 0
            progress_bytes = 0
            final = None
            failed_frame = False
            while True:
                chunk = await process.stdout.read(65536 if progress is not None else min(65536, request.response_bytes + 1 - len(body)))
                if not chunk:
                    break
                if progress is not None:
                    # Text bounds and transport framing overhead are distinct.
                    # Never accumulate snapshots of the entire growing answer.
                    wire_bytes += len(chunk)
                    if wire_bytes > request.response_bytes * 64 + 65536:
                        raise Failure('response_size', 'Provider stream exceeded its transport bound.')
                    frames.extend(chunk)
                    if len(frames) > request.response_bytes * 6 + 65536:
                        raise Failure('response_size', 'Provider stream frame exceeded its byte bound.')
                    while b'\n' in frames:
                        line, _, frames = frames.partition(b'\n')
                        try:
                            event = json.loads(line)
                            if not isinstance(event, dict):
                                raise ValueError
                            if final is not None or failed_frame:
                                raise ValueError
                            if '_failure' in event and set(event) == {'_failure'}:
                                failed_frame = True
                                body = bytearray(line)
                                continue
                            kind = event.get('kind')
                            if kind == 'result' and set(event) == {'kind', 'value'}:
                                final = json.dumps(event['value'], ensure_ascii=False).encode()
                                if len(final) > request.response_bytes:
                                    raise Failure('response_size', 'Provider response exceeded the inherited byte bound.')
                            elif kind in ('text', 'reset', 'phase') and set(event) == {'kind', 'text'}:
                                text = event['text']
                                if (not isinstance(text, str) or len(text.encode()) > request.response_bytes
                                        or kind == 'reset' and text != ''
                                        or kind == 'phase' and text not in ('thinking', 'tools', 'finishing')):
                                    raise ValueError
                                if kind == 'text':
                                    progress_bytes += len(text.encode())
                                    if progress_bytes > request.response_bytes:
                                        raise Failure('response_size', 'Provider progress exceeded the inherited byte bound.')
                                progress(ModelProgress(kind, text))
                            else:
                                raise ValueError
                        except (ValueError, TypeError, UnicodeError):
                            raise Failure('invalid_response', 'Provider stream framing was invalid.') from None
                    continue
                body.extend(chunk)
                if len(body) > request.response_bytes:
                    raise Failure('response_size', 'Provider response exceeded the inherited byte bound.')
            code = await process.wait()
            if code:
                alarm = getattr(signal, 'SIGALRM', None)
                if (alarm is not None and code == -alarm
                        or sys.platform == 'win32' and code == 124):
                    raise TimeoutError
                reason = 'provider_unavailable'
                try:
                    reported = json.loads(body).get('_failure')
                    if isinstance(reported, str) and reported in {
                        'response_size', 'provider_http', 'provider_context_limit', 'provider_configuration', 'invalid_response',
                        'provider_auth', 'provider_balance', 'provider_rate_limit', 'credential_required',
                        'credential_exposure', 'provider_filtered', 'provider_interrupted', 'unsupported_tool', 'invalid_tool_call',
                        'image_input_unavailable', 'image_input_size', 'request_size'}:
                        reason = reported
                except (ValueError, AttributeError):
                    pass
                details = {
                    'image_input_unavailable': 'Image input is invalid or unavailable for this model. Use a supported PNG/JPEG with an OpenAI vision model or DeepSeek Flash.',
                    'image_input_size': 'This request contains too many images. Continue with fewer document pages.',
                    'request_size': 'The provider request is too large. Continue with fewer document pages or smaller images.',
                }
                raise Failure(reason, details.get(reason, f'{provider} request failed; no retry was sent.'))
            if progress is not None:
                if frames or final is None or failed_frame:
                    raise Failure('invalid_response', 'Provider stream ended without a complete result.')
                return final
            return bytes(body)
    except TimeoutError:
        raise
    except OSError as error:
        raise Failure('provider_unavailable', 'Local provider transport failed; no retry was sent.') from error
    finally:
        if process is not None and process.returncode is None:
            # Only our child is signalled, and repeated cancellation cannot abandon it.
            try:
                process.kill()
            except ProcessLookupError:
                pass
            reaped = asyncio.create_task(process.wait())
            cancelled = False
            while not reaped.done():
                try:
                    await asyncio.shield(reaped)
                except asyncio.CancelledError:
                    cancelled = True
            reaped.result()
            if cancelled:
                raise asyncio.CancelledError


def worker(exchange) -> int:
    guard = None
    code = 1
    try:
        # The private wire is UTF-8 bytes, independent of Windows console locale.
        # Text-only stdin remains injectable for the existing synthetic controls.
        packet = json.load(getattr(sys.stdin, 'buffer', sys.stdin))
        # Socket idle timeouts alone cannot bound a trickling provider response.
        # Inherit the original monotonic deadline even after a parent crash.
        remaining = packet['deadline'] - time.monotonic()
        if remaining <= 0:
            raise Failure('provider_unavailable', 'Transport deadline expired.')
        if sys.platform == 'win32':
            from ._windows_transport_deadline import Deadline
            guard = Deadline(packet['deadline'])
        else:
            signal.signal(signal.SIGALRM, signal.SIG_DFL)
            signal.setitimer(signal.ITIMER_REAL, remaining)
        remaining = packet['deadline'] - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        packet['timeout'] = remaining
        if packet.get('stream'):
            def emit(value):
                sys.stdout.buffer.write(json.dumps(value, ensure_ascii=False).encode() + b'\n')
                sys.stdout.buffer.flush()
            body = exchange(packet, progress=lambda event: emit({'kind': event.kind, 'text': event.text}))
            emit({'kind': 'result', 'value': json.loads(body)})
        else:
            body = exchange(packet)
            sys.stdout.buffer.write(body)
        code = 0
    except TimeoutError:
        if sys.platform == 'win32':
            code = 124
        else:
            # Retain the pre-existing Unix exchange-error mapping. Its absolute
            # watchdog still terminates with SIGALRM, independently of Python.
            print(json.dumps({'_failure': 'invalid_response'}))
    except Failure as error:
        print(json.dumps({'_failure': error.code}))
    except Exception:
        print(json.dumps({'_failure': 'invalid_response'}))
    finally:
        # Keep protection armed through success AND failure output, including
        # buffered text/binary flushes when a parent stops reading the pipe.
        try:
            sys.stdout.flush()
        except Exception:
            code = 1
        finally:
            if guard is not None:
                try:
                    guard.close()
                except Exception:
                    # Output is already final: never append a second frame or
                    # raw cleanup exception. A failed close cannot report success.
                    code = 1
    return code
