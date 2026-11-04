"""Trusted Windows page-reader startup and lifetime, not OS tool confinement.

The fixed helper owns its Chromium/driver descendants in a kill-on-close Job.
Web destinations retain the public proxy and request rules in reader_backend;
Chromium keeps its own sandbox. No personal browser profile is opened.
"""
import json
import os
from pathlib import Path
import shutil
import site
import subprocess
import sys
import tempfile
import time

from ...core.records import Failure
from ..mcp.windows_runtime import environment, own_process_tree
from .network import public_url
from .worker import Cancelled

MAX_BYTES = 2_000_000
_prepared = None


def layout(root):
    root = Path(root).resolve()
    executable = root / 'package-runtime/browser/reader/chrome-headless-shell-win64/chrome-headless-shell.exe'
    sdk = root / 'package-runtime/mcp/python'
    helper = root / 'src/kejtu_next/adapters/web/reader_backend.py'
    if not executable.is_file() or not sdk.is_dir() or not helper.is_file():
        raise Failure('web_reader_unavailable', 'The packaged webpage reader is missing. No browser was started.')
    # Package verification owns hashes. Startup also refuses replaced links at
    # these fixed executable/dependency paths instead of following an alias.
    for target in (executable, sdk, helper):
        for candidate in (target, *target.parents):
            if candidate == root:
                break
            if candidate.is_symlink() or candidate.is_junction():
                raise Failure('web_reader_unavailable', 'The packaged webpage reader path has changed. No browser was started.')
    return executable, sdk


def arguments(root):
    layout(root)
    return [sys.executable, '-B', '-s', '-m', 'kejtu_next.adapters.web.reader_backend']


def prepare_backend():
    """Called in the disposable helper before Playwright can start any process."""
    global _prepared
    if _prepared is not None:
        return _prepared
    root = Path(__file__).resolve().parents[4]
    executable, sdk = layout(root)
    raw_cache = os.environ.get('KEJTU_READER_CACHE')
    if not raw_cache:
        raise ValueError('The page reader has no private temporary profile directory.')
    cache = Path(raw_cache)
    if not cache.is_absolute() or not cache.is_dir() or cache.is_symlink() or cache.is_junction():
        raise ValueError('The page reader temporary profile directory is unavailable.')
    clean = environment(cache)
    os.environ.clear()
    os.environ.update(clean)
    # pywin32's .pth bootstrap adds its DLL directories; it is the same pinned
    # optional SDK installation used by the Windows Files worker.
    site.addsitedir(str(sdk))
    own_process_tree()
    _prepared = executable
    return executable


def remove_cache(cache):
    # Job teardown can release Chromium profile locks just after worker exit.
    # Retry only deletion of this invocation's own generated scratch directory.
    deadline = time.monotonic() + 2
    while True:
        try:
            shutil.rmtree(cache)
            return
        except FileNotFoundError:
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise ValueError('The temporary browser profile could not be removed.') from None
            time.sleep(.05)


def fetch(root, url, timeout, cancelled):
    public_url(url)
    command = arguments(root)
    if cancelled.is_set():
        raise Cancelled()
    cache = Path(tempfile.mkdtemp(prefix='kejtu-page-reader-'))
    process = None
    try:
        env = environment(cache)
        env.update(PYTHONPATH=str(Path(root).resolve() / 'src'), KEJTU_READER_CACHE=str(cache))
        # Binary pipes avoid the Windows console code page. A new process group
        # is not needed: the helper joins its own Job before it starts children.
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=env, cwd=cache,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        pending = json.dumps({'url': url, 'timeout': timeout}, ensure_ascii=False,
                             allow_nan=False).encode('utf-8')
        deadline = time.monotonic() + timeout
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
        if len(output) > MAX_BYTES:
            raise ValueError('The page extract exceeded the supported size.')
        try:
            data = json.loads(output.decode('utf-8'))
        except (ValueError, UnicodeError):
            raise ValueError('The browser helper did not return a complete page result.') from None
        if not isinstance(data, dict):
            raise ValueError('The browser helper did not return a complete page result.')
        if process.returncode or (data.get('error') and 'status' not in data):
            raise ValueError(data.get('error', 'The browser helper could not read the page.'))
        return data
    finally:
        try:
            if process is not None:
                # Killing this exact helper closes its private Job handle and
                # kills its descendants; do not call Unix killpg on Windows.
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)
        finally:
            remove_cache(cache)
