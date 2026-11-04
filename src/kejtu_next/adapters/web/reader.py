"""Cancellable isolated public-page extraction helper."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .worker import Cancelled


def browser_arguments(root):
    """Fixed helper command; Linux retains its separate PID/filesystem namespace."""
    if sys.platform == 'win32':
        from .windows_reader import arguments
        return arguments(root)
    from ..linux_sandbox import command
    args = command(root, browser=True) + ['--unshare-user', '--unshare-pid', '--unshare-ipc',
            '--unshare-uts', '--die-with-parent', '--new-session', '--cap-drop', 'ALL',
            '--ro-bind', '/usr', '/usr', '--proc', '/proc', '--dev', '/dev',
            '--tmpfs', '/tmp', '--dir', '/etc', '--setenv', 'HOME', '/tmp']
    for name in ('lib', 'lib64', 'bin', 'sbin'):
        path = Path('/') / name
        if path.is_symlink():
            args += ['--symlink', os.readlink(path), str(path)]
        elif path.exists():
            args += ['--ro-bind', str(path), str(path)]
    for name in ('resolv.conf', 'nsswitch.conf', 'hosts', 'ssl', 'fonts', 'ld.so.cache', 'os-release'):
        path = Path('/etc') / name
        if path.exists():
            args += ['--ro-bind', str(path.resolve()), str(path)]
    for path in (Path(sys.prefix), root / '.state/playwright', Path(__file__).resolve().parent):
        args += ['--ro-bind', str(path), str(path)]
    return args + ['--remount-ro', '/', '--chdir', '/tmp', '--', sys.executable, '-E', '-s', '-B',
                   str(Path(__file__).with_name('reader_backend.py'))]


class WebReader:
    def __init__(self, runtime_root):
        # Windows embedded Python's prefix is .venv/Scripts, not a venv root.
        # Resolve the bundled browser beside this installation's source instead.
        self.runtime_root = Path(__file__).resolve().parents[4] if sys.platform == 'win32' else Path(runtime_root)

    def fetch(self, url, timeout, cancelled):
        if sys.platform == 'win32':
            from .windows_reader import fetch
            return fetch(self.runtime_root, url, timeout, cancelled)
        root = self.runtime_root
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'LC_ALL', 'LD_LIBRARY_PATH')}
        env['PLAYWRIGHT_BROWSERS_PATH'] = str(root / '.state/playwright')
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        # No inherited credentials, browser profile, desktop bus or model endpoint.
        process = subprocess.Popen(browser_arguments(root),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True, env=env)
        pending = json.dumps({'url': url, 'timeout': timeout})
        deadline = time.monotonic() + timeout
        try:
            while True:
                if cancelled.is_set():
                    raise Cancelled()
                if time.monotonic() >= deadline:
                    raise ValueError('Page reading timed out; no page content was confirmed. Try another public page.')
                try:
                    output, error = process.communicate(input=pending, timeout=.1)
                    break
                except subprocess.TimeoutExpired:
                    pending = None
            if len(output.encode()) > 2_000_000:
                raise ValueError('The page extract exceeded the supported size.')
            try:
                data = json.loads(output)
            except ValueError:
                raise ValueError('Browser helper failed: ' + error[-800:]) from None
            if process.returncode or (data.get('error') and 'status' not in data):
                raise ValueError(data.get('error', 'Browser helper failed.'))
            return data
        finally:
            # Browser/driver children must stop even after the helper crashes.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()

