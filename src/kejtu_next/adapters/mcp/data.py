"""Private writable folders of installed Shelf packages, beside the connection settings.

One folder per installation, ``shelf/<installation id>/``, with two parts:

``data``   what the package's programs keep (PLUGIN_DATA). It stays across updates, and on
           removal unless deleting it is chosen.
``cache``  disposable working space, one folder per connection inside it. It always goes
           with the package.

A folder belongs to its installation, never to a connection name, so a later package that
reuses a name never sees an earlier one's files. Only real directories inside this owner's
own root are created, entered or deleted; a link is never followed. Nothing in a folder is
executed or imported here, and its files are read only to copy them: for a backup, and back
again after a restore.
"""
import asyncio
from contextlib import closing, contextmanager
import os
from pathlib import Path
import re
import shutil
import stat
import time
import uuid

from ...core.records import Failure

FOLDERS = dict(plugin_data='data', cache='cache')
MAX_DEPTH, MAX_ENTRIES = 64, 1_000_000
_ID = re.compile(r'[0-9a-f]{32}\Z')
_NAME = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')


class PackageData:
    def __init__(self, directory):
        self.base = Path(directory)
        self.root = self.base / 'shelf'

    def folder(self, kind, installation):
        """Where one part of an installation's folder lives."""
        if kind not in FOLDERS or not isinstance(installation, str) or not _ID.fullmatch(installation):
            raise Failure('shelf_record', 'Invalid package folder identity; nothing was changed.')
        return self.root / installation / FOLDERS[kind]

    def relative(self, kind, installation):
        """The same place, as a path below the settings folder (for the record)."""
        return self.folder(kind, installation).relative_to(self.base).as_posix()

    def state(self, kind, installation):
        """'present' or 'absent'. A link, or anything that is not a folder, is refused."""
        path = self.folder(kind, installation)
        self._real(path)
        return 'present' if path.is_dir() else 'absent'

    def holds_nothing(self, kind, installation):
        """Whether a part is absent or an empty folder: nothing in it could be lost."""
        path = self.folder(kind, installation)
        self._real(path)
        return not path.exists() or not any(path.iterdir())

    def create(self, kind, installation):
        """Create one part, private to this user. 'created', or 'present' when it was there."""
        path = self.folder(kind, installation)
        self._real(path)
        self._clear_leftovers()
        return self._ensure(path)

    def launch(self, installation, name):
        """The data folder and this connection's cache folder, ready for a program to start.

        Both are created when absent, as a connection's cache always was: a missing
        folder means an empty start, not a refusal. Each must be a real private folder
        at its own exact path. Nothing else is touched on this path.
        """
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise Failure('shelf_record', 'Invalid connection name for a package folder; nothing was started.')
        data, root = self.folder('plugin_data', installation), self.folder('cache', installation)
        for path in (data, root):
            self._real(path)
            self._ensure(path)
        cache = root / name
        if cache.is_symlink():
            raise Failure('mcp_cache', 'The connection cache must be a real private directory.')
        cache.mkdir(mode=0o700, exist_ok=True)
        for path in (data, cache):
            if path.resolve() != path or path.is_symlink() or not path.is_dir():
                raise Failure('mcp_cache', 'A package folder must be a real private directory.')
        return data, cache

    def _ensure(self, path):
        if path.is_dir():
            return 'present'
        try:
            for item in (self.root, path.parent, path):
                item.mkdir(mode=0o700, exist_ok=True)
        except OSError:
            raise Failure('shelf_data', 'A package folder could not be created; nothing else was changed.') from None
        self._real(path)
        return 'created'

    # For backups: the data folder is measured and copied as it is. A link is copied as a link
    # and never followed; anything that is neither a file, a folder nor a link is left out.

    def measure(self, installation, seconds=5):
        """What the data folder holds: ``(bytes, files, complete)``. Not complete when it took too long to count."""
        path = self.folder('plugin_data', installation)
        self._real(path)
        if not path.is_dir():
            return 0, 0, True
        total = files = 0
        until = time.monotonic() + seconds
        try:
            with closing(_walk(path)) as entries:
                for _, entry, _ in entries:
                    if time.monotonic() > until:
                        return total, files, False
                    if stat.S_ISREG(entry.st_mode):
                        total, files = total + entry.st_size, files + 1
        except OSError:
            raise Failure('shelf_data', 'A package folder changed or could not be read safely.') from None
        return total, files, True

    def snapshot(self, installation, destination):
        """Copy the data folder to ``destination``, which must not exist yet. What was copied, in numbers."""
        path = self.folder('plugin_data', installation)
        self._real(path)
        if not path.exists():
            return dict(bytes=0, files=0, links=0, left_out=0)
        return _copy(path, Path(destination))

    def restore(self, installation, source):
        """Put a copied data folder back as this installation's own. It must not be there yet."""
        path = self.folder('plugin_data', installation)
        self._real(path)
        if path.exists() or path.is_symlink():
            raise Failure('shelf_data', 'This package already has a data folder here; nothing was restored over it.')
        source = Path(source)
        if source.is_symlink() or not source.is_dir():
            raise Failure('shelf_data', 'The backup holds no data folder for this package; nothing was restored.')
        return _copy(source, path)  # destination ancestors are created through held directory handles

    async def remove(self, kind, installation):
        """Take one part out of use with a single rename, then delete it. False when it was not there."""
        path = self.folder(kind, installation)
        self._real(path)
        self._clear_leftovers()
        if not path.exists():
            self._tidy(path.parent)
            return False
        doomed = path.parent / ('.remove-' + uuid.uuid4().hex)
        path.rename(doomed)
        # It is gone from use. Deleting its files is cleanup; anything left is
        # cleared before the next package folder is created or removed.
        await asyncio.to_thread(shutil.rmtree, doomed, True)
        self._tidy(path.parent)
        return True

    def _real(self, path):
        """Every folder from ``path`` up to this owner's root must be a real directory, or absent."""
        for item in (path, path.parent, self.root):
            if item.is_symlink() or (item.exists() and not item.is_dir()):
                raise Failure('shelf_data', 'A package folder path is a link or not a folder; nothing was changed.')

    def _clear_leftovers(self):
        """Delete what an ended removal left behind: real ``.remove-*`` folders in real installation folders."""
        if self.root.is_symlink() or not self.root.is_dir():
            return
        for folder in self.root.iterdir():
            if folder.is_symlink() or not folder.is_dir() or not _ID.fullmatch(folder.name):
                continue
            for stale in folder.iterdir():
                if stale.name.startswith('.remove-') and not stale.is_symlink() and stale.is_dir():
                    shutil.rmtree(stale, ignore_errors=True)
            self._tidy(folder)

    @staticmethod
    def _tidy(folder):
        """Remove an installation's folder once nothing is left in it."""
        try:
            if not folder.is_symlink():
                folder.rmdir()
        except OSError:
            pass


@contextmanager
def _directory(path, *, parent=None, create=False):
    """Hold every traversed directory; never follow an ancestor link or reopen a pathname."""
    if os.open not in os.supports_dir_fd or not hasattr(os, 'O_NOFOLLOW'):
        raise Failure('shelf_data', 'Safe package folder copying is unavailable on this platform.')
    path = Path(path)
    if '..' in path.parts:
        raise Failure('shelf_data', 'A package folder path must not leave its selected directory.')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open('/', flags) if parent is None else os.dup(parent)
    try:
        parts = path.absolute().parts[1:] if parent is None else path.parts
        for name in parts:
            if name == '.':
                continue
            if create:
                try:
                    os.mkdir(name, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(name, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _same_file(before, after):
    if (before.st_dev, before.st_ino, stat.S_IFMT(before.st_mode)) != (after.st_dev, after.st_ino, stat.S_IFMT(after.st_mode)):
        raise Failure('shelf_data', 'A package entry changed while being copied; this copy was refused.')


def _walk(top):
    """Yield relative name, lstat and its held parent fd. The fd is valid until the next yield."""
    count = 0

    def walk(fd, relative, depth):
        nonlocal count
        if depth > MAX_DEPTH:
            raise Failure('shelf_data', 'A package’s data folder is nested too deeply to copy; nothing was left out silently.')
        with os.scandir(fd) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            count += 1
            if count > MAX_ENTRIES:
                raise Failure('shelf_data', 'A package’s data folder holds too many entries to copy; nothing was left out silently.')
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            yield relative / name, info, fd
            if stat.S_ISDIR(info.st_mode):
                with _directory(name, parent=fd) as child:
                    _same_file(info, os.fstat(child))
                    yield from walk(child, relative / name, depth + 1)

    if isinstance(top, int):
        yield from walk(top, Path('.'), 0)
    else:
        with _directory(top) as root:
            yield from walk(root, Path('.'), 0)


def _copy(source, destination):
    """Copy one folder to a place that does not exist yet. Files keep their bytes, mode and time."""
    done = dict(bytes=0, files=0, links=0, left_out=0)
    try:
        # Open the source first, including all ancestors. A disappearing source or
        # an unsafe path is an error once copying has started.
        with _directory(source) as source_root, _directory(destination.parent, create=True) as parent:
            os.mkdir(destination.name, 0o700, dir_fd=parent)
            with _directory(destination.name, parent=parent) as target_root, closing(_walk(source_root)) as entries:
                for relative, info, source_parent in entries:
                    with _directory(relative.parent, parent=target_root) as target_parent:
                        name = relative.name
                        if stat.S_ISDIR(info.st_mode):
                            os.mkdir(name, 0o700, dir_fd=target_parent)
                        elif stat.S_ISLNK(info.st_mode):
                            link = os.readlink(name, dir_fd=source_parent)
                            _same_file(info, os.stat(name, dir_fd=source_parent, follow_symlinks=False))
                            os.symlink(link, name, dir_fd=target_parent)
                            done['links'] += 1
                        elif stat.S_ISREG(info.st_mode):
                            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_parent)
                            with os.fdopen(fd, 'rb') as reader:
                                opened = os.fstat(reader.fileno())
                                _same_file(info, opened)
                                target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=target_parent)
                                with os.fdopen(target, 'wb') as writer:
                                    shutil.copyfileobj(reader, writer, 1024 * 1024)
                                    writer.flush()
                                    os.fchmod(writer.fileno(), stat.S_IMODE(opened.st_mode) & 0o700 | 0o600)
                                    os.utime(writer.fileno(), ns=(opened.st_atime_ns, opened.st_mtime_ns))
                            done['bytes'] += opened.st_size
                            done['files'] += 1
                        else:
                            done['left_out'] += 1
    except OSError:
        raise Failure('shelf_data', 'A package folder changed or could not be copied safely; this copy was refused.') from None
    return done
