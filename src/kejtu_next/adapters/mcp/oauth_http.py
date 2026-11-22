"""Bounded MCP OAuth discovery and code exchange. No tool calls or retries.

Discovery supplies endpoints; subsequent credential requests use only that saved
registration. Redirects, ambient proxies and cookies are never used. The user
starts authorization explicitly in Connections, never from a model/tool result.
"""
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from ...core.records import Failure
from .secrets import credential_value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ResponseError(Failure):
    def __init__(self, status):
        self.status = status
        super().__init__('mcp_sign_in', f'The service could not complete sign-in (HTTP {status}). No request was retried.')


def endpoint(value, *, loopback=False):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or any(ord(c) <= 32 for c in value) or '\\' in value:
        raise ValueError('Invalid authorization address')
    url = urlsplit(value)
    _ = url.port
    if (not url.hostname or url.username is not None or url.password is not None or url.fragment
            or not (url.scheme == 'https' or loopback and url.scheme == 'http' and url.hostname in ('127.0.0.1', 'localhost', '::1'))):
        raise ValueError('Authorization needs HTTPS')
    return value


def token_set(value, previous=None):
    if not isinstance(value, dict) or str(value.get('token_type', '')).lower() != 'bearer':
        raise Failure('mcp_sign_in', 'The service returned an unsupported sign-in response.')
    try:
        access = credential_value(value.get('access_token'))
        if any(not 33 <= ord(c) <= 126 for c in access):
            raise ValueError
        refresh = value.get('refresh_token', (previous or {}).get('refresh_token'))
        if refresh is not None:
            credential_value(refresh)
        lifetime = value.get('expires_in')
        if lifetime is not None and (type(lifetime) is not int or not 1 <= lifetime <= 315360000):
            raise ValueError
        # Short-lived tokens still have a usable interval; never refresh in a loop.
        refresh_at = time.time() + lifetime - min(60, lifetime / 10) if lifetime is not None else None
        return dict(access_token=access, refresh_token=refresh, refresh_at=refresh_at)
    except (ValueError, Failure):
        raise Failure('mcp_sign_in', 'The service returned invalid sign-in credentials.') from None


class OAuthHTTP:
    def __init__(self, *, loopback=False, opener=None):
        self.loopback = loopback
        self.opener = opener or build_opener(ProxyHandler({}), NoRedirect())

    def request(self, url, body=None, *, form=False):
        endpoint(url, loopback=self.loopback)
        headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity'}
        data = None
        if body is not None:
            data = (urlencode(body) if form else json.dumps(body, allow_nan=False)).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        try:
            with self.opener.open(Request(url, data=data, headers=headers), timeout=15) as response:
                if response.headers.get('Content-Encoding', 'identity') != 'identity':
                    raise ValueError
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except HTTPError as error:
            status = error.code
            error.close()
            raise ResponseError(status) from None
        except (OSError, URLError, ValueError, UnicodeError):
            raise Failure('mcp_sign_in', 'The service’s sign-in response could not be confirmed. No request was retried.') from None

    def discover(self, server):
        endpoint(server, loopback=self.loopback)
        url = urlsplit(server)
        origin = urlunsplit((url.scheme, url.netloc, '', '', ''))
        issuer, resource = origin, server
        # RFC 9728 path-specific metadata, then the origin document. A legacy
        # server without either may expose authorization metadata at its origin.
        paths = list(dict.fromkeys(['/.well-known/oauth-protected-resource' + url.path.rstrip('/'),
                                   '/.well-known/oauth-protected-resource']))
        for path in paths:
            try:
                protected = self.request(origin + path)
            except ResponseError as error:
                if error.status == 404:
                    continue
                raise
            resource = endpoint(protected.get('resource'), loopback=self.loopback)
            if not (server.rstrip('/') == resource.rstrip('/') or server.startswith(resource.rstrip('/') + '/')):
                raise Failure('mcp_sign_in', 'The service’s sign-in metadata identifies a different connection.')
            issuers = protected.get('authorization_servers')
            if not isinstance(issuers, list) or len(issuers) != 1:
                raise Failure('mcp_sign_in', 'This service does not identify one supported sign-in provider.')
            issuer = endpoint(issuers[0], loopback=self.loopback)
            break
        parsed = urlsplit(issuer)
        if parsed.query:
            raise Failure('mcp_sign_in', 'The service returned an invalid sign-in provider.')
        base = urlunsplit((parsed.scheme, parsed.netloc, '', '', ''))
        metadata = self.request(base + '/.well-known/oauth-authorization-server' + parsed.path.rstrip('/'))
        if metadata.get('issuer', '').rstrip('/') != issuer.rstrip('/') or 'S256' not in metadata.get('code_challenge_methods_supported', []):
            raise Failure('mcp_sign_in', 'The service does not provide the required verified browser sign-in flow.')
        if 'none' not in metadata.get('token_endpoint_auth_methods_supported', ['client_secret_basic']):
            raise Failure('mcp_sign_in', 'This service requires an app registration with a secret, which this sign-in flow does not support yet.')
        result = dict(issuer=issuer, resource=resource)
        for name in ('authorization_endpoint', 'token_endpoint', 'registration_endpoint'):
            result[name] = endpoint(metadata.get(name), loopback=self.loopback)
        reserved = {'response_type', 'client_id', 'redirect_uri', 'state', 'scope', 'resource',
                    'code_challenge', 'code_challenge_method'}
        if reserved & parse_qs(urlsplit(result['authorization_endpoint']).query).keys():
            raise Failure('mcp_sign_in', 'The service returned conflicting sign-in parameters.')
        result['issuer_response_required'] = metadata.get('authorization_response_iss_parameter_supported') is True
        return result

    def register(self, metadata, redirect):
        value = self.request(metadata['registration_endpoint'], dict(client_name='Kejtu',
            redirect_uris=[redirect], grant_types=['authorization_code', 'refresh_token'],
            response_types=['code'], token_endpoint_auth_method='none'))
        ident = value.get('client_id')
        if (not isinstance(ident, str) or not 1 <= len(ident) <= 1024 or any(ord(c) < 33 for c in ident)
                or value.get('token_endpoint_auth_method', 'none') != 'none'
                or value.get('client_secret') or value.get('redirect_uris') != [redirect]):
            raise Failure('mcp_sign_in', 'The service returned an unsupported app registration.')
        return ident

    def exchange(self, record, code, verifier):
        return token_set(self.request(record['metadata']['token_endpoint'], dict(
            grant_type='authorization_code', client_id=record['client'], code=code,
            code_verifier=verifier, redirect_uri=record['redirect'], resource=record['metadata']['resource']), form=True))

    def refresh(self, record):
        tokens = record['tokens']
        return token_set(self.request(record['metadata']['token_endpoint'], dict(
            grant_type='refresh_token', client_id=record['client'], refresh_token=tokens['refresh_token'],
            resource=record['metadata']['resource']), form=True), tokens)
