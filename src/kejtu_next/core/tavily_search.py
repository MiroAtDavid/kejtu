"""Tavily search options; selection, credentials and IO belong to their owners."""
import re

from .records import Failure

CATEGORIES = ('general', 'web', 'news', 'finance')
PROPERTIES = {
    'search_depth': {'type': 'string', 'enum': ['basic', 'fast', 'ultra-fast', 'advanced'],
        'description': 'Default basic. Advanced retrieves more detail and may use more account credits; use only for a concrete evidence gap.'},
    'count': {'type': 'integer', 'minimum': 1, 'maximum': 20,
        'description': 'Fetched results, default 10. limit controls delivery of the saved results.'},
    'exclude_domains': {'type': 'array', 'maxItems': 20, 'items': {'type': 'string'},
        'description': 'Domains to exclude for this search. No URLs or paths.'},
}
GUIDE = '''Active Tavily capabilities: general/web, news and finance search. Use basic
depth by default; fast and ultra-fast favour latency, advanced may use more
account credits. No automatic depth changes or provider fallback run.
Keep queries under 400 characters. site restricts to one domain; exclude_domains
excludes sources. language accepts a two-letter code or all. time_range filters
day/week/month/year; it does not guarantee event dates or date ordering.
Returned snippets are provider excerpts, not independently checked pages or an
answer. Use web_read for original HTML/text evidence. Search does not enable PDF
reading, image viewing or authenticated browsing.
count fetches up to 20 hits (default 10); limit controls local delivery. Tavily
has no engine pages: page must be 1. Continue source_id/next_offset to read saved
hits before searching again. A rate/account limit is a failed request; report it
instead of retrying repeatedly or switching credentials/providers.
'''


def validate(args):
    def fail(message):
        raise Failure('web_arguments', message + ' No request ran.')

    allowed = {'query', 'category', 'site', 'language', 'time_range', 'page', 'limit', 'offset'} | PROPERTIES.keys()
    if not isinstance(args, dict) or set(args) - allowed:
        fail('Unknown Tavily option; use the supplied web_search schema.')
    query = args.get('query')
    if not isinstance(query, str) or not query.strip() or len(query) > 400 or any(ord(c) < 32 or ord(c) == 127 for c in query):
        fail('Tavily needs a query of 1–400 characters.')
    if args.get('category', 'general') not in CATEGORIES:
        fail('Tavily category must be general/web, news or finance.')
    if args.get('search_depth', 'basic') not in PROPERTIES['search_depth']['enum']:
        fail('Unsupported Tavily search depth.')
    if 'time_range' in args and args['time_range'] not in ('day', 'week', 'month', 'year'):
        fail('time_range must be day/week/month/year.')
    if 'language' in args and (not isinstance(args['language'], str) or not re.fullmatch(r'all|[a-z]{2}', args['language'])):
        fail('Use a two-letter language code such as de, or all.')
    for key, low, high in (('page', 1, 1), ('count', 1, 20), ('limit', 1, 20), ('offset', 0, 2000000)):
        if key in args and (type(args[key]) is not int or not low <= args[key] <= high):
            fail(f'Tavily {key} must be {low}–{high}; use source_id/next_offset for saved hits.')
    domains = args.get('exclude_domains', [])
    if not isinstance(domains, list) or len(domains) > 20:
        fail('exclude_domains must be a list of at most 20 domains.')
    for domain in domains + ([args['site']] if 'site' in args else []):
        if (not isinstance(domain, str) or len(domain) > 253 or '.' not in domain
                or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', domain)
                or '..' in domain):
            fail('Use domain names without URL schemes or paths.')
    if args.get('site', '').lower() in [d.lower() for d in domains]:
        fail('The included domain is also excluded.')
    return args
