"""Brave's explicit search contract; no provider selection or network IO here."""
import math
import re
from datetime import date

from .records import Failure

CATEGORIES = ('general', 'web', 'news', 'places', 'images', 'video')
COUNTS = {'general': 20, 'web': 20, 'news': 50, 'places': 100, 'images': 200, 'video': 50}
PROPERTIES = {
    'country': {'type': 'string', 'description': 'Country code, e.g. AT; ALL worldwide. Overrides a language-region country.'},
    'ui_lang': {'type': 'string', 'description': 'Response metadata language/region, e.g. de-AT; unavailable for images.'},
    'date_from': {'type': 'string', 'description': 'YYYY-MM-DD; supply date_to too, instead of time_range. Web/news/video only.'},
    'date_to': {'type': 'string', 'description': 'YYYY-MM-DD inclusive end; requires date_from.'},
    'goggles': {'type': 'array', 'minItems': 1, 'maxItems': 3, 'items': {'type': 'string'},
        'description': 'Web/news ranking only: up to 3 registered hosted URLs or inline rules, e.g. $boost=3,site=orf.at. Newlines separate rules. Optional; no default ranking changes.'},
    'operators': {'type': 'boolean', 'description': 'Web/news/video query operators, default true; false searches literal operator text.'},
    'safesearch': {'type': 'string', 'enum': ['off', 'moderate', 'strict'], 'description': 'Default moderate; images default strict and accept only off/strict.'},
    'spellcheck': {'type': 'boolean', 'description': 'Default false; true allows Brave to rewrite the query, disclosed in results.'},
    'extra_snippets': {'type': 'boolean', 'description': 'Web/news only; default true, up to 5 additional excerpts per hit.'},
    'count': {'type': 'integer', 'minimum': 1, 'maximum': 200,
        'description': 'Fetched hits, default 20. Maximum web 20, news/video 50, places 100, images 200. limit controls local delivery; keep count unchanged when paging.'},
    'location': {'type': 'string', 'description': 'Places anchor such as Vienna Austria; alternative to latitude+longitude. Use task-supplied location.'},
    'latitude': {'type': 'number', 'minimum': -90, 'maximum': 90, 'description': 'Places anchor or web search location bias; also requires longitude.'},
    'longitude': {'type': 'number', 'minimum': -180, 'maximum': 180},
    'radius': {'type': 'number', 'minimum': 0, 'maximum': 1000000,
        'description': 'Places radius bias in metres, requires an anchor. NOT a strict boundary or exhaustive nearby query.'},
    'units': {'type': 'string', 'enum': ['metric', 'imperial'], 'description': 'Places distances; default metric.'},
}

GUIDE = '''Active Brave capabilities: category general/web, news, places, images or video
selects the real corresponding API. SearXNG pools are unavailable on Brave.
Use ordinary web search by default. No option silently falls back to another API.

Operators go directly in query: site:orf.at, -site:example.org, "exact phrase",
-excluded, +required, intitle:, inbody:, inpage:, filetype:pdf or ext:pdf,
lang:de, loc:at, and uppercase AND/OR/NOT. They are enabled for web/news/video;
operators:false treats them literally. The site field appends one site: restriction.
Operators are provider filters, not proof every hit obeys them. Check results.
Finding a PDF does not enable PDF reading; the current reader is HTML/text only.

Country and language are independent: country:"AT", language:"de", ui_lang:"de-AT".
Existing language:"de-AT" also works. Omitted country remains worldwide; no user
location is inferred. Web/news/video allow time_range OR both date_from/date_to
(YYYY-MM-DD). Dates filter page age, not guaranteed event dates. Example:
{"query":"Hochwasser site:orf.at -Sport","country":"AT","language":"de","date_from":"2024-09-01","date_to":"2024-09-30"}.

Goggles optionally re-rank web/news; they do not add a different index. Use only
when a task needs source preferences. goggles:["$boost=3,site=orf.at\\n$downrank=2,site=example.org"]
boosts/downranks domains; $discard,site=example.org excludes one. Up to 3 values,
each inline rules or an already registered hosted Goggle URL. Do not invent URLs
or claim to register/upload a Goggle. Saved request options disclose ranking changes.

Places: {"category":"places","query":"cafes","location":"Vienna Austria","country":"AT","language":"de","radius":1000}.
Alternatively supply latitude and longitude together. Omit query to explore an
anchored area. Never send private remembered coordinates; use the task's place.
Radius is a bias, not a hard cutoff. Check coordinates/distances; do not assert
nearest, exhaustive coverage, routes or travel times. Returned addresses, hours,
ratings, contacts and source links are provider claims, not a freshly checked
business website. Missing fields are unknown. Fetched time is not update time;
opening hours can be stale. Provider place IDs expire after about 8 hours.

Images return source-page and image links/metadata; videos return links/metadata.
Neither means the image was viewed or the video watched. Goggles/extra_snippets
are web/news only; dates are web/news/video only. Images/places have no next engine
page: increase count only for a concrete gap. count fetches (default 20); limit
delivers saved hits (default 8). Continue source_id/next_offset before fetching
again. Keep every original option, including count/Goggles, for the next engine
page. Read saved sources with source_id even after the provider is switched.
'''


def validate(args):
    """Validate cross-field semantics before any request or usage reservation."""
    def fail(detail):
        raise Failure('web_arguments', detail + ' No request ran.')

    category = args.get('category', 'general')
    if not isinstance(category, str) or category not in CATEGORIES:
        fail('Brave category must be general/web, news, places, images or video; SearXNG pools are unavailable.')
    allowed = {'query', 'category', 'site', 'language', 'time_range', 'page', 'limit', 'offset'} | PROPERTIES.keys()
    if set(args) - allowed:
        fail('Unknown Brave option; use the supplied web_search schema.')
    for key, maximum in (('query', 600 if category in ('general', 'web', 'places') else 400),
                         ('site', 253), ('language', 20), ('country', 3), ('ui_lang', 10), ('location', 200)):
        if key in args and (not isinstance(args[key], str) or not args[key].strip()
                or len(args[key]) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in args[key])):
            fail(f'Invalid {key}.')
    if not args.get('query') and not (category == 'places' and ('location' in args or 'latitude' in args)):
        fail('Supply query, or omit it only for Places with a location or coordinate anchor.')
    if len(args.get('query', '').split()) > (75 if category in ('general', 'web', 'places') else 50):
        fail('Shorten the query to the endpoint word limit (web/places 75; others 50).')
    if 'site' in args and (not re.fullmatch(r'[A-Za-z0-9.-]+', args['site']) or '..' in args['site']):
        fail('site must be a domain without a URL path.')
    # Batch preflight must reject the complete composed request, not discover
    # these errors after an earlier valid operation has already been dispatched.
    composed = args.get('query', '') + (' site:' + args['site'] if args.get('site') else '')
    if len(composed) > (600 if category in ('general', 'web', 'places') else 400) or len(composed.split()) > (75 if category in ('general', 'web', 'places') else 50):
        fail('Shorten the query including its site filter to the endpoint character/word limit.')
    if 'site' in args and args.get('operators') is False:
        fail('site requires enabled operators.')
    if 'language' in args and not re.fullmatch(r'all|[a-z]{2,3}(?:-[A-Za-z]{2,4})?', args['language']):
        fail('Use a Brave language code or language-region such as de-AT.')
    if 'country' in args and not re.fullmatch(r'[A-Z]{2}|ALL', args['country']):
        fail('country must be an uppercase code such as AT, or ALL.')
    if 'ui_lang' in args and not re.fullmatch(r'[a-z]{2}-[A-Z]{2}', args['ui_lang']):
        fail('ui_lang must be a language-region code such as de-AT.')
    for key, maximum in (('page', 1 if category in ('places', 'images') else 10), ('count', COUNTS[category]), ('limit', 20)):
        if key in args and (type(args[key]) is not int or not 1 <= args[key] <= maximum):
            fail(f'{category} {key} must be 1–{maximum}.')
    if 'offset' in args and (type(args['offset']) is not int or not 0 <= args['offset'] <= 2000000):
        fail('offset must be a nonnegative saved-result position.')
    for key in ('operators', 'spellcheck', 'extra_snippets'):
        if key in args and type(args[key]) is not bool:
            fail(f'{key} must be true or false.')
    if 'safesearch' in args and args['safesearch'] not in ('off', 'strict', *(() if category == 'images' else ('moderate',))):
        fail('safesearch must be off/strict for images, or off/moderate/strict otherwise.')
    for key, supported in {
        'goggles': ('general', 'web', 'news'), 'extra_snippets': ('general', 'web', 'news'),
        'operators': ('general', 'web', 'news', 'video'), 'site': ('general', 'web', 'news', 'video'),
        'date_from': ('general', 'web', 'news', 'video'), 'date_to': ('general', 'web', 'news', 'video'),
        'time_range': ('general', 'web', 'news', 'video'), 'ui_lang': ('general', 'web', 'news', 'video', 'places'),
        'latitude': ('general', 'web', 'places'), 'longitude': ('general', 'web', 'places'),
        'location': ('places',), 'radius': ('places',), 'units': ('places',),
    }.items():
        if key in args and category not in supported:
            fail(f'{key} is unsupported for {category}; remove it or select a supported category.')
    if 'time_range' in args and args['time_range'] not in ('day', 'week', 'month', 'year'):
        fail('time_range must be day/week/month/year.')
    if 'date_from' in args or 'date_to' in args:
        try:
            start, end = args['date_from'], args['date_to']
            if 'time_range' in args or not all(isinstance(s, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', s) for s in (start, end)):
                raise ValueError
            if date.fromisoformat(start) > date.fromisoformat(end):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            fail('Use valid date_from and date_to (YYYY-MM-DD), start <= end, instead of time_range.')
    if 'goggles' in args:
        goggles = args['goggles']
        if not isinstance(goggles, list) or not 1 <= len(goggles) <= 3 or any(
            not isinstance(g, str) or not g.strip() or len(g.encode()) > 2048
            or any(ord(c) < 32 and c not in '\n\t' or ord(c) == 127 for c in g) for g in goggles):
            fail('goggles must contain 1–3 nonempty hosted URLs or inline definitions, at most 2048 bytes each.')
    if ('latitude' in args) != ('longitude' in args) or 'location' in args and 'latitude' in args:
        fail('Use location OR both latitude and longitude.')
    for key, low, high in (('latitude', -90, 90), ('longitude', -180, 180), ('radius', 0, 1000000)):
        if key in args and (type(args[key]) not in (int, float) or not low <= args[key] <= high or not math.isfinite(args[key])):
            fail(f'{key} must be a finite number between {low} and {high}.')
    if 'radius' in args and not ('location' in args or 'latitude' in args):
        fail('radius requires a Places location or coordinates.')
    if 'units' in args and args['units'] not in ('metric', 'imperial'):
        fail('units must be metric or imperial.')
    return args
