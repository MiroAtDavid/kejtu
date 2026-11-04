"""Brave web/news/places/image/video results; one fixed-origin request, no redirects or retries.

An owned child bounds DNS, TLS and reads and is reaped before Stop completes.
The credential travels only through its anonymous input pipe and the TLS header.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from ...core.credentials import validate_key, credential_echo
from ...core.records import Failure
from ...core.brave_search import CATEGORIES, validate
from .brave_results import normalize
from .tls import platform_https_handlers

MAX_BYTES = 1_000_000
# Tool arguments are bounded separately from their JSON pipe representation.
# Unicode Goggles and escaped credentials must fit without clipping the request.
MAX_REQUEST_BYTES = 32768
ORIGIN = 'https://api.search.brave.com'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def parameters(args):
    validate(args)
    category = args.get('category', 'general')
    kind = 'web' if category == 'general' else category
    query = args.get('query', '') + (' site:' + args['site'] if args.get('site') else '')
    if len(query) > (600 if kind in ('web', 'places') else 400) or len(query.split()) > (75 if kind in ('web', 'places') else 50):
        raise Failure('web_query_limit', 'The query including site exceeds Brave’s endpoint limit. Shorten it; no request ran.')
    if args.get('site') and args.get('operators') is False:
        raise Failure('web_arguments', 'site requires enabled operators. No request ran.')
    params = dict(count=args.get('count', 20), country='ALL',
                  safesearch=args.get('safesearch', 'strict' if kind == 'images' else 'moderate'),
                  spellcheck=str(args.get('spellcheck', False)).lower())
    if query:
        params['q'] = query
    if kind not in ('places', 'images'):
        params['offset'] = args.get('page', 1)-1
        params['operators'] = str(args.get('operators', True)).lower()
        params['include_fetch_metadata'] = 'true'
    if kind in ('web', 'news'):
        params['extra_snippets'] = str(args.get('extra_snippets', True)).lower()
        if 'goggles' in args:
            params['goggles'] = args['goggles']
    if kind == 'web':
        params.update(result_filter='web', text_decorations='false', summary='false')
    language = args.get('language', 'all')
    if language != 'all':
        # Preserve native Brave language codes while accepting the established
        # language-region shorthand. Explicit country has final precedence.
        parts = language.split('-')
        if len(parts) == 2 and len(parts[1]) == 2 and language not in ('en-gb', 'pt-br', 'pt-pt'):
            params.update(search_lang=parts[0], country=parts[1].upper())
        else:
            params['search_lang'] = language
    for key in ('country', 'ui_lang'):
        if key in args:
            params[key] = args[key]
    if args.get('time_range'):
        params['freshness'] = dict(day='pd', week='pw', month='pm', year='py')[args['time_range']]
    if 'date_from' in args:
        params['freshness'] = args['date_from'] + 'to' + args['date_to']
    if kind == 'places':
        params['units'] = args.get('units', 'metric')
        for key in ('location', 'latitude', 'longitude', 'radius'):
            if key in args:
                params[key] = args[key]
        if 'latitude' in args:
            params['geoloc'] = f'{args["latitude"]}x{args["longitude"]}'
    return kind, params


def exchange(args, key, timeout, *, opener=None):
    key = validate_key(key)
    kind, params = parameters(args)
    path = {'places': 'local/place_search', 'video': 'videos/search'}.get(kind, kind + '/search')
    headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity', 'X-Subscription-Token': key}
    if kind == 'web' and 'latitude' in args:
        headers.update({'X-Loc-Lat': str(args['latitude']), 'X-Loc-Long': str(args['longitude'])})
    request = Request(f'{ORIGIN}/res/v1/{path}?{urlencode(params, doseq=True)}', headers=headers)
    meta = dict(provider='brave', fetched_at=datetime.now(timezone.utc).isoformat(),
                search_kind=kind, results=[], next_page_candidate=None)
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
            if credential_echo(text, key):
                raise Failure('credential_exposure', 'Brave returned a credential; no search content was retained.')
            meta['http_status'] = status
            # These opaque headers describe several rate windows. Do not turn
            # them into an invented monthly balance or billing estimate.
            meta['rate_limits'] = {}
            for header in ('X-RateLimit-Limit', 'X-RateLimit-Remaining', 'X-RateLimit-Reset', 'Retry-After'):
                value = response.headers.get(header)
                if value and len(value) <= 200 and not credential_echo(value, key) and all(32 <= ord(c) < 127 for c in value):
                    meta['rate_limits'][header] = value
            if status != 200:
                code, message = (
                    ('web_credential', 'Brave rejected the API key. Check Settings → Web.') if status in (401,403) else
                    ('web_rate_limit', 'Brave declined the request because of a rate or account limit. Check the Brave dashboard and retry later.') if status == 429 else
                    ('web_arguments', 'Brave rejected the request options. Check supported language/country and Goggles syntax; no results were confirmed.') if status == 422 else
                    ('web_http', f'Brave returned HTTP {status}; no search results were confirmed.'))
                return dict(meta, status='error', code=code, message=message)
            body = json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            results, query_info, more = normalize(body, kind, params['count'])
            page = args.get('page', 1)
            meta.update(results=results, status='ok' if results else 'empty', query_info=query_info,
                        applied_options={k:v for k,v in params.items() if k != 'q'},
                        next_page_candidate=page+1 if more and results and page < 10 and kind not in ('places', 'images') else None)
            if kind == 'places':
                meta['notice'] = ('Brave Places provider data, not a checked business website. Radius is a distance bias, '
                    'not a strict boundary; results are not exhaustive. Hours can be stale; missing fields are unknown. '
                    'Place IDs are temporary (about 8 hours). No route or live travel time was obtained.')
            elif kind in ('images', 'video'):
                meta['notice'] = 'Search links and metadata only; no image was viewed or video watched.'
            return meta
    except Failure:
        raise
    except (OSError, ValueError, TypeError, AttributeError):
        raise Failure('web_unavailable', 'Brave search failed; no results were confirmed. No retry or provider switch ran.') from None


class Brave:
    name = 'brave'
    categories = CATEGORIES

    def __init__(self, key, *, spawn=asyncio.create_subprocess_exec):
        self.key, self.spawn = key, spawn

    def prepare(self, args):
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
                    raise Failure('web_arguments', 'The encoded Brave request exceeds its transport bound; no request ran.')
                out, error = await process.communicate(payload)
                if process.returncode or len(out) > MAX_BYTES or len(error) > 4096:
                    raise ValueError
                text = out.decode('utf-8')
                if credential_echo(text, self.key):
                    raise ValueError
                result = json.loads(text)
                if 'failure' in result:
                    raise Failure(result['failure'], result['message'])
                return result
        except TimeoutError:
            raise Failure('web_timeout', 'Brave search timed out; its outcome is unknown. No retry ran.') from None
        except (OSError, ValueError, TypeError):
            raise Failure('web_unavailable', 'Brave search failed; no results were confirmed.') from None
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
        result = dict(failure='web_unavailable', message='Brave search failed; no results were confirmed.')
    # This is a UTF-8 IPC pipe, not a locale-encoded console. Windows cp1252
    # otherwise corrupts accented results or refuses non-Western text/errors.
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False)+'\n').encode('utf-8'))
    sys.stdout.buffer.flush()


if __name__ == '__main__':
    main()
