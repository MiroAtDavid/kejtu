"""The deliberately small video catalogue, shared by commands and extraction."""
from ..network import public_url


# name: (label, homepage, accepted page domains, native search prefix)
SITES = {
    'youtube': ('YouTube', 'https://www.youtube.com', ('youtube.com', 'youtu.be'), 'ytsearch'),
    'vimeo': ('Vimeo', 'https://vimeo.com', ('vimeo.com',), None),
    'dailymotion': ('Dailymotion', 'https://www.dailymotion.com', ('dailymotion.com', 'dai.ly'), None),
    'arte': ('ARTE', 'https://www.arte.tv', ('arte.tv',), None),
    'ard': ('ARD Mediathek', 'https://www.ardmediathek.de', ('ardmediathek.de',), None),
    'zdf': ('ZDF', 'https://www.zdf.de', ('zdf.de', 'zdfheute.de'), None),
    'orf': ('ORF ON', 'https://on.orf.at', ('on.orf.at',), None),
    'bilibili': ('Bilibili · donghua/anime', 'https://www.bilibili.com', ('bilibili.com', 'bilibili.tv'), 'bilisearch'),
    'redbull': ('Red Bull TV · sports', 'https://www.redbull.com/int-en/discover', ('redbull.com', 'redbull.tv'), None),
}

# No generic page extractor, plugins, adult-site catalogue or local-file input.
EXTRACTORS = [r'youtube(?::.*)?', r'vimeo(?::.*)?', r'dailymotion(?::.*)?',
              r'ArteTV.*', r'ARDMediathek.*', r'zdf(?::.*)?', r'orf:on',
              r'BiliBili.*', r'BiliIntl', r'biliIntl:series', r'RedBull.*']


def site_for_url(url):
    _, host, _ = public_url(url)
    for key, (_, _, domains, _) in SITES.items():
        if any(host == domain or host.endswith('.' + domain) for domain in domains):
            return key
    raise ValueError('That platform is outside this video trial. Run video sites for supported platforms.')


def compact_entry(info, *, site=None):
    """Search receipts contain stable page links, never expiring stream URLs."""
    age = info.get('age_limit')
    if isinstance(age, (float, int)) and age >= 18:
        return None
    url = info.get('webpage_url') or info.get('url')
    try:
        actual = site_for_url(url)
    except (ValueError, TypeError):
        return None
    if site and actual != site:
        return None
    return {'title': str(info.get('title') or 'Untitled video')[:200], 'url': url,
            'channel': str(info.get('channel') or info.get('uploader') or '')[:100],
            'duration_seconds': info.get('duration'), 'playability': 'not_checked'}
