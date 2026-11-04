"""Public-client OAuth for ChatGPT plan usage, independent of Codex.

Only fixed OpenAI origins receive credentials. Identity is verified before any
account is activated. This module never logs response bodies or token values.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, ProxyHandler, build_opener

from ...core.records import Failure
from ._transport import NoRedirect

ISSUER = 'https://auth.openai.com'
RESOURCE = 'https://api.openai.com/v1'
AUTHORIZE = ISSUER + '/api/accounts/authorize'
TOKEN = ISSUER + '/api/accounts/oauth/token'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
PERMISSION = 'chatgpt.tokens.use.direct'
TERMINAL_REFRESH = frozenset(('invalid_grant', 'invalid_refresh_token', 'token_expired',
    'refresh_token_expired', 'refresh_token_invalidated', 'refresh_token_reused'))


def secret(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 16384 or any(not 33 <= ord(c) <= 126 for c in value):
        raise Failure('chatgpt_auth', 'ChatGPT returned an invalid credential. Sign in again.')
    return value


class OAuthError(Failure):
    def __init__(self, code, status):
        # Keep only protocol error identifiers, never provider prose/token echoes.
        self.oauth_code = code if code in TERMINAL_REFRESH | {'invalid_client', 'access_denied'} else 'oauth_error'
        self.status = status
        super().__init__('chatgpt_auth', f'ChatGPT sign-in could not complete (HTTP {status}, {self.oauth_code}).')


async def joined_io(function, *args):
    """Join bounded credential IO even on cancellation; no late token writes."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


class OAuthHTTP:
    def __init__(self, opener=None):
        self.opener = opener or build_opener(ProxyHandler({}), NoRedirect())
        self.discovery = None
        self.jwks = None
        self.keys_at = 0

    def request(self, url, form=None, bearer=None, empty=False):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.netloc not in ('auth.openai.com', 'api.openai.com')
                or parsed.username or parsed.password or parsed.fragment):
            raise Failure('chatgpt_auth', 'ChatGPT returned an unsupported authentication endpoint.')
        headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity'}
        data = None
        if form is not None:
            data = urlencode(form).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        if bearer is not None:
            if url != RESOURCE + '/models':
                raise Failure('chatgpt_auth', 'Unsupported ChatGPT inventory endpoint.')
            headers['Authorization'] = 'Bearer ' + secret(bearer)
        try:
            with self.opener.open(Request(url, data=data, headers=headers), timeout=15) as response:
                if response.headers.get('Content-Encoding', 'identity') != 'identity':
                    raise ValueError
                # Model catalogues include capability/instruction metadata and
                # can exceed the smaller identity/token response bound. Keep
                # the larger bound exclusive to the authenticated inventory.
                catalog = bearer is not None and url == RESOURCE + '/models'
                limit = 4 * 1024 * 1024 if catalog else 262144
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    if catalog:
                        raise Failure('chatgpt_catalog', 'The ChatGPT model list is too large to load safely. Your saved connection was kept.')
                    raise ValueError
                if empty and response.status == 200 and not raw:
                    return {}
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError
                return result
        except HTTPError as error:
            try:
                raw = error.read(8193)
                value = json.loads(raw) if len(raw) <= 8192 else {}
                code = value.get('error') if isinstance(value, dict) else None
            except (OSError, ValueError):
                code = None
            finally:
                error.close()
            raise OAuthError(code if isinstance(code, str) else '', error.code) from None
        except (OSError, ValueError, TypeError):
            raise Failure('chatgpt_unavailable', 'ChatGPT could not be reached or returned an invalid response. Your saved connection was kept.') from None

    def configuration(self):
        if self.discovery is None:
            value = self.request(ISSUER + '/.well-known/openid-configuration')
            if value.get('issuer') != ISSUER:
                raise Failure('chatgpt_auth', 'ChatGPT identity discovery did not match the expected issuer.')
            for field in ('jwks_uri', 'revocation_endpoint'):
                url = urlsplit(value.get(field, ''))
                if url.scheme != 'https' or url.netloc != 'auth.openai.com' or url.fragment or url.query:
                    raise Failure('chatgpt_auth', 'ChatGPT identity discovery returned an unsupported endpoint.')
            self.discovery = value
        return self.discovery

    def identity(self, token, client_id, nonce=None, access_token=None):
        try:
            import jwt
        except ImportError:
            raise Failure('chatgpt_setup', 'Install Kejtu’s current Python requirements to enable ChatGPT sign-in.') from None
        try:
            header = jwt.get_unverified_header(secret(token))
            if header.get('alg') != 'RS256' or not isinstance(header.get('kid'), str):
                raise ValueError
            if self.jwks is None or time.time() - self.keys_at > 3600 or not any(
                    key.get('kid') == header['kid'] for key in self.jwks.get('keys', [])):
                self.jwks = self.request(self.configuration()['jwks_uri'])
                self.keys_at = time.time()
            keys = [key for key in self.jwks['keys'] if key.get('kid') == header['kid']
                    and key.get('kty') == 'RSA' and key.get('use', 'sig') == 'sig'
                    and key.get('alg', 'RS256') == 'RS256']
            if len(keys) != 1:
                raise ValueError
            claims = jwt.decode(token, jwt.PyJWK.from_dict(keys[0], algorithm='RS256').key,
                algorithms=['RS256'], audience=client_id, issuer=ISSUER, leeway=5,
                options={'require': ['sub', 'exp', 'iat', 'aud', 'iss']})
            if not isinstance(claims['sub'], str) or not 1 <= len(claims['sub']) <= 512:
                raise ValueError
            if claims.get('azp', client_id) != client_id:
                raise ValueError
            if nonce is not None and (not isinstance(claims.get('nonce'), str)
                    or not hmac.compare_digest(claims['nonce'], nonce)):
                raise ValueError
            if 'at_hash' in claims and access_token is not None:
                digest = base64.urlsafe_b64encode(hashlib.sha256(access_token.encode()).digest()[:16]).decode().rstrip('=')
                if not hmac.compare_digest(str(claims['at_hash']), digest):
                    raise ValueError
            return claims
        except Failure:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, jwt.PyJWTError):
            raise Failure('chatgpt_identity', 'ChatGPT identity verification failed. The existing account was not changed.') from None

    def exchange(self, code, client_id, verifier, redirect, nonce):
        tokens = self.request(TOKEN, dict(grant_type='authorization_code', client_id=client_id,
            code=code, code_verifier=verifier, redirect_uri=redirect, resource=RESOURCE))
        claims = self.identity(tokens.get('id_token'), client_id, nonce, tokens.get('access_token'))
        return tokens, claims

    def refresh(self, account):
        tokens = self.request(TOKEN, dict(grant_type='refresh_token', client_id=account['client_id'],
            refresh_token=account['tokens']['refresh_token'], resource=RESOURCE))
        if tokens.get('id_token'):
            identity = self.identity(tokens['id_token'], account['client_id'], access_token=tokens.get('access_token'))
            if identity['sub'] != account['subject']:
                raise Failure('chatgpt_identity', 'ChatGPT refresh returned a different identity. Sign in again.')
        return tokens

    def revoke(self, account):
        return self.request(self.configuration()['revocation_endpoint'], dict(
            token=account['tokens']['refresh_token'], token_type_hint='refresh_token',
            client_id=account['client_id']), empty=True)
