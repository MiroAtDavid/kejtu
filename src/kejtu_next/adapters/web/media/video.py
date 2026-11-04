"""Curated video search and a separate, verified mpv window."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

from .errors import Cancelled
from .mpv import MpvController
from .video_sites import SITES, compact_entry, site_for_url
from ..network import public_address, public_url
from .errors import WorkspaceError

HELP = '''video sites
video search <site> "search terms" [--limit 5]  (1–10 matches)
video play "https://...individual-video-page..."
video pause | resume | stop | status
video volume <0-100> | seek <absolute seconds> | speed <0.25-3>
video fullscreen on | off
YouTube and Bilibili have native search; other sites use the current Web provider. Search requires Web permission.
Search matches are not proof of playability. Use a result's exact URL to play it.
One video at a time, in a separate mpv window. Radio is independent.
Public videos only: no browser cookies, login, subscription/DRM bypass, or adult platforms.
Availability/subtitles vary by country. Escape closes fullscreen; q closes the player.
'''
GUIDE = '''
Video: use standalone run_in_workspace commands. video sites lists curated platforms and links.
video search youtube "terms" works for official sports and anime channels too; bilibili offers donghua/anime.
Other curated platforms appear in video sites; video help explains search and controls.
video play "exact result URL" opens mpv. video pause / resume / stop / status; video help lists controls.
Use returned links, never invent them. Search results are candidates; only a playing receipt confirms playback.
'''
UNIT = 'kejtu-next-video-player.service'


def arguments(command):
    try:
        parts = shlex.split(command)
    except ValueError:
        if command.lstrip().startswith('video '):
            raise WorkspaceError('Check the quotes in this video command.') from None
        return None
    if not parts or parts[0] != 'video':
        return None
    args = parts[1:] or ['help']
    action = args[0]
    if action == 'search':
        if (len(args) not in (3, 5) or args[1] not in SITES or not args[2].strip()
                or len(args[2]) > 300 or any(ord(c) < 32 for c in args[2])):
            raise WorkspaceError('Use video search <site> "terms" [--limit 5]. Run video sites for names.')
        if len(args) == 5 and (args[3] != '--limit' or args[4] not in [str(n) for n in range(1, 11)]):
            raise WorkspaceError('Video search limit must be 1–10.')
    elif action == 'play':
        if len(args) != 2:
            raise WorkspaceError('Use video play "individual video URL".')
        site_for_url(args[1])
    elif action in ('volume', 'seek', 'speed'):
        try:
            value = float(args[1]) if len(args) == 2 else math.nan
        except ValueError:
            value = math.nan
        low, high = {'volume': (0, 100), 'seek': (0, 604800), 'speed': (.25, 3)}[action]
        if not math.isfinite(value) or not low <= value <= high:
            raise WorkspaceError(f'Video {action} must be a number from {low} to {high}.')
    elif action == 'fullscreen':
        if len(args) != 2 or args[1] not in ('on', 'off'):
            raise WorkspaceError('Use video fullscreen on or off.')
    elif action not in ('sites', 'help', 'pause', 'resume', 'stop', 'status') or len(args) != 1:
        raise WorkspaceError('Use one standalone video command. ' + HELP)
    return args


def catalogue(args):
    """Pure results shared by the application and standalone player surface."""
    if args[0] == 'help':
        return {'help': HELP}
    if args[0] == 'sites':
        return {'sites': [{'name': key, 'label': row[0], 'url': row[1]} for key, row in SITES.items()]}
    return None


class VideoController(MpvController):
    label = 'Video'

    def __init__(self, directory=None, *, search=None, interpreter=None):
        if directory is None:self._supported()
        self.directory = directory or Path(f'/run/user/{os.getuid()}/kejtu-next-video')
        self.socket_path = self.directory / 'mpv.sock'
        self.state_path = self.directory / 'video.json'
        self.unit = UNIT
        self.deadline = 0.0
        self.cancelled = threading.Event()
        self.search = search
        self.interpreter = Path(interpreter or '/usr/bin/python3')

    def _extract(self, request):
        if not self.interpreter.is_file():
            raise WorkspaceError('Video support needs its yt-dlp runtime. Install the optional private video runtime; see docs/private/university-media.md.')
        request['timeout'] = max(1, self.deadline - time.monotonic())
        environment = dict(os.environ)
        environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[4])
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            process = subprocess.Popen([str(self.interpreter), '-m', 'kejtu_next.adapters.web.media.video_extract'],
                stdin=subprocess.PIPE, stdout=output, stderr=errors, env=environment, start_new_session=True)
            try:
                process.stdin.write(json.dumps(request).encode())
                process.stdin.close()
                while process.poll() is None:
                    self._check()
                    if output.tell() > 2_000_000 or errors.tell() > 2_000_000:
                        raise WorkspaceError('Video extractor returned too much data.')
                    time.sleep(.05)
                output.seek(0)
                raw = output.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise WorkspaceError('Video extractor returned too much data.')
                try:
                    data = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise WorkspaceError('Video extraction failed before returning a result.') from None
                if process.returncode or data.get('error'):
                    raise WorkspaceError('Could not open this video source: ' + data.get('error', 'extractor failed'))
                return data
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()

    def _search(self, args, allow_web):
        site, query = args[1:3]
        limit = int(args[4]) if len(args) == 5 else 5
        if SITES[site][3]:
            return self._extract({'action': 'search', 'site': site, 'query': query, 'limit': limit})
        if not allow_web:
            raise WorkspaceError('This platform search needs web search enabled in Settings → Workspace.')
        raise WorkspaceError('This platform uses the current permitted web search provider; use the application search controls.')

    def _start(self):
        if self._alive():
            return
        self._clear_stale_socket()
        args = ['/usr/bin/systemd-run', '--user', '--quiet', '--collect', '--unit=' + self.unit,
                '--property=UMask=0077', '--property=NoNewPrivileges=yes', '--property=Type=exec', *self._desktop_environment(),
                '--property=MemoryMax=1G', '--property=TasksMax=128',
                '/usr/bin/mpv', '--no-config', '--load-scripts=no', '--ytdl=no',
                '--idle=yes', '--force-window=yes', '--terminal=no', '--input-terminal=no',
                '--volume=20', '--volume-max=100', '--network-timeout=15', '--access-references=no',
                '--demuxer-lavf-o=protocol_whitelist=[http,https,tcp,tls,crypto,data]',
                '--input-ipc-server=' + str(self.socket_path), '--title=Kejtu Next · Video']
        result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=min(5, max(.1, self.deadline - time.monotonic())))
        if result.returncode:
            raise WorkspaceError('Could not start video player: ' + result.stderr[:350])
        self._record_started()
        while not self._alive():
            self._check()
            time.sleep(.05)

    def _state(self):
        if not self._alive():
            return {'state': 'stopped'}
        props = {name: self._rpc(['get_property', name], optional=True) for name in
                 ('idle-active', 'pause', 'paused-for-cache', 'time-pos', 'duration',
                  'volume', 'speed', 'fullscreen', 'video-out-params', 'audio-out-params')}
        saved = json.loads(self.state_path.read_text()) if self.state_path.is_file() else {}
        ready = bool(props['video-out-params'])
        state = ('stopped' if props['idle-active'] else 'loading' if not ready else
                 'paused' if props['pause'] else 'buffering' if props['paused-for-cache'] else 'playing')
        return {'state': state, **saved, 'position_seconds': props['time-pos'], 'duration_seconds': props['duration'],
                'volume': props['volume'], 'speed': props['speed'], 'fullscreen': props['fullscreen'],
                'video_output_ready': ready, 'audio_output_ready': bool(props['audio-out-params'])}

    def _stop(self):
        self._quit()
        self.state_path.unlink(missing_ok=True)
        self.identity_path.unlink(missing_ok=True)
        return {'state': 'stopped'}

    def _play(self, url):
        resolved = self._extract({'action': 'play', 'url': url})
        streams = resolved['streams']
        # Validate resolved destinations too, before handing network URLs to mpv.
        for stream in streams:
            _, host, port = public_url(stream['url'])
            addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            if not addresses or any(not public_address(row[4][0]) for row in addresses):
                raise WorkspaceError('The media URL does not resolve to a public destination.')
        try:
            self._start()
            self._rpc(['stop'])
            self._rpc(['set_property', 'pause', False])
            headers = streams[0]['headers']
            self._rpc(['set_property', 'http-header-fields', [f'{key}: {value}' for key, value in headers.items()]])
            self._rpc(['set_property', 'audio-files', [stream['url'] for stream in streams[1:]]])
            self.state_path.write_text(json.dumps({'title': resolved['title'], 'url': url}) + '\n')
            # HLS requires following its media segments. Keep the process default
            # restrictive and enable references only for this resolved HLS item.
            # The HTTP(S) protocol whitelist still applies to nested media loads.
            file_options = {'access-references': 'yes' if any(stream.get('hls') is True for stream in streams) else 'no'}
            self._rpc(['loadfile', streams[0]['url'], 'replace', -1, file_options])
            while True:
                state = self._state()
                if state['state'] == 'playing' and isinstance(state.get('position_seconds'), (float, int)):
                    if len(streams) < 2 or state.get('audio_output_ready'):
                        return state
                self._check()
                time.sleep(.15)
        except (Cancelled, WorkspaceError, OSError):
            self.cancelled = threading.Event()
            self.deadline = time.monotonic() + 2
            try:
                self._stop()
            except (WorkspaceError, OSError):
                pass
            raise

    def run(self, args, *, timeout=60, cancelled=None, allow_web=False):
        args = arguments(shlex.join(['video', *args]))
        started = time.monotonic()
        self.deadline, self.cancelled = started + timeout, cancelled or threading.Event()
        action = args[0]
        data = catalogue(args)
        if data is not None:
            pass
        elif action == 'search':
            data = self._search(args, allow_web)
        else:
            with self._lock():
                if action == 'play':
                    data = self._play(args[1])
                elif action == 'stop':
                    data = self._stop()
                elif action == 'status':
                    data = self._state()
                else:
                    if not self._alive():
                        raise WorkspaceError('Video is stopped. Use video play "URL".')
                    if action == 'seek':
                        self._rpc(['seek', float(args[1]), 'absolute+exact'])
                    else:
                        prop = 'pause' if action in ('pause', 'resume') else action
                        value = (action == 'pause' if prop == 'pause' else args[1] == 'on' if prop == 'fullscreen'
                                 else float(args[1]))
                        self._rpc(['set_property', prop, value])
                    data = self._state()
        return {'status': 'succeeded', 'exit_code': 0, 'output': json.dumps(data, ensure_ascii=False),
                'duration_ms': round((time.monotonic() - started) * 1000)}


def main():
    try:
        print(VideoController().run(sys.argv[1:], allow_web=True)['output'])
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc)[:1000]}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
