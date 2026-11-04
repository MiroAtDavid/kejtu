"""Compact, labelled Brave evidence. Preserve chosen fields without paraphrasing."""

# Whole selected values are retained, never truncated to fit the model window.
# Saved-source paging owns admission; optional verbose reviews/photos stay outside
# this projection. A fetch timestamp never establishes business-data freshness.
COMMON = ('extra_snippets', 'page_age', 'age', 'page_fetched', 'fetched_content_timestamp',
          'language', 'family_friendly', 'profile', 'meta_url')
PLACE = ('id', 'coordinates', 'postal_address', 'opening_hours', 'contact', 'price_range',
         'rating', 'distance', 'categories', 'serves_cuisine', 'timezone', 'profiles', 'provider_url')
MEDIA = ('thumbnail', 'properties', 'video', 'source', 'confidence')


def normalize(body, kind, count):
    expected = {'web': 'search', 'places': 'locations', 'video': 'videos'}.get(kind, kind)
    if not isinstance(body, dict) or body.get('type') != expected:
        raise ValueError('Unexpected Brave envelope')
    section = body.get('web') if kind == 'web' else body
    if section is not None and not isinstance(section, dict):
        raise ValueError('Invalid result section')
    rows = [] if section is None else section.get('results', [])
    if rows is None and kind == 'places':
        rows = []
    if not isinstance(rows, list) or len(rows) > count:
        raise ValueError('Invalid result count')
    results = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid result')
        title, url = row.get('title'), row.get('url')
        if kind != 'images' and (not isinstance(title, str) or not isinstance(url, str)):
            raise ValueError('Missing result identity')
        if any(v is not None and not isinstance(v, str) for v in (title, url)):
            raise ValueError('Invalid result identity')
        result = dict(title=title or '', snippet=row.get('description') or '')
        if url is not None:
            result['url'] = url
        if not isinstance(result['snippet'], str):
            raise ValueError('Invalid snippet')
        fields = COMMON + (PLACE if kind == 'places' else MEDIA if kind in ('images', 'video') else ())
        for field in fields:
            value = row.get(field)
            if value is None:
                continue
            if field == 'extra_snippets' and (not isinstance(value, list) or len(value) > 5
                    or not all(isinstance(v, str) for v in value)):
                raise ValueError('Invalid extra snippets')
            if field in ('page_age', 'age', 'page_fetched') and not isinstance(value, str):
                raise ValueError('Invalid date')
            result[field] = value
        if kind in ('places', 'images', 'video'):
            result['result_type'] = kind
        results.append(result)
    if kind == 'places':
        # A city/address query can legitimately return no POIs. Keep these
        # separate labelled entities instead of reporting a false empty search.
        for collection in ('cities', 'countries', 'regions', 'neighborhoods', 'addresses'):
            entries = body.get(collection) or []
            if not isinstance(entries, list) or len(entries) > 100:
                raise ValueError('Invalid geographic results')
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get('name'), str):
                    raise ValueError('Invalid geographic identity')
                result = dict(title=entry['name'], result_type=collection, snippet='')
                for key in ('type', 'country', 'coordinates', 'postal_address', 'street', 'city', 'region', 'postal_code'):
                    if entry.get(key) is not None:
                        result[key] = entry[key]
                results.append(result)
        if len(results) > 500:
            raise ValueError('Too many geographic results')
    query = body.get('query') or {}
    if not isinstance(query, dict):
        raise ValueError('Invalid query metadata')
    query_info = {key: query[key] for key in ('original', 'altered', 'cleaned', 'spellcheck_off',
        'show_strict_warning', 'search_operators') if key in query}
    more = query.get('more_results_available', len(rows) == count)
    if type(more) is not bool:
        raise ValueError('Invalid pagination')
    return results, query_info, more
