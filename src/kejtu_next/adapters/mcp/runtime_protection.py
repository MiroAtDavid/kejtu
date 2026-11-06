"""Keep lower-trust writes separate from later host execution inputs on Linux."""
import hashlib
from importlib.machinery import PathFinder
import locale
import os
from pathlib import Path
import site
import stat
import sys
import sysconfig
import time

from ...core.records import Failure

MAX_ENTRIES = 100_000
MAX_SECONDS = 5


def _within(path, root):
    return path == root or root in path.parents


def _overlaps(left, right):
    return _within(left, right) or _within(right, left)


MAX_SYMLINK_HOPS = 64
MAX_PTH_BYTES = 1_048_576
MAX_PTH_TOTAL_BYTES = 8_388_608
MAX_PTH_ENTRIES = 100_000

# Reviewed files from the setuptools 80.9.0 wheel pinned by ops/requirements.lock.
# Updating the runtime pin requires reviewing the hook and its shim together.
SETUPTOOLS_FILES = {
    'distutils-precedence.pth': (151, '2638ce9e2500e572a5e0de7faed6661eb569d1b696fcba07b0dd223da5f5d224'),
    '_distutils_hack/__init__.py': (6755, 'df81e6bcba34ee3e3952f776551fb669143b9490fdd6c4caeb32609f97e985b4'),
    'setuptools-80.9.0.dist-info/METADATA': (6572, '7f890ca8dbc16b63b568c89312ed6a7f9f0a79d0b2b7f3c83bf796ac3d930615'),
}


def _unavailable():
    _refuse('Writable-folder and Python startup safety could not be checked within '
            'the scan limits. Choose a smaller accessible folder or read-only access. '
            'No tool was started.')


class _Budget:
    """Cooperative limits; an individual filesystem operation can still block."""
    def __init__(self, cancelled=None):
        self.deadline = time.monotonic() + MAX_SECONDS
        self.cancelled = cancelled
        self.count = 0

    def check(self):
        if self.cancelled is not None and self.cancelled():
            _refuse('Writable-folder safety checking was cancelled. No tool was started.')
        if time.monotonic() >= self.deadline:
            _unavailable()

    def entry(self):
        self.check()
        self.count += 1
        if self.count > MAX_ENTRIES:
            _unavailable()


def _expanded(path, *, _budget=None):
    """Resolve in filesystem order, retaining every replaceable symlink name.

    Never normalize a link target before following it: link/.. means the parent
    of the link's resolved target, not the link's lexical parent. Repeated links
    can be valid, so a hop limit rather than a global seen set bounds cycles.
    """
    budget = _budget or _Budget()
    path = Path(path).absolute()
    values, pending = {path}, list(reversed(path.parts[1:]))
    # Linux resolves a double-leading slash to the same root as a single slash.
    # Keep its lexical spelling above, but canonicalize the traversed pathname.
    resolved, hops = Path('/'), 0
    while pending:
        budget.check()
        part = pending.pop()
        if part == '..':
            resolved = resolved.parent
            continue
        candidate = resolved / part
        try:
            info = candidate.lstat()
        except (FileNotFoundError, NotADirectoryError):
            # Missing future paths and zip-import suffixes remain protected.
            resolved = candidate
            continue
        except OSError:
            _refuse('A protected runtime path could not be inspected. No tool was started.')
        if not stat.S_ISLNK(info.st_mode):
            resolved = candidate
            continue
        hops += 1
        if hops > MAX_SYMLINK_HOPS:
            _refuse('A protected runtime path contains a cycle or too many symlink hops. No tool was started.')
        values.add(candidate)
        try:
            target = Path(os.readlink(candidate))
        except OSError:
            _refuse('A protected runtime path could not be resolved. No tool was started.')
        if target.is_absolute():
            resolved = Path('/')
            parts = target.parts[1:]
        else:
            parts = target.parts
        pending.extend(reversed(parts))
    values.add(resolved)
    return values


def _trusted_setuptools_hook(path, raw, *, _budget):
    """Recognize the pinned shim without importing or executing packaging code.

    Only the interpreter's own site-packages may supply it. That directory is
    protected by execution_roots through sys.prefix, including alias checks.
    Unknown hooks and alternate module providers receive no exception.
    """
    name = 'distutils-precedence.pth'
    if path.name != name or (len(raw), hashlib.sha256(raw).hexdigest()) != SETUPTOOLS_FILES[name]:
        return False
    try:
        directory = Path(sysconfig.get_path('purelib')).resolve()
        if path != directory / name or not _within(directory, Path(sys.prefix).resolve()):
            return False
        for relative, (size, expected) in SETUPTOOLS_FILES.items():
            _budget.check()
            file = directory / relative
            info = file.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_size != size
                    or file.parent.is_symlink()):
                return False
            if relative == name:
                content = raw
            else:
                with file.open('rb') as stream:
                    content = stream.read(size + 1)
            _budget.check()
            if (len(content), hashlib.sha256(content).hexdigest()) != (size, expected):
                return False
        shim = directory / '_distutils_hack/__init__.py'
        # -S workers omit site-packages from their imports. Include the verified
        # environment for this lookup, while still rejecting earlier providers.
        spec = PathFinder.find_spec('_distutils_hack',
            [value for value in sys.path] + [str(directory)])
        if spec is None or spec.origin != str(shim):
            return False
        loaded = sys.modules.get('_distutils_hack')
        if loaded is not None and getattr(loaded, '__file__', None) != str(shim):
            return False
        _budget.check()
        return True
    except (OSError, ValueError, ImportError):
        return False


def _pth_targets(*, _budget=None):
    """Path targets configured by existing .pth files, including absent ones.

    site processes a path line only when that directory exists at startup. A
    writable grant could therefore create an absent target and make it a Python
    search root on a later normal host start. Executable lines refuse except
    for the exact pinned setuptools shim in the protected interpreter environment.
    Inspection never evaluates a hook or assumes its current effects are complete.
    """
    budget = _budget or _Budget()
    directories = []
    try:
        values = site.getsitepackages()
    except (AttributeError, TypeError, ValueError):
        values = []
    if isinstance(values, str):
        values = [values]
    if isinstance(values, (list, tuple)):
        directories.extend(Path(value) for value in values if isinstance(value, str))
    try:
        usersite = site.getusersitepackages()
    except (AttributeError, TypeError, ValueError):
        usersite = None
    if isinstance(usersite, str):
        directories.append(Path(usersite))
    elif isinstance(usersite, (list, tuple)):
        directories.extend(Path(value) for value in usersite if isinstance(value, str))
    targets, count, total = set(), 0, 0
    for directory in dict.fromkeys(directories):
        budget.check()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    budget.check()
                    count += 1
                    if count > MAX_PTH_ENTRIES:
                        _unavailable()
                    if not entry.name.endswith('.pth') or entry.name.startswith('.'):
                        continue
                    path = Path(entry.path)
                    # A dangling .pth symlink can become active later too.
                    targets.add(path)
                    if not entry.is_file():
                        continue
                    with path.open('rb') as stream:
                        raw = stream.read(min(MAX_PTH_BYTES, MAX_PTH_TOTAL_BYTES - total) + 1)
                    budget.check()
                    total += len(raw)
                    if len(raw) > MAX_PTH_BYTES or total > MAX_PTH_TOTAL_BYTES:
                        _refuse('Python startup configuration exceeds the inspection limits. '
                                'No tool was started.')
                    if _trusted_setuptools_hook(path, raw, _budget=budget):
                        continue
                    try:
                        content = raw.decode('utf-8-sig')
                    except UnicodeDecodeError:
                        # Match site.addpackage, including its locale fallback.
                        try:
                            content = raw.decode(locale.getencoding())
                        except (UnicodeError, LookupError):
                            _refuse('Python startup configuration could not be decoded. No tool was started.')
                    for line in content.splitlines():
                        budget.check()
                        if line.startswith('#') or not line.strip():
                            continue
                        if line.startswith(('import ', 'import\t')):
                            _refuse('Python startup configuration contains executable .pth code '
                                    'other than the verified setuptools 80.9.0 shim. Its future '
                                    'import paths cannot be checked. Use the supported Kejtu '
                                    'runtime or repair its installation. No tool was started.')
                        # Python removes trailing whitespace only, and applies
                        # abspath before checking whether this target exists.
                        targets.add(Path(os.path.abspath(os.path.join(directory, line.rstrip()))))
        except FileNotFoundError:
            # An absent site directory is normal. A disappearing .pth file is
            # not: its startup authority has changed during inspection.
            if Path(directory).exists():
                _refuse('Python startup configuration changed during inspection. No tool was started.')
        except OSError:
            _refuse('Python startup configuration could not be inspected. No tool was started.')
    return targets


def execution_roots(root, runtime=None, *, _budget=None):
    budget = _budget or _Budget()
    original_root = Path(root).absolute()
    root = original_root.resolve()
    # Protect future user-site imports too: site adds an absent directory on a
    # later interpreter start, including executable .pth and sitecustomize files.
    usersite = site.getusersitepackages()
    paths = {original_root, root, root / 'src', root / '.venv',
             Path(runtime) if runtime else root / '.state/mcp-runtime/python',
             Path(sys.prefix), Path(sys.exec_prefix),
             Path(sys.base_prefix), Path(sys.base_exec_prefix),
             Path(sys.executable).absolute().parent, Path(sys.executable).resolve().parent}
    if isinstance(usersite, str):
        paths.add(Path(usersite))
    elif isinstance(usersite, (list, tuple)):
        paths.update(Path(value) for value in usersite if isinstance(value, str))
    paths.update(Path(value or os.getcwd()) for value in sys.path)
    # Existing .pth files can name a currently absent directory. It is not in
    # sys.path yet, but becomes an import root after a writable grant creates it.
    paths.update(_pth_targets(_budget=budget))
    # Protect the lexical path, its target and every intermediate symlink: a
    # writable intermediate component can redirect a later resolution without
    # touching either endpoint.
    roots = set()
    for path in paths:
        roots.update(_expanded(path, _budget=budget))
    return roots


def startup_roots(*, _budget=None):
    """User startup authority, even when absent or redirected outside Home.

    Home itself is not a protected subtree: its ordinary document children must
    remain writable. Its startup files make a writable Home ancestor unsafe.
    """
    budget = _budget or _Budget()
    home = os.environ.get('HOME')
    if not home:
        import pwd
        home = pwd.getpwuid(os.getuid()).pw_dir
    home = Path(home)
    paths = {home / name for name in ('.local', '.config', '.ssh', '.profile',
             '.bashrc', '.bash_profile', '.bash_login', '.bash_logout', '.zshenv',
             '.zshrc', '.zprofile', '.zlogin', '.zlogout', '.xinitrc', '.xsession',
             '.xsessionrc', '.pam_environment')}
    userbase = site.getuserbase()
    if userbase:
        paths.add(Path(userbase))
    for variable in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME'):
        value = os.environ.get(variable, '')
        if Path(value).is_absolute():
            paths.add(Path(value))
    zdot = os.environ.get('ZDOTDIR')
    if zdot:
        paths.update(Path(zdot) / name for name in ('.zshenv', '.zshrc', '.zprofile', '.zlogin', '.zlogout'))
    # Host executables can live outside ~/.local/bin; protect actual PATH inputs.
    paths.update(Path(value or os.getcwd()) for value in os.get_exec_path())
    # Retain both sides of redirected startup paths and every intermediate
    # symlink: a writable parent can replace the symlink itself.
    roots = set()
    for path in paths:
        roots.update(_expanded(path, _budget=budget))
    return roots


def _refuse(message):
    raise Failure('mcp_runtime_protection', message)


def validate_write_roots(root, writes, *, runtime=None):
    budget = _Budget()
    protected = execution_roots(root, runtime, _budget=budget) | startup_roots(_budget=budget)
    _check_write_roots(writes, protected)


def _check_write_roots(writes, protected):
    for value in writes:
        path = Path(value).resolve()
        if any(_overlaps(path, code) for code in protected):
            _refuse('This writable folder includes application code, a Python runtime or '
                    'desktop/shell startup files. Share it read-only and choose a separate '
                    'document folder for writing. No tool was started.')


def validate_runtime_writes(row, root, *, runtime=None, check_links=True, _cancelled=None):
    if sys.platform != 'linux' or row.get('transport', 'stdio') != 'stdio':
        return
    shared = row.get('shared_folders')
    writes = (row.get('write_paths', []) if shared is None else
              [grant['path'] for grant in shared if grant['access'] == 'write'])
    budget = _Budget(_cancelled)
    protected = execution_roots(root, runtime, _budget=budget)
    _check_write_roots(writes, protected | startup_roots(_budget=budget))
    cache = row.get('cache')
    if cache:
        path = Path(cache).resolve()
        installation = Path(root).resolve()
        # The host owns this dedicated cache, never imports it, and may keep it
        # below the installation/private archive. It cannot alias Python inputs.
        for code in protected:
            if _within(code, path) or code != installation and _within(path, code):
                _refuse('The connection cache overlaps Kejtu’s Python runtime. '
                        'Repair this connection before using it. No tool was started.')
    if check_links:
        _validate_links(row, writes, cache, _budget=budget)
    budget.check()


def _validate_links(row, writes, cache, *, _budget=None):
    # A pre-existing inode alias can bypass path mounts. Count every writable
    # name of a linked inode, refusing any reference outside that write view.
    # Cache links must be contained within that cache independently: a later
    # connection may reuse the cache with a different document permission set.
    budget = _budget or _Budget()
    private = [Path(value) for value in row.get('private_paths', [])]
    dependencies = [Path(value) for value in row.get('read_paths', [])]
    shared = row.get('shared_folders')
    grants = sorted(shared or [], key=lambda grant: len(Path(grant['path']).parts))
    desktop = [Path(value) for value in row.get('desktop_private_paths', {}).values()]

    def masked(path, cached):
        masks = desktop if cached else private + (dependencies if shared is not None else [])
        return any(_within(path, value) for value in masks)

    def writable(path, cached):
        if masked(path, cached):
            return False
        if cached or shared is None:
            return True  # Legacy write mounts follow read mounts.
        mode = None
        for grant in grants:
            if _within(path, Path(grant['path'])):
                mode = grant['access']
        return mode == 'write'

    for roots, cached in ((writes, False), ([cache] if cache else [], True)):
        seen, walked, linked = set(), set(), {}
        # Visit every explicit write root. A RO/private subtree is never walked;
        # an explicit write below a RO parent is reached directly instead. Seen
        # names and walked directories keep overlapping grants from double counts.
        for value in sorted(set(roots), key=lambda value: len(Path(value).parts)):
            budget.check()
            path = Path(value)
            if str(path.resolve()) != value or not path.is_dir():
                _unavailable()
            pending = [path]
            while pending:
                parent = pending.pop()
                if parent in walked or not writable(parent, cached):
                    continue
                walked.add(parent)
                if parent not in seen:
                    seen.add(parent)
                    budget.entry()
                try:
                    with os.scandir(parent) as entries:
                        for entry in entries:
                            budget.check()
                            item = Path(entry.path)
                            if item in seen or not writable(item, cached):
                                continue
                            seen.add(item)
                            budget.entry()
                            info = entry.stat(follow_symlinks=False)
                            if stat.S_ISDIR(info.st_mode):
                                pending.append(item)
                            elif stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
                                key = (info.st_dev, info.st_ino)
                                expected, names = linked.setdefault(key, (info.st_nlink, []))
                                if expected != info.st_nlink:
                                    _unavailable()
                                names.append(item)
                except OSError:
                    _unavailable()
        for key, (expected, names) in linked.items():
            if len(names) != expected:
                _refuse('A writable folder or connection cache contains hard links to '
                        'files outside its writable view. Use independent file copies '
                        'or read-only access. No tool was started.')
            for item in names:
                budget.check()
                try:
                    info = item.lstat()
                except OSError:
                    _unavailable()
                if (info.st_dev, info.st_ino, info.st_nlink) != (*key, expected):
                    _unavailable()
