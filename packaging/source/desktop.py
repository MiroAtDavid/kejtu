#!/usr/bin/env python3
"""Register this source checkout with the Linux desktop; reuse managed startup."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[2]


class DesktopError(ValueError):
    pass


def text(value):
    value = str(value)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise DesktopError('Desktop launcher paths must not contain control characters.')
    return value


def string(value):
    return text(value).replace('\\', '\\\\').replace(' ', '\\s')


def argument(value):
    # Desktop Entry quoting is not shell quoting: escape its string layer too.
    value = text(value).replace('%', '%%')
    for char in ('\\', '"', '`', '$'):
        value = value.replace(char, '\\' + char)
    return '"' + value.replace('\\', '\\\\') + '"'


def ordinary(path, *, directory=False):
    """Never redirect a registration/removal through a symlink or hardlink."""
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise DesktopError(f'Desktop registration needs an ordinary directory: {parent}')
    if path.is_symlink():
        raise DesktopError(f'Desktop registration refuses a symlink: {path}')
    if path.exists():
        info = path.stat()
        valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
        if not valid or info.st_uid != os.getuid():
            raise DesktopError(f'Desktop registration needs a file owned by this user: {path}')


def description(root):
    root = root.resolve(strict=True)
    config = json.loads((root / 'desktop/src-tauri/tauri.conf.json').read_text())
    identifier = config['identifier']
    cargo = tomllib.loads((root / 'desktop/src-tauri/Cargo.toml').read_text())
    binary = cargo['package']['name']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', identifier) or not re.fullmatch(r'[a-z0-9_-]+', binary):
        raise DesktopError('Unsupported native desktop identity.')
    # Pinned GTK3 defaults WM_CLASS to argv[0] with an uppercase initial.
    # The maintained launcher uses X11; do not change GTK/app/data identity.
    wm_class = binary[0].upper() + binary[1:]
    icon = root / 'desktop/src-tauri/icons/icon.png'
    if not icon.is_file() or not (root / 'kejtu').is_file():
        raise DesktopError('The existing app icon or managed launcher is missing.')
    value = os.environ.get('XDG_DATA_HOME', '')
    data = Path(value) if Path(value).is_absolute() else Path.home() / '.local/share'
    target = data / 'applications' / (identifier + '.desktop')
    content = '\n'.join([
        '[Desktop Entry]', 'Type=Application', 'Name=Kejtu',
        'Comment=A quieter place to think',
        'Exec=/usr/bin/python3 -B ' + argument(root / 'packaging/source/desktop.py') + ' launch',
        'Path=' + string(root), 'Icon=' + string(icon), 'Terminal=false',
        'StartupNotify=false', 'StartupWMClass=' + wm_class,
        'Categories=Office;', 'X-Kejtu-Source-Root=' + string(root), '',
    ]).encode()
    return target, content


def receipt_path(root):
    return root / '.state/source-install/desktop.json'


def check(root):
    target, content = description(root)
    receipt = receipt_path(root)
    ordinary(target)
    ordinary(receipt)
    if target.exists() and target.read_bytes() != content:
        previous = json.loads(receipt.read_text()) if receipt.exists() else {}
        if (previous.get('root') != str(root.resolve()) or previous.get('path') != str(target)
                or previous.get('sha256') != hashlib.sha256(target.read_bytes()).hexdigest()):
            raise DesktopError(f'An existing or edited launcher was preserved: {target}. '
                               'Remove that entry yourself, or use --no-desktop-entry for this checkout.')
    return target, content


@contextmanager
def registration_lock(target):
    ordinary(target.parent, directory=True)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    lock = target.parent / ('.' + target.stem + '.lock')
    ordinary(lock)
    with os.fdopen(os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def atomic_write(path, content, mode):
    ordinary(path)
    ordinary(path.parent, directory=True)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.kejtu-desktop-', delete=False) as output:
            name = Path(output.name)
            os.fchmod(output.fileno(), mode)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if name is not None:
            name.unlink(missing_ok=True)


def install(root):
    target, _ = check(root)
    with registration_lock(target):
        target, content = check(root)
        atomic_write(target, content, 0o644)
        receipt = dict(schema=1, root=str(root.resolve()), path=str(target),
                       sha256=hashlib.sha256(content).hexdigest())
        atomic_write(receipt_path(root), (json.dumps(receipt, indent=2) + '\n').encode(), 0o600)
    return target


def remove(root):
    # Use the recorded destination even if the current XDG base has changed.
    receipt = receipt_path(root)
    ordinary(receipt)
    if not receipt.exists():
        raise DesktopError('No desktop registration receipt exists for this checkout.')
    record = json.loads(receipt.read_text())
    target = Path(record['path'])
    expected_name = description(root)[0].name
    if (record.get('root') != str(root.resolve()) or not target.is_absolute()
            or target.name != expected_name or target.parent.name != 'applications'):
        raise DesktopError('The desktop registration receipt does not match this checkout.')
    ordinary(target)
    if not target.exists():
        return target
    with registration_lock(target):
        ordinary(target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != record['sha256']:
            raise DesktopError(f'The edited desktop entry was preserved: {target}')
        target.unlink()
    return target


def launch(root):
    launcher = root / 'kejtu'
    state = json.loads(subprocess.check_output([launcher, 'status'], cwd=root, text=True))
    if state['running']:
        return 0
    # Initialization, partial-state refusal, saved profile and process ownership
    # remain with ./kejtu. Never repair or replace app state from this shortcut.
    action = 'start' if (root / '.state/runtime/core.sqlite3').exists() else 'initialize'
    os.execv(launcher, [str(launcher), action])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'remove', 'launch'))
    args = parser.parse_args()
    try:
        if sys.platform != 'linux' or os.geteuid() == 0:
            raise DesktopError('Use this command as your normal Linux desktop user.')
        if args.action == 'launch':
            return launch(ROOT)
        path = install(ROOT) if args.action == 'install' else remove(ROOT)
        print(f'Desktop entry {"installed" if args.action == "install" else "removed"}: {path}')
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f'Desktop integration stopped: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
