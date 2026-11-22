"""Installed program trees of Shelf packages, under ``.state/apps/shelf``.

A tree is one pinned archive, unpacked and published whole at
``<tree_sha256>/<directory>``. Its location is its content: identical pinned
trees are one tree, and a different tree can never take its place. Everything
that uses the network or reads the content (download, extraction, hashing) runs
in a disposable process, which a stop or its time allowance ends at once.
Publishing is one rename, so a tree is either absent or complete. Nothing in a
tree is executed or imported here; included tools under ``.state/apps`` are
never touched.
"""
import asyncio
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import sys
import uuid

from ...core.records import Failure

DEPENDENCIES = ('npm', 'wheels')
PYTHON = '/usr/bin/python3'
WHEEL_HOST = 'https://files.pythonhosted.org/'
WHEEL_LOCK, WHEEL_FOLDER = 'wheels.lock.json', 'site-packages'
MAX_WHEELS = 200
NPM, NODE = '/usr/bin/npm', '/usr/bin/node'
REGISTRY = 'https://registry.npmjs.org/'
MAX_LOCK_BYTES = 4 * 1024 ** 2
MAX_PACKAGES = 2000
MAX_ARCHIVE_BYTES = 256 * 1024 ** 2
MAX_UNPACKED_BYTES = 1024 ** 3
SECONDS = 240
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
_DIRECTORY = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z')
# A structured refusal from the process proves nothing was published. This code
# means the process ended without saying, so the tree must be inspected.
UNKNOWN = 'shelf_tree_outcome'


def artifact(value):
    """A pinned archive: where it is, its exact bytes, and the exact tree it becomes.

    With ``dependencies`` the pinned tree is the one after the dependency step:
    the unpacked archive plus what its lock file installs.
    """
    try:
        url, directory = value['url'], value['directory']
        if value.get('dependencies') not in (None, *DEPENDENCIES):
            raise ValueError()
        if (not isinstance(url, str) or not url.startswith('https://') or len(url) > 2048
                or any(ord(c) <= 32 or c == '\\' for c in url)
                or not isinstance(directory, str) or not _DIRECTORY.fullmatch(directory)
                or type(value['bytes']) is not int or not 1 <= value['bytes'] <= MAX_ARCHIVE_BYTES
                or type(value['unpacked_bytes']) is not int or not 1 <= value['unpacked_bytes'] <= MAX_UNPACKED_BYTES
                or not all(isinstance(value[k], str) and _DIGEST.fullmatch(value[k]) for k in ('sha256', 'tree_sha256'))):
            raise ValueError()
    except (AttributeError, KeyError, TypeError, ValueError):
        raise Failure('shelf_source', 'A program needs an HTTPS archive pinned by its size, hash, folder and unpacked tree '
                      '(at most 256 MiB, 1 GiB unpacked); nothing was installed.') from None
    pin = {k: value[k] for k in ('url', 'bytes', 'sha256', 'directory', 'unpacked_bytes', 'tree_sha256')}
    if value.get('dependencies'):
        pin['dependencies'] = value['dependencies']
    return pin


def npm_lock(tree):
    """Check, as data, that a package's npm lock names only exact public registry packages.

    Every dependency must come from the public npm registry with a sha512 integrity
    value, alone or among other SRI tokens, so npm checks each download against
    the lock. A lock naming a Git repository, a folder, a link, another host, or
    no integrity is refused before
    npm is started, and so is a package that ships its own npm configuration, which
    could redirect the step. Returns the number of packages.
    """
    tree = Path(tree)
    try:
        if (tree / '.npmrc').is_symlink() or (tree / '.npmrc').exists():
            raise ValueError()
        for name in ('package.json', 'package-lock.json'):
            path = tree / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_LOCK_BYTES:
                raise ValueError()
        lock = json.loads((tree / 'package-lock.json').read_text())
        packages = lock['packages']
        if lock.get('lockfileVersion') not in (2, 3) or '' not in packages or not 1 < len(packages) <= MAX_PACKAGES + 1:
            raise ValueError()
        for name, entry in packages.items():
            if name == '':
                continue
            exact = (isinstance(entry.get('resolved'), str) and entry['resolved'].startswith(REGISTRY)
                     and isinstance(entry.get('integrity'), str)
                     and any(re.fullmatch(r'sha512-[A-Za-z0-9+/]{86}==(?:\?[^\s]+)?', token)
                             for token in entry['integrity'].split()))
            if not name.startswith('node_modules/') or entry.get('link') or not (exact or entry.get('inBundle') is True):
                raise ValueError()
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        raise Failure('shelf_dependency', 'The package’s npm lock must name only public npm registry packages with '
                      'sha512 integrity values, and it may not bring its own npm configuration; nothing was installed.') from None
    return len(packages) - 1


def relative_command(command):
    """An executable named inside its own tree: a plain relative path, never out of it."""
    parts = PurePosixPath(command).parts if isinstance(command, str) else ()
    if (not parts or len(command) > 512 or command.startswith('/') or '\\' in command
            or any(part in ('.', '..') or any(ord(c) < 32 or ord(c) == 127 for c in part) for part in parts)
            or PurePosixPath(command).as_posix() != command):
        raise Failure('shelf_source', 'A program is named by a plain path inside its own package; nothing was installed.')
    return command


def usable_program(tree, command):
    """Whether a declared program is a regular executable file inside the tree. Reads metadata only."""
    path = Path(tree) / relative_command(command)
    return not path.is_symlink() and path.is_file() and bool(path.stat().st_mode & 0o111)


def missing_programs(tree, commands):
    """The declared programs a tree does not hold as executables."""
    if not isinstance(commands, list) or len(commands) > 16:
        raise Failure('shelf_source', 'A package tree declares at most 16 programs; nothing was installed.')
    return [command for command in commands if not usable_program(tree, command)]


def clear_leftovers(root):
    """Delete staging folders an ended run left behind, inside this owner's own real folders only.

    Only real hash-named folders directly under the real root are entered, and
    only real ``.install-*`` and ``.remove-*`` folders in them are deleted. A link
    is never followed, so nothing outside the root can be reached.
    """
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        return
    for folder in root.iterdir():
        if folder.is_symlink() or not folder.is_dir() or not _DIGEST.fullmatch(folder.name):
            continue
        for stale in folder.iterdir():
            if stale.name.startswith(('.install-', '.remove-')) and not stale.is_symlink() and stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)


def tree_path(root, key):
    """Where the tree with this identity lives under ``root`` (``.state/apps/shelf``)."""
    tree, _, directory = key.partition('/') if isinstance(key, str) else ('', '', '')
    if not _DIGEST.fullmatch(tree) or not _DIRECTORY.fullmatch(directory):
        raise Failure('shelf_record', 'Invalid package tree identity; nothing was changed.')
    return Path(root) / tree / directory


def wheel_lock(tree):
    """Check, as data, a package's list of Python wheels; returns one pinned download per wheel.

    Each is an exact file on the Python package index: an HTTPS address ending in
    ``.whl``, its size and its sha256. A source archive is not a wheel and is refused,
    so nothing ever has to be built. The folder they unpack into must not exist yet.
    """
    tree = Path(tree)
    try:
        path = tree / WHEEL_LOCK
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_LOCK_BYTES:
            raise ValueError()
        if (tree / WHEEL_FOLDER).is_symlink() or (tree / WHEEL_FOLDER).exists():
            raise ValueError()
        lock = json.loads(path.read_text())
        wheels = lock['wheels']
        if lock.get('version') != 1 or not isinstance(wheels, list) or not 1 <= len(wheels) <= MAX_WHEELS:
            raise ValueError()
        items, names = [], set()
        for wheel in wheels:
            url, name = wheel['url'], PurePosixPath(wheel['url']).name
            if (not isinstance(url, str) or not url.startswith(WHEEL_HOST) or not url.endswith('.whl') or len(url) > 2048
                    or any(ord(c) <= 32 or c in '\\?#' for c in url) or name in names
                    or type(wheel['bytes']) is not int or not 1 <= wheel['bytes'] <= MAX_ARCHIVE_BYTES
                    or not isinstance(wheel['sha256'], str) or not _DIGEST.fullmatch(wheel['sha256'])):
                raise ValueError()
            names.add(name)
            items.append(dict(id=name, archive=wheel['sha256'] + '.whl', url=url, bytes=wheel['bytes'], sha256=wheel['sha256']))
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        raise Failure('shelf_dependency', 'The package’s Python wheel list must name only exact wheel files on '
                      'files.pythonhosted.org, each with its size and sha256; nothing was installed.') from None
    return items


class PackageTrees:
    seconds = SECONDS
    # Refusals that prove this request published nothing: the process says why, or
    # hashing showed the tree an installation wanted to share is not there as pinned.
    refusals = ('shelf_download', 'shelf_tree', 'shelf_tree_differs', 'shelf_tree_missing', 'shelf_program_missing',
                'shelf_dependency')
    pinned = staticmethod(artifact)

    @staticmethod
    def ready(pin):
        """Refuse, before anything is recorded, a package this computer cannot prepare."""
        if pin.get('dependencies') == 'npm' and not (Path(NPM).is_file() and Path(NODE).is_file()):
            raise Failure('shelf_dependency', 'This package needs Node.js and npm installed on this computer '
                          '(/usr/bin/node and /usr/bin/npm); nothing was installed.')
        if pin.get('dependencies') == 'wheels' and not Path(PYTHON).is_file():
            raise Failure('shelf_dependency', 'This package needs Python 3 installed on this computer '
                          '(/usr/bin/python3); nothing was installed.')

    def __init__(self, root, *, worker=None, data=None):
        # Trees live in the installation's private data: the checkout's .state,
        # or an installed package's data folder (``layout.private_data``).
        # ``base`` is the folder above it, where the symlink check stops.
        data = Path(data) if data is not None else Path(root) / '.state'
        self.base = data.parent.resolve()
        self.root = self.base / data.name / 'apps/shelf'
        self.worker = worker or self._process

    @staticmethod
    def key(pin):
        return pin['tree_sha256'] + '/' + pin['directory']

    def path(self, key):
        return tree_path(self.root, key)

    def locate(self, command):
        """The tree identity and the program inside it for an executable in a tree here, else None.

        Only a program this owner would accept in a package (``relative_command``) is located.
        """
        try:
            tree, directory, *program = Path(command).relative_to(self.root).parts
            if not _DIGEST.fullmatch(tree) or not _DIRECTORY.fullmatch(directory):
                return None
            return tree + '/' + directory, relative_command('/'.join(program))
        except (ValueError, Failure):  # elsewhere, not deep enough to be inside a tree, or not a plain path
            return None

    async def materialize(self, pin, commands, check):
        """Download, verify and publish one tree holding its declared programs.

        'published', or 'present' when the pinned tree was already there. A tree
        that lacks a declared program is never published, and one already in place
        that lacks it is refused (``shelf_program_missing``).
        """
        request = dict(action='materialize', root=str(self.root), artifact=artifact(pin), commands=list(commands))
        return self._state(await self.worker(request, check), ('published', 'present'))

    async def verify(self, key, check, commands=()):
        """What is there, by hashing it; nothing is changed.

        'ok' (the pinned tree, with every given program), 'absent', 'different', or
        'incomplete' (the pinned tree, but a given program is not an executable in it).
        """
        self.path(key)
        request = dict(action='verify', root=str(self.root), key=key, commands=list(commands))
        return self._state(await self.worker(request, check), ('ok', 'absent', 'different', 'incomplete'))

    async def remove(self, key):
        """Take a tree out of use with one rename, then delete it. False when it was not there."""
        path = self.path(key)
        # Every folder from the tree up to the application folder must be real.
        folders = [path, *path.parents[:path.parents.index(self.base)]]
        if any(item.is_symlink() for item in folders):
            raise Failure('shelf_tree', 'A package tree path is a symlink; nothing was deleted.')
        if not path.exists():
            return False
        doomed = path.parent / ('.remove-' + uuid.uuid4().hex)
        path.rename(doomed)
        # The tree is gone from use. Deleting its files is cleanup; anything left
        # is removed before the next tree is published here.
        await asyncio.to_thread(shutil.rmtree, doomed, True)
        try:
            path.parent.rmdir()
        except OSError:
            pass
        return True

    @staticmethod
    def _state(result, states):
        if result.get('ok') is True and result.get('state') in states:
            return result['state']
        code, error = result.get('code'), result.get('error')
        if result.get('ok') is False and code in PackageTrees.refusals and isinstance(error, str):
            raise Failure(code, error[:500])
        raise Failure(UNKNOWN, 'The package step ended without a result; inspect this installation before trying again.')

    async def _process(self, request, check):
        """One request in a disposable process, ended at once by a stop or the time allowance."""
        process = read = None
        try:
            check()
            env = {'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'LANG': 'C.UTF-8',
                   'PYTHONPATH': str(Path(__file__).resolve().parents[4] / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'}
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-B', '-m', 'kejtu_next', '--package-tree-worker',
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env=env, start_new_session=True)
            read = asyncio.create_task(process.communicate(json.dumps(request).encode()))
            while not read.done():
                await asyncio.wait({read}, timeout=.1)
                check()
            data, _ = read.result()
            if process.returncode != 0 or len(data) > 65536:
                return {}
            try:
                result = json.loads(data)
            except ValueError:
                return {}
            return result if isinstance(result, dict) else {}
        finally:
            if process:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
            if read:
                if not read.done():
                    read.cancel()
                await asyncio.gather(read, return_exceptions=True)
