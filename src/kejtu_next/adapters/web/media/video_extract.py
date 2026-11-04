"""Isolated yt-dlp worker. Only compact search data / one resolved item leave it.

Run with the dedicated .state/video-tools interpreter, not the main API venv.
The worker never downloads media, imports browser cookies, or loads user config.
"""
import json
import os
import re
import sys
import uuid

from .video_sites import EXTRACTORS, SITES, compact_entry, site_for_url
from ..network import public_proxy, public_url


class QuietLog:
    def debug(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        pass


def javascript_runtimes():
    # yt-dlp prefers Deno; keep Node available on hosts without Deno.
    return {name: {'path': '/usr/bin/' + name} for name in ('deno', 'node')
            if os.access('/usr/bin/' + name, os.X_OK)}


def bilibili_search(downloader, query, limit):
    # yt-dlp's flat Bilibili search discards titles. Keep the same public search
    # API and its anonymous cookie, retaining metadata from that one response.
    from yt_dlp.extractor.bilibili import BiliBiliSearchIE
    from yt_dlp.utils import clean_html, parse_duration
    extractor = BiliBiliSearchIE(downloader)
    extractor._set_cookie('.bilibili.com', 'buvid3', f'{uuid.uuid4()}infoc')
    payload = extractor._download_json('https://api.bilibili.com/x/web-interface/search/type', query,
        query={'keyword': query, 'search_type': 'video', 'page': 1, 'page_size': 20, 'highlight': 0})
    rows = (payload.get('data') or {}).get('result') or []
    return [{'title': clean_html(row.get('title')), 'url': row.get('arcurl'),
             'uploader': row.get('author'), 'duration': parse_duration(row.get('duration'))}
            for row in rows[:limit]]


def playable(info):
    if not info or info.get('_type') in ('playlist', 'multi_video'):
        raise ValueError('Choose an individual video URL, not a channel or playlist.')
    if info.get('has_drm') or (info.get('age_limit') or 0) >= 18:
        raise ValueError('This trial does not play DRM-protected or age-18 material.')
    streams = info.get('requested_formats') or [info]
    if len(streams) > 2:
        raise ValueError('This video requires an unsupported stream combination.')
    selected = []
    for item in streams:
        url = item.get('url')
        public_url(url)
        if item.get('has_drm'):
            raise ValueError('This stream requires DRM.')
        headers = {k: str(v) for k, v in (item.get('http_headers') or {}).items()
                   if k.lower() in ('user-agent', 'referer', 'origin') and '\n' not in str(v) and '\r' not in str(v)}
        selected.append({'url': url, 'headers': headers, 'audio_only': item.get('vcodec') == 'none',
                         'hls': item.get('protocol') in ('m3u8', 'm3u8_native')})
    if all(item['audio_only'] for item in selected):
        raise ValueError('No playable video track was returned.')
    selected.sort(key=lambda item: item['audio_only'])
    return {'title': str(info.get('title') or 'Video')[:200],
            'duration_seconds': info.get('duration'), 'streams': selected}


def extract(request):
    from importlib.metadata import version
    if version('yt-dlp')!='2026.8.19' or version('yt-dlp-ejs')!='0.8.0':
        raise ValueError('The optional video runtime must match its pinned yt-dlp 2026.8.19 and EJS 0.8.0 manifest.')
    from yt_dlp import YoutubeDL
    from yt_dlp.globals import plugin_dirs
    plugin_dirs.value = []

    search = request['action'] == 'search'
    site = request['site'] if search else site_for_url(request['url'])
    prefix = SITES[site][3]
    if search and not prefix:
        raise ValueError('This platform uses local web search.')
    target = f"{prefix}{request['limit']}:{request['query']}" if search else request['url']
    # Reuse the web reader's checked, pinned public egress for metadata extraction.
    with public_proxy(timeout=request.get('timeout', 55)) as proxy:
        options = {
            'quiet': True, 'no_warnings': True, 'logger': QuietLog(),
            'allowed_extractors': EXTRACTORS, 'extract_flat': 'in_playlist' if search else False,
            'skip_download': True, 'noplaylist': True, 'playlistend': request.get('limit', 1),
            'age_limit': 17, 'socket_timeout': 12, 'retries': 0, 'extractor_retries': 0,
            'cachedir': False, 'js_runtimes': javascript_runtimes(),
            'remote_components': set(), 'format': 'bv*[height<=1080]+ba/b[height<=1080]/b',
            'proxy': f'http://kejtu:{proxy.password}@127.0.0.1:{proxy.server_port}',
        }
        with YoutubeDL(options) as downloader:
            info = ({'entries': bilibili_search(downloader, request['query'], request['limit'])}
                    if search and site == 'bilibili' else downloader.extract_info(target, download=False))
        if search:
            rows = [compact_entry(row, site=site) for row in (info or {}).get('entries', []) if row]
            return {'site': site, 'results': [row for row in rows if row][:request['limit']],
                    'note': 'Search matches; playback, subtitles and regional availability are checked when opening.'}
        return playable(info)


def main():
    try:
        request = json.loads(sys.stdin.read(16384))
        print(json.dumps(extract(request), ensure_ascii=False))
    except Exception as exc:
        # Never put expiring URLs or proxy credentials into a chat receipt.
        message = re.sub(r'https?://\S+', '[link]', str(exc))
        if any(phrase in message.lower() for phrase in ('log in', 'logged-in', 'sign in', '--cookies')):
            message = 'The site requires sign-in for this video. This trial does not use accounts or browser cookies.'
        print(json.dumps({'error': message[:500]}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
