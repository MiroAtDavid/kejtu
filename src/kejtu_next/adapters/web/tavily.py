"""One fixed-origin Tavily request, with optional credentials and an owned child.

No SDK dependency, redirects, ambient proxies, retries or authentication fallback.
Only the anonymous input pipe and TLS Authorization header carry a saved key.
"""
import asyncio
import json
import math
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from ...core.credentials import validate_key, credential_echo
from ...core.records import Failure
from ...core.tavily_search import CATEGORIES, validate
from .tls import platform_https_handlers

MAX_BYTES = 1_000_000
MAX_REQUEST_BYTES = 32768
ORIGIN = 'https://api.tavily.com'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def parameters(args):
    validate(args)
    category = args.get('category', 'general')
    params = dict(query=args['query'], topic='general' if category == 'web' else category,
                  max_results=args.get('count', 10), search_depth=args.get('search_depth', 'basic'),
                  auto_parameters=False, include_answer=False, include_raw_content=False,
                  include_images=False, include_usage=True)
    if 'site' in args:
        params.update(include_domains=[args['site']], include_domains_mode='restrict')
    if 'exclude_domains' in args:
        params['exclude_domains'] = args['exclude_domains']
    if 'time_range' in args:
        params['time_range'] = args['time_range']
    if args.get('language', 'all') != 'all':
        params['language'] = args['language']
    return params


def normalize(body, count):
    if not isinstance(body, dict) or not isinstance(body.get('results'), list) or len(body['results']) > count:
        raise ValueError
    results = []
    for row in body['results']:
        if not isinstance(row, dict) or any(not isinstance(row.get(k), str) for k in ('title', 'url', 'content')):
            raise ValueError
        result = dict(title=row['title'], url=row['url'], snippet=row['content'])
        if row.get('score') is not None:
            score = row['score']
            if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError
            result['provider_score'] = score
        if row.get('published_date') is not None:
            if not isinstance(row['published_date'], str):
                raise ValueError
            result['published_date'] = row['published_date']
        results.append(result)
    return results


def exchange(args, key, timeout, *, opener=None):
    params = parameters(args)
    if key is not None:
        key = validate_key(key)
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json', 'Accept-Encoding': 'identity'}
    if key is None:
        headers['X-Tavily-Access-Mode'] = 'keyless'
    else:
        headers['Authorization'] = 'Bearer ' + key
    request = Request(ORIGIN + '/search', data=json.dumps(params).encode(), headers=headers, method='POST')
    meta = dict(provider='tavily', fetched_at=datetime.now(timezone.utc).isoformat(),
                search_kind=params['topic'], results=[], next_page_candidate=None,
                applied_options={k: v for k, v in params.items() if k != 'query'})
    try:
        opener = opener or build_opener(ProxyHandler({}), NoRedirect(), *platform_https_handlers())
        try:
            response = opener.open(request, timeout=timeout)
        except HTTPError as error:
            response = error
        with response:
            status = response.status
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError
            text = raw.decode('utf-8')
            if key is not None and credential_echo(text, key):
                raise Failure('credential_exposure', 'Tavily returned a credential; no search content was retained.')
            meta['http_status'] = status
            if status != 200:
                code, message = (
                    ('web_credential', 'Tavily rejected access. Check Settings → Web and your Tavily key.') if status in (401, 403) else
                    ('web_rate_limit', 'Tavily reached a rate or account limit. Retry later, or check your key and account limits in Settings → Web.') if status in (429, 432, 433) else
                    ('web_arguments', 'Tavily rejected the search options; no results were confirmed.') if status in (400, 422) else
                    ('web_http', f'Tavily returned HTTP {status}; no search results were confirmed.'))
                return dict(meta, status='error', code=code, message=message)
            body = json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            results = normalize(body, params['max_results'])
            meta.update(results=results, status='ok' if results else 'empty',
                        notice='Tavily search excerpts, not independently checked source pages. No generated answer was requested.')
            # Provider-reported credits are evidence, not a computed account balance.
            usage = body.get('usage')
            if usage is not None:
                if (not isinstance(usage, dict) or type(usage.get('credits')) not in (int, float)
                        or not math.isfinite(usage['credits']) or usage['credits'] < 0):
                    raise ValueError
                meta['query_info'] = {'usage': {'credits': usage['credits']}}
            return meta
    except Failure:
        raise
    except (OSError, ValueError, TypeError, AttributeError):
        raise Failure('web_unavailable', 'Tavily search failed; no results were confirmed. No retry or provider switch ran.') from None


class Tavily:
    name = 'tavily'
    categories = CATEGORIES

    def __init__(self, key=None, *, credentials_available=True, spawn=asyncio.create_subprocess_exec):
        self.key, self.spawn = key, spawn
        self.credentials_available = credentials_available

    def prepare(self, args):
        if not self.credentials_available:
            raise Failure('credential_store', 'Tavily key status is unknown. Reload its key in Settings → Web before searching.')
        if self.key is not None:
            validate_key(self.key)
        parameters(args)

    async def search(self, args, *, deadline):
        self.prepare(args)
        remaining = min(25, deadline-asyncio.get_running_loop().time())
        if remaining <= 0:
            raise Failure('timeout', 'The search deadline expired; no request ran.')
        process = None
        try:
            async with asyncio.timeout(remaining):
                process = await self.spawn(sys.executable, '-B', '-m', __name__, '--request',
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                payload = json.dumps(dict(arguments=args, api_key=self.key, timeout=remaining), ensure_ascii=False).encode()
                if len(payload) > MAX_REQUEST_BYTES:
                    raise Failure('web_arguments', 'The encoded Tavily request exceeds its transport bound; no request ran.')
                out, error = await process.communicate(payload)
                if process.returncode or len(out) > MAX_BYTES or len(error) > 4096:
                    raise ValueError
                text = out.decode('utf-8')
                if self.key is not None and credential_echo(text, self.key):
                    raise ValueError
                result = json.loads(text)
                if 'failure' in result:
                    raise Failure(result['failure'], result['message'])
                return result
        except TimeoutError:
            raise Failure('web_timeout', 'Tavily search timed out; its outcome is unknown. No retry ran.') from None
        except (OSError, ValueError, TypeError):
            raise Failure('web_unavailable', 'Tavily search failed; no results were confirmed.') from None
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                # Stop must join even if another cancellation arrives.
                waiter = asyncio.create_task(process.communicate())
                while not waiter.done():
                    try:
                        await asyncio.shield(waiter)
                    except asyncio.CancelledError:
                        pass


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError
        packet = json.loads(raw)
        result = exchange(packet['arguments'], packet['api_key'], packet['timeout'])
    except Failure as error:
        result = dict(failure=error.code, message=error.detail)
    except Exception:
        result = dict(failure='web_unavailable', message='Tavily search failed; no results were confirmed.')
    # This is a UTF-8 IPC pipe, not a locale-encoded console. Windows cp1252
    # otherwise corrupts accented results or refuses non-Western text/errors.
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False)+'\n').encode('utf-8'))
    sys.stdout.buffer.flush()


if __name__ == '__main__':
    main()
