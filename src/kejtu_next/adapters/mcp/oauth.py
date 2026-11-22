"""One browser sign-in owner for remote MCP connections.

Only an explicit Settings action opens authorization. Registration and tokens
live in the existing connection keyring; settings hold an opaque reference.
Refresh retires the old token durably before sending, as model sign-in does.
No tool action is replayed after an authentication failure.
"""
import asyncio
from copy import deepcopy
import json
import secrets
import time
from urllib.parse import urlencode, urlsplit

from ..authorization import callback_arguments, challenge, joined_io
from ...core.records import Failure
from .oauth_http import OAuthHTTP
from .secrets import credential_value


class RemoteSignIn:
    def __init__(self, host, *, http=None):
        self.host, self.http = host, http or OAuthHTTP()
        self.pending = None
        self.server = None
        self.timer = None
        self.handlers = set()
        self.stage, self.message = 'idle', ''
        self.lock = asyncio.Lock()
        self.refresh_lock = asyncio.Lock()

    def row(self, name):
        value = self.host.connections.read()
        row = value['servers'].get(name)
        if row is None or row['transport'] != 'http':
            raise Failure('mcp_sign_in', 'Select a remote connection to sign in.')
        return value, row

    def status(self, name):
        value, row = self.row(name)
        current = self.pending if self.pending and self.pending['name'] == name else None
        return dict(server=name, revision=value['revision'],
            connected=bool(row.get('oauth', {}).get('connected')),
            stage=self.stage if current else 'idle', message=self.message if current else '',
            authorization_url=current['url'] if current and self.stage == 'waiting' else None)

    def _item(self, row):
        self.host._keyring()
        return self.host.secrets.keyring(row['oauth']['secret'], 'connection · account sign-in')

    async def _read(self, row):
        raw = await self._item(row).read()
        try:
            if raw is None:
                raise ValueError
            record = json.loads(raw)
            if record['server'] != row['url'] or record['version'] != 1:
                raise ValueError
            return record
        except (ValueError, KeyError, TypeError):
            raise Failure('mcp_sign_in_required', 'This connection’s saved sign-in is missing or belongs to a different service. Connect the account again.') from None

    async def _write(self, row, record):
        raw = credential_value(json.dumps(record, separators=(',', ':'), allow_nan=False))
        item = self._item(row)
        await item.save(raw)
        if await item.read() != raw:
            raise Failure('mcp_sign_in_required', 'The saved sign-in could not be confirmed. Connect the account again.')

    def _same(self, pending):
        if time.monotonic() >= pending['expires']:
            raise Failure('mcp_sign_in', 'Sign-in expired. Start a new sign-in from Connections.')
        value, row = self.row(pending['name'])
        if row != pending['row']:
            raise Failure('mcp_conflict', 'The connection changed during sign-in. Start a new sign-in from its current settings.')
        return value, row

    async def begin(self, name, revision, before_save=lambda: None):
        async with self.lock, self.host.lock, self.refresh_lock:
            await self._cancel()
            value, row = self.row(name)
            if value['revision'] != revision:
                raise Failure('mcp_conflict', 'Connections changed. Refresh before signing in.')
            if row.get('headers') or row.get('secrets'):
                raise Failure('mcp_sign_in', 'This connection already uses manually configured headers. Add a separate connection for browser sign-in.')
            self.host._keyring()
            # Verify keyring availability before creating a remote registration.
            # Only this explicit user action may initiate account setup.
            await self.host.secrets.usable(secrets.token_hex(16))
            old = row.get('oauth', {}).get('secret')
            record = None
            if old:
                try:
                    record = await self._read(row)
                except Failure as error:
                    if error.code != 'mcp_sign_in_required':
                        raise
                    # Restored settings intentionally have no portable keyring data.
                    # Explicit reconnection can register this computer again.
            port = urlsplit(record['redirect']).port if record else 0
            try:
                self.server = await asyncio.start_server(self._callback, '127.0.0.1', port, limit=12288)
            except OSError:
                raise Failure('mcp_sign_in', 'The local sign-in return address is busy. Close the other sign-in and try again.') from None
            redirect = f'http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/auth/callback'
            try:
                if record is None:
                    metadata = await joined_io(self.http.discover, row['url'])
                    client = await joined_io(self.http.register, metadata, redirect)
                    record = dict(version=1, server=row['url'], metadata=metadata,
                                  client=client, redirect=redirect, tokens=None)
                    # Keep this registration across cancellation, reopen and reauthorization.
                    # It is saved before the browser is asked to authorize it.
                    stored = await self.host.secrets.store(name, {'account sign-in': json.dumps(record, separators=(',', ':'))})
                    identity = stored['account sign-in']
                    try:
                        before_save()
                        current, original = self.row(name)
                        if current['revision'] != revision or original != row:
                            raise Failure('mcp_conflict', 'Connections changed during sign-in setup.')
                        row = dict(row, oauth=dict(secret=identity, connected=False), enabled=False)
                        current['servers'][name] = row
                        self.host.connections.save(current, revision)
                    except BaseException:
                        await self.host._forget_unreferenced([identity], quiet=True)
                        raise
                    if old:
                        await self.host._forget_unreferenced([old])
                state, verifier = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
                params = dict(response_type='code', client_id=record['client'], redirect_uri=redirect,
                    state=state, code_challenge=challenge(verifier), code_challenge_method='S256',
                    resource=record['metadata']['resource'])
                address = record['metadata']['authorization_endpoint']
                self.pending = dict(name=name, row=deepcopy(row), record=record, state=state, verifier=verifier,
                    redirect=redirect, expires=time.monotonic()+600,
                    url=address + ('&' if '?' in address else '?') + urlencode(params))
                self.stage, self.message = 'waiting', ''
                self.timer = asyncio.create_task(self._expire())
                return self.status(name)
            except (ValueError, TypeError, KeyError):
                await self._cancel()
                raise Failure('mcp_sign_in', 'The service does not provide supported sign-in metadata. Nothing was authorized.') from None
            except BaseException:
                await self._cancel()
                raise

    async def _callback(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        pending = self.pending
        code, message = 400, 'This sign-in link is invalid or expired. Return to Kejtu.'
        try:
            args = await callback_arguments(reader, pending)
            if self.pending is not pending or self.stage != 'waiting':
                raise ValueError
            self._same(pending)
            # Consume exactly once before the first remote request.
            self.stage = 'exchanging'
            if 'error' in args:
                raise Failure('mcp_sign_in', 'Sign-in was declined. You can try again from Connections.')
            metadata = pending['record']['metadata']
            if (('iss' in args or metadata.get('issuer_response_required'))
                    and args.get('iss', [None])[0] != metadata['issuer']):
                raise Failure('mcp_sign_in', 'The sign-in response came from a different provider.')
            authorization_code = credential_value(args.get('code', [None])[0])
            tokens = await joined_io(self.http.exchange, pending['record'], authorization_code, pending['verifier'])
            self._same(pending)
            if self.pending is pending:
                pending['candidate'] = dict(pending['record'], tokens=tokens)
                self.stage = 'ready'
                code, message = 200, 'Sign-in verified. Return to Kejtu to finish connecting.'
        except Failure as error:
            if self.pending is pending:
                self.stage, self.message = 'error', error.detail
                message = 'Sign-in could not complete. Return to Kejtu for details.'
        except (ValueError, TypeError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            pass
        finally:
            try:
                body = message.encode()
                writer.write(f'HTTP/1.1 {code} Result\r\nContent-Type: text/plain; charset=utf-8\r\nCache-Control: no-store\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode() + body)
                await writer.drain()
            except (OSError, ConnectionError):
                pass
            writer.close()
            self.handlers.discard(task)

    async def finish(self, name, before_save=lambda: None):
        async with self.lock, self.host.lock, self.refresh_lock:
            pending = self.pending
            if not pending or pending['name'] != name or self.stage != 'ready':
                raise Failure('mcp_sign_in', 'Complete sign-in in the browser first.')
            self._same(pending)
            stored = await self.host.secrets.store(name, {'account sign-in': json.dumps(pending['candidate'], separators=(',', ':'))})
            identity = stored['account sign-in']
            old = pending['row']['oauth']['secret']
            try:
                before_save()
                value, row = self._same(pending)
                # A possibly different account must get a fresh tool review.
                value['servers'][name] = dict(row, oauth=dict(secret=identity, connected=True),
                    enabled=False, allowed_tools=[], repeatable_tools=[])
                self.host.connections.save(value, value['revision'])
                self.host.inspections.pop(name, None)
                self.host.catalogues.pop(name, None)
            except BaseException:
                await self.host._forget_unreferenced([identity, old], quiet=True)
                await self._cancel()
                raise
            await self._cancel()
            await self.host._forget_unreferenced([old])
            return self.status(name)

    async def disconnect(self, name, revision, before_save=lambda: None):
        async with self.lock, self.host.lock, self.refresh_lock:
            await self._cancel()
            value, row = self.row(name)
            if value['revision'] != revision:
                raise Failure('mcp_conflict', 'Connections changed. Refresh before disconnecting.')
            if not row.get('oauth'):
                return self.status(name)
            identity = row['oauth']['secret']
            await self._item(row).read()  # locked keyring refuses before any saved change
            before_save()
            value, current = self.row(name)
            if value['revision'] != revision or row != current:
                raise Failure('mcp_conflict', 'Connections changed while disconnecting.')
            row.pop('oauth')
            row.update(enabled=False, allowed_tools=[], repeatable_tools=[])
            value['servers'][name] = row
            self.host.connections.save(value, revision)
            self.host.inspections.pop(name, None)
            self.host.catalogues.pop(name, None)
            await self.host._forget_unreferenced([identity])
            return self.status(name)

    async def authorize(self, snapshot):
        """Resolve/refresh before dispatch. Never refresh in response to a failed tool call."""
        async with self.refresh_lock:
            def current():
                rows = self.host.connections.read()['servers'].values()
                if not any(r.get('oauth') == snapshot['oauth'] and r.get('url') == snapshot['url'] for r in rows):
                    raise Failure('mcp_sign_in_required', 'This connection’s sign-in changed. No tool request was sent.')
            current()
            if not snapshot['oauth']['connected']:
                raise Failure('mcp_sign_in_required', 'Connect this account in Settings → Connections before using its tools.')
            record = await self._read(snapshot)
            tokens = record.get('tokens')
            if not tokens:
                raise Failure('mcp_sign_in_required', 'Sign in again in Settings → Connections. An earlier refresh could not be confirmed and was not repeated.')
            if tokens['refresh_at'] is not None and time.time() >= tokens['refresh_at']:
                if not tokens.get('refresh_token'):
                    raise Failure('mcp_sign_in_required', 'This sign-in has expired. Connect the account again.')
                current()
                retired = dict(record, tokens=None)
                await self._write(snapshot, retired)
                current()
                replacement = await joined_io(self.http.refresh, record)
                current()
                record = dict(record, tokens=replacement)
                await self._write(snapshot, record)
                tokens = replacement
            current()
            return tokens['access_token']

    async def _expire(self):
        await asyncio.sleep(600)
        self.timer = None
        await self.cancel()

    async def cancel(self):
        async with self.lock:
            await self._cancel()

    async def _cancel(self):
        self.pending = None
        self.stage, self.message = 'idle', ''
        if self.timer:
            self.timer.cancel()
            self.timer = None
        server, self.server = self.server, None
        if server:
            server.close()
        tasks = list(self.handlers - {asyncio.current_task()})
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if server:
            await server.wait_closed()
