"""Private ChatGPT registrations and short-lived loopback sign-in attempts.

The browser callback prepares a candidate only. ModelControls publishes it after
the usual idle check, so signing in cannot change an admitted request's account.
"""
import asyncio
import base64
from copy import deepcopy
import hashlib
import hmac
import json
import secrets
import math
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid

from ...core.records import Failure
from ...ports.private_records import PrivateRecord
from .chatgpt_oauth import (AUTHORIZE, RESOURCE, SCOPES, PERMISSION, TERMINAL_REFRESH,
    OAuthHTTP, OAuthError, joined_io, secret)

MAX_ACCOUNTS = 16


def client_id(value):
    if (not isinstance(value, str) or not value or value == 'dynamic_agent_client' or len(value) > 256
            or any(not (c.isascii() and (c.isalnum() or c in '_-')) for c in value)):
        raise Failure('chatgpt_auth', 'ChatGPT registration did not return a valid issued client ID.')
    return value


def token_set(value, previous=None):
    previous = previous or {}
    scope = value.get('scope', previous.get('scope', ''))
    granted = isinstance(scope, str) and PERMISSION in scope.split()
    if value.get('token_type', '' if granted else 'Bearer').lower() != 'bearer':
        raise Failure('chatgpt_auth', 'ChatGPT did not grant a bearer session.')
    seconds = value.get('expires_in', 3600 if not granted else None)
    if type(seconds) is not int or not 1 <= seconds <= 86400:
        raise Failure('chatgpt_auth', 'ChatGPT returned an invalid session expiry.')
    if not isinstance(scope, str) or len(scope) > 4096:
        raise Failure('chatgpt_auth', 'ChatGPT returned invalid permissions.')
    def optional_token(name):
        item = value.get(name)
        return secret(item) if granted or item is not None else None
    result = dict(access_token=optional_token('access_token'),
        refresh_token=optional_token('refresh_token'), scope=scope,
        id_token=secret(value.get('id_token', previous.get('id_token'))),
        expires_at=time.time() + seconds)
    earliest = value.get('earliest_refresh_at', 0)
    if not isinstance(earliest, (int, float)) or not 0 <= earliest <= result['expires_at']:
        raise Failure('chatgpt_auth', 'ChatGPT returned an invalid refresh time.')
    result['earliest_refresh_at'] = earliest
    return result


class AccountStore:
    def __init__(self, record: PrivateRecord, state_id):
        self.record = record
        self.path = record.path
        self.state_id = state_id
        self.missing = False

    def locked(self):
        return self.record.locked()

    def read(self):
        try:
            raw = self.record.read()
            self.missing = raw is None
            if self.missing:
                return dict(version=1, state_id=self.state_id, host='urn:uuid:' + str(uuid.uuid4()),
                            active=None, accounts=[], welcomed=False)
            value = json.loads(raw)
            if (value.get('version') != 1 or value.get('state_id') != self.state_id
                    or not isinstance(value.get('host'), str) or not value['host'].startswith('urn:uuid:')
                    or len(value['host']) != 45 or type(value.get('welcomed')) is not bool
                    or not isinstance(value.get('accounts'), list) or len(value['accounts']) > MAX_ACCOUNTS):
                raise ValueError
            ids = set()
            for account in value['accounts']:
                ident = client_id(account['client_id'])
                if ident in ids or not isinstance(account['subject'], str) or not account['subject']:
                    raise ValueError
                ids.add(ident)
                if not isinstance(account['label'], str) or len(account['label'].encode()) > 512:
                    raise ValueError
                pending = account.get('refresh_pending')
                if pending is not None:
                    if (not isinstance(pending, dict) or set(pending) != {'attempt_id', 'started_at'}
                            or not isinstance(pending['attempt_id'], str) or len(pending['attempt_id']) != 32
                            or any(c not in '0123456789abcdef' for c in pending['attempt_id'])
                            or type(pending['started_at']) not in (int, float)
                            or not math.isfinite(pending['started_at']) or pending['started_at'] < 0
                            or account.get('tokens') is not None):
                        raise ValueError
                tokens = account.get('tokens')
                if tokens is not None:
                    for field in ('access_token', 'refresh_token', 'id_token'):
                        if field == 'id_token' or tokens[field] is not None:
                            secret(tokens[field])
                    if (not isinstance(tokens['scope'], str) or len(tokens['scope']) > 4096
                            or type(tokens['expires_at']) not in (int, float)
                            or type(tokens['earliest_refresh_at']) not in (int, float)):
                        raise ValueError
            if value['active'] is not None and value['active'] not in ids:
                raise ValueError
            return value
        except (OSError, ValueError, TypeError, KeyError, Failure, UnicodeError, AttributeError):
            raise Failure('chatgpt_storage', 'Saved ChatGPT connections could not be read safely. No credentials were replaced.') from None

    def write(self, value, *, published=lambda: None):
        raw = json.dumps(value, ensure_ascii=True).encode()
        if len(raw) > 524288:
            raise Failure('chatgpt_storage', 'Saved ChatGPT connections exceed their storage limit.')
        self.record.replace(raw, published=published)


class ChatGPTAccounts:
    def __init__(self, record: PrivateRecord, state_id, *, http=None):
        self.store = AccountStore(record, state_id)
        self.http = http or OAuthHTTP()
        self.data = None
        self.storage_error = ''
        self.error = ''
        self.stage = 'idle'
        self.pending = None
        self.candidate = None
        self.server = None
        self.timer = None
        self.handlers = set()
        self.lock = asyncio.Lock()
        self.retry_client = None
        try:
            with self.store.locked():
                data = self.store.read()
                # A new host cannot authorize browser sign-in before storage ack.
                if self.store.missing:
                    self._write(data)
                else:
                    self.data = data
        except (OSError, Failure):
            self.storage_error = 'Saved ChatGPT connections could not be confirmed. Reopen after checking private storage; no sign-in or refresh was sent.'

    def _ready(self):
        if not self.data or self.storage_error:
            raise Failure('chatgpt_storage', self.storage_error or 'ChatGPT private storage is unavailable.')

    def _write(self, data):
        try:
            self.store.write(data, published=lambda: setattr(self, 'data', data))
        except (OSError, Failure):
            # Keep visibility authority, but do not use credentials after a late
            # acknowledgement failure. cancel/status cannot reset this guard.
            self.storage_error = 'ChatGPT storage could not be confirmed. Reopen to inspect saved state; no operation was retried.'
            raise Failure('chatgpt_storage', self.storage_error) from None

    @staticmethod
    def _find(data, ident):
        result = next((a for a in data['accounts'] if a['client_id'] == ident), None)
        if result is None:
            raise Failure('credential_required', 'Continue with ChatGPT in Settings → Models before sending.')
        return result

    def account(self, ident=None):
        self._ready()
        return self._find(self.data, ident or self.data['active'])

    def require(self):
        account = self.account()
        if not account.get('tokens'):
            raise Failure('credential_required', 'Sign in to ChatGPT again in Settings → Models. A refresh with an unknown outcome is never repeated.')
        if PERMISSION not in account['tokens']['scope'].split():
            raise Failure('chatgpt_permission', 'Enable ChatGPT plan usage in Settings → Models before sending.')
        return account

    def status(self):
        data = self.data or dict(accounts=[], active=None, welcomed=True)
        def state(account):
            if not account.get('tokens'):
                return 'signed_out'
            return 'connected' if PERMISSION in account['tokens']['scope'].split() else 'permission_needed'
        return dict(accounts=[dict(id=a['client_id'], label=a['label'], status=state(a)) for a in data['accounts']],
            active=data['active'], stage=self.stage, message=self.storage_error or self.error or
                ('ChatGPT refresh could not be confirmed. Sign in again; the old token will not be reused.'
                 if any(a.get('refresh_pending') for a in data['accounts']) else ''),
            authorization_url=self.pending['url'] if self.pending and self.stage == 'waiting' else None,
            welcome=not data['welcomed'] and any(state(a) == 'connected' for a in data['accounts']))

    async def begin(self, ident=None):
        retry = self.retry_client if ident is None else None
        await self.cancel()
        self._ready()
        account = self.account(ident) if ident else None
        if not account and len(self.data['accounts']) >= MAX_ACCOUNTS:
            raise Failure('chatgpt_auth', 'The saved ChatGPT account limit has been reached.')
        state, nonce, verifier = (secrets.token_urlsafe(48) for _ in range(3))
        try:
            self.server = await asyncio.start_server(self._callback, '127.0.0.1', 0, limit=12288)
        except OSError:
            raise Failure('chatgpt_auth', 'Kejtu could not open its local sign-in callback. Check local firewall permissions and try again.') from None
        port = self.server.sockets[0].getsockname()[1]
        redirect = f'http://127.0.0.1:{port}/auth/callback'
        params = dict(client_id=account['client_id'] if account else retry or 'dynamic_agent_client',
            ext_agent_host_id=self.data['host'], response_type='code', redirect_uri=redirect,
            scope=SCOPES, resource=RESOURCE, state=state, nonce=nonce, code_challenge_method='S256',
            code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='))
        if not account and not retry:
            params['agent_name_hint'] = 'Kejtu'
        elif account and (not account.get('tokens') or PERMISSION not in account['tokens']['scope'].split()):
            params['prompt'] = 'consent'
        # ID-token hints are optional. Omit them so neither native UI nor browser
        # launcher arguments ever contain a saved credential.
        self.pending = dict(state=state, nonce=nonce, verifier=verifier, redirect=redirect,
            client=account['client_id'] if account else retry, subject=account['subject'] if account else None,
            url=AUTHORIZE + '?' + urlencode(params), expires=time.monotonic()+600)
        self.stage, self.error = 'waiting', ''
        self.timer = asyncio.create_task(self._expire())
        return self.status()

    async def _expire(self):
        await asyncio.sleep(600)
        self.timer = None
        await self.cancel()
        self.stage, self.error = 'error', 'ChatGPT sign-in expired. Continue with ChatGPT to try again.'

    async def _callback(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        status, message = 400, 'This sign-in link is invalid or expired. Return to Kejtu.'
        pending = self.pending
        try:
            raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
            if len(raw) > 12288:
                raise ValueError
            lines = raw.decode('ascii').split('\r\n')
            method, target, version = lines[0].split(' ')
            headers = dict(line.split(':', 1) for line in lines[1:] if line)
            host = next((v.strip() for k,v in headers.items() if k.lower() == 'host'), '')
            url = urlsplit(target)
            if (method != 'GET' or version not in ('HTTP/1.0', 'HTTP/1.1') or url.path != '/auth/callback'
                    or url.netloc or url.fragment or not pending or self.stage != 'waiting'
                    or host != urlsplit(pending['redirect']).netloc or time.monotonic() >= pending['expires']):
                raise ValueError
            args = parse_qs(url.query, keep_blank_values=True, max_num_fields=16)
            if any(len(v) != 1 for v in args.values()) or not hmac.compare_digest(args.get('state', [''])[0], pending['state']):
                raise ValueError
            # Consume the attempt once, before awaiting the token endpoint.
            self.stage = 'exchanging'
            if 'error' in args:
                raise Failure('chatgpt_auth', 'ChatGPT sign-in was declined. Your existing connection was kept.')
            issued = client_id(args.get('client_id', [pending['client']])[0])
            if pending['client'] and issued != pending['client']:
                raise Failure('chatgpt_identity', 'ChatGPT returned a different registration. The existing connection was kept.')
            code = secret(args.get('code', [None])[0])
            try:
                tokens, claims = await joined_io(self.http.exchange, code, issued, pending['verifier'], pending['redirect'], pending['nonce'])
            except OAuthError as error:
                if error.oauth_code == 'invalid_grant':
                    # A used/expired code is never replayed. Retain the newly
                    # issued registration for a fresh authorization attempt.
                    self.retry_client = issued
                    action = 'Add account' if pending['subject'] is None and self.data['active'] else 'Continue with ChatGPT'
                    raise Failure('chatgpt_auth',
                        f'ChatGPT could not complete this sign-in. Keep Kejtu open, choose {action} '
                        'and finish sign-in in the new browser tab.') from None
                raise
            if pending['subject'] and claims['sub'] != pending['subject']:
                raise Failure('chatgpt_identity', 'ChatGPT returned a different account. The existing connection was kept.')
            saved = token_set(tokens)
            label = claims.get('email') or claims.get('name') or 'ChatGPT account'
            if not isinstance(label, str):
                label = 'ChatGPT account'
            label = label.encode()[:400].decode(errors='ignore') + ' · ' + issued[-8:]
            if self.pending is pending:
                self.candidate = dict(client_id=issued, subject=claims['sub'], label=label, tokens=saved)
                self.stage = 'ready'
                status, message = 200, 'Sign-in verified. Return to Kejtu to finish connecting your account.'
        except Failure as error:
            if self.pending is pending:
                self.stage, self.error = 'error', error.detail
                message = 'Sign-in could not complete. Return to Kejtu for details.'
        except (ValueError, TypeError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            pass
        finally:
            if self.stage != 'waiting' and self.server:
                self.server.close()
            if self.stage != 'waiting' and self.timer:
                self.timer.cancel()
                self.timer = None
            try:
                body = ('<!doctype html><meta charset="utf-8"><title>Kejtu · ChatGPT</title>'
                        '<h1>Kejtu</h1><p>' + message + '</p>').encode()
                writer.write(f'HTTP/1.1 {status} Result\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nContent-Security-Policy: default-src \'none\'\r\nConnection: close\r\n\r\n'.encode()+body)
                await writer.drain()
            except (OSError, RuntimeError):
                pass
            writer.close()
            self.handlers.discard(task)

    async def finish(self):
        self._ready()
        if self.stage != 'ready' or self.candidate is None:
            raise Failure('chatgpt_auth', 'Complete ChatGPT sign-in in your browser first.')
        with self.store.locked():
            data = self.store.read()
            candidate = self.candidate
            previous = next((a for a in data['accounts'] if a['client_id'] == candidate['client_id']), None)
            if previous and previous['subject'] != candidate['subject']:
                raise Failure('chatgpt_identity', 'Saved ChatGPT identity changed. No connection was replaced.')
            data['accounts'] = [a for a in data['accounts'] if a['client_id'] != candidate['client_id']] + [candidate]
            if len(data['accounts']) > MAX_ACCOUNTS:
                raise Failure('chatgpt_auth', 'The saved ChatGPT account limit has been reached.')
            data['active'] = candidate['client_id']
            self._write(data)
        await self.cancel()
        return self.status()

    async def cancel(self):
        self.retry_client = None
        self.pending, self.candidate, self.stage, self.error = None, None, 'idle', ''
        if self.timer:
            self.timer.cancel()
            self.timer = None
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        handlers = list(self.handlers - {asyncio.current_task()})
        for task in handlers:
            task.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)
        return self.status()

    async def select(self, ident):
        await self.cancel()
        self._ready()
        with self.store.locked():
            data = self.store.read()
            self._find(data, ident)
            data['active'] = ident
            self._write(data)
        return self.status()

    def welcome_seen(self):
        self._ready()
        with self.store.locked():
            data = self.store.read()
            data['welcomed'] = True
            self._write(data)
        return self.status()

    async def sign_out(self, ident):
        await self.cancel()
        self._ready()
        async with self.lock:
            self._ready()
            with self.store.locked():
                data = self.store.read()
                account = self._find(data, ident)
                if account.get('tokens'):
                    try:
                        if account['tokens'].get('refresh_token'):
                            await joined_io(self.http.revoke, account)
                    except Failure:
                        self.error = 'Signed out locally. Remote revocation was not confirmed; disconnect Kejtu in ChatGPT settings if needed.'
                    account['tokens'] = None
                    self._write(data)
        return self.status()

    async def access_token(self):
        self._ready()
        async with self.lock:
            with self.store.locked():
                self.data = self.store.read()
                account = self.require()
                tokens = account['tokens']
                now = time.time()
                if now >= tokens['expires_at'] - 60 and now >= tokens['earliest_refresh_at']:
                    previous = deepcopy(account)
                    data = deepcopy(self.data)
                    pending = self._find(data, account['client_id'])
                    # Retire the old refresh token durably BEFORE remote rotation.
                    # Legacy readers see signed_out too; no automatic crash retry.
                    pending['tokens'] = None
                    pending['refresh_pending'] = dict(attempt_id=uuid.uuid4().hex, started_at=now)
                    self._write(data)
                    try:
                        replacement = await joined_io(self.http.refresh, previous)
                    except OAuthError as error:
                        if error.oauth_code in TERMINAL_REFRESH:
                            raise Failure('credential_required', 'ChatGPT sign-in has expired or was revoked. Sign in again in Settings → Models.') from None
                        raise
                    # Failure/cancellation keeps the signed-out registration. A
                    # joined but discarded response is not permission to replay.
                    candidate = deepcopy(data)
                    renewed = self._find(candidate, account['client_id'])
                    renewed['tokens'] = token_set(replacement, tokens)
                    renewed.pop('refresh_pending')
                    self._write(candidate)
                    account = self.require()
                return account['tokens']['access_token']

    async def catalog(self):
        token = await self.access_token()
        value = await joined_io(self.http.request, RESOURCE + '/models', None, token)
        try:
            models = []
            seen = set()
            for row in value['models']:
                if row.get('visibility') != 'list':
                    continue
                slug, label = row['slug'], row['display_name']
                if any(not isinstance(v, str) or not v or len(v.encode()) > 1024 or token in v for v in (slug, label)):
                    raise ValueError
                if slug not in seen:
                    models.append(dict(model=slug, label=label))
                    seen.add(slug)
            return models[:50], len(models) > 50
        except (ValueError, TypeError, KeyError, AttributeError):
            raise Failure('chatgpt_catalog', 'ChatGPT returned an invalid model list. No generation was requested.') from None
