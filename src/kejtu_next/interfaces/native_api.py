"""Bounded local HTTP/JSON interface; application and SQLite remain the authorities."""
from __future__ import annotations

import asyncio
import json
import secrets

from ..core.records import Failure
from .native_wire import BOUNDS, PROTOCOL, validate_request, validate_response


def wire_float(raw):
    # JSON integers are mathematical values, not a lexical spelling: JS cannot
    # distinguish 1 from 1.0. Normalize integral spellings for application APIs.
    value = float(raw)
    return int(value) if value.is_integer() else value


def response(request_id: str, *, data=None, error=None) -> bytes:
    value = {'protocol': PROTOCOL, 'request_id': request_id, 'kind': 'result', 'data': data}
    if error is not None:
        value = {'protocol': PROTOCOL, 'request_id': request_id, 'kind': 'error', 'error': error}
    raw = json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode()
    if len(raw) > BOUNDS['response_bytes']:
        fallback = {'protocol': PROTOCOL, 'request_id': request_id, 'kind': 'error',
                    'error': {'code': 'response_size', 'detail': 'Native response exceeded its fixed byte bound.'}}
        raw = json.dumps(fallback, separators=(',', ':')).encode()
    return raw


class NativeServer:
    def __init__(self, application, *, token=None):
        self.application = application
        self.token = token or secrets.token_hex(32)
        self.server = None
        self.handlers = set()
        self.closing = False

    async def start(self):
        self.server = await asyncio.start_server(self._handle, '127.0.0.1', 0, limit=8192)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        if self.closing or len(self.handlers) >= BOUNDS['maximum_concurrent_requests']:
            body = response('0' * 32, error={'code': 'busy', 'detail': 'Native request capacity is occupied.'})
            writer.write(f'HTTP/1.1 503 Busy\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode() + body)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            return
        task = asyncio.current_task()
        self.handlers.add(task)
        try:
            await self._exchange(reader, writer)
        finally:
            self.handlers.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _exchange(self, reader, writer):
        request_id = '0' * 32
        status, body = 400, response(request_id, error={'code': 'malformed_request', 'detail': 'Malformed native request.'})
        try:
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
            if len(header) > 8192:
                raise Failure('request_size', 'Native request headers are too large.')
            lines = header[:-4].split(b'\r\n')
            if lines[0] != b'POST /native/v1 HTTP/1.1':
                raise Failure('invalid_transport', 'Only the fixed native endpoint is available.')
            fields = {}
            for line in lines[1:]:
                name, separator, value = line.partition(b':')
                if not separator:
                    raise Failure('malformed_request', 'Malformed native headers.')
                key = name.decode('ascii').lower()
                if key in fields:
                    raise Failure('malformed_request', 'Duplicate native headers are not accepted.')
                fields[key] = value.strip().decode('ascii')
            if 'origin' in fields or 'transfer-encoding' in fields or 'content-encoding' in fields:
                raise Failure('invalid_transport', 'Browser origins and encoded request bodies are not accepted.')
            if fields.get('authorization') != 'Bearer ' + self.token:
                raise Failure('unauthorized', 'Native session authorization failed.')
            if fields.get('content-type') != 'application/json':
                raise Failure('invalid_transport', 'Native requests require application/json.')
            try:
                length = int(fields.get('content-length', '-1'))
            except ValueError:
                raise Failure('malformed_request', 'Content length is invalid.') from None
            ceiling = max(BOUNDS['control_request_bytes'], 6 * 4 * self.application.ingress_context_limit + 4096)
            if not 0 <= length <= ceiling:
                raise Failure('request_size', 'Native request exceeds its launch-pinned byte bound.')
            raw = await asyncio.wait_for(reader.readexactly(length), 5)
            value = validate_request(json.loads(raw, parse_float=wire_float))
            request_id = value['request_id']
            if value['operation'] != 'turn.submit' and len(raw) > BOUNDS['control_request_bytes']:
                raise Failure('request_size', 'Control request exceeds its encoded byte bound.')
            data = await self.application.dispatch(value['operation'], value['arguments'])
            try:
                validate_response(value['operation'], {'protocol': PROTOCOL, 'request_id': request_id,
                    'kind': 'result', 'data': data})
            except ValueError:
                raise Failure('invalid_response', 'Application result failed the generated contract.') from None
            body, status = response(request_id, data=data), 200
        except Failure as error:
            body = response(request_id, error={'code': error.code, 'detail': error.detail})
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError, asyncio.TimeoutError):
            pass
        except Exception:
            body = response(request_id, error={'code': 'internal_error', 'detail': 'Native request could not be completed.'})
        reason = 'OK' if status == 200 else 'Bad Request'
        writer.write(f'HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode() + body)
        try:
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def close(self):
        self.closing = True
        if self.server is not None:
            self.server.close()
        handlers = list(self.handlers)
        for task in handlers:
            task.cancel()
        if handlers:
            await asyncio.gather(*handlers, return_exceptions=True)
        # On current Python, wait_closed also waits for accepted connections;
        # their handlers must be cancelled before joining the listener.
        if self.server is not None:
            await self.server.wait_closed()
