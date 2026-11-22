"""Where an installation keeps its program files and its private data.

A source checkout holds both: program files in the checkout and private data in
its `.state/`. An installed Linux package keeps its program files read-only and
its private data in the folder that holds `runtime/` (normally
~/.local/share/kejtu). The native launcher verifies the package manifest that
marks such an installation before it starts the backend.
"""
import os
from pathlib import Path
import sys

MARKER = 'kejtu-package.json'


def program_root():
    return Path(__file__).resolve().parents[4]


def installed(root=None):
    return sys.platform == 'linux' and (Path(root or program_root()) / MARKER).is_file()


def private_data(state_dir, root=None):
    """Backups, launch inputs, package trees and the archive's own folder."""
    root = Path(root or program_root())
    return Path(state_dir).resolve().parent if installed(root) else root / '.state'


def installed_data(environ=None):
    """The installed package's private data folder; the same rule as the native
    launcher (package_startup.rs, LINUX layout): an absolute XDG_DATA_HOME, else
    ~/.local/share, resolved once, then kejtu.
    """
    environ = os.environ if environ is None else environ
    value = environ.get('XDG_DATA_HOME', '')
    if Path(value).is_absolute():
        base = Path(value)
    else:
        home = environ.get('HOME', '')
        if not Path(home).is_absolute():
            import pwd
            home = pwd.getpwuid(os.getuid()).pw_dir
        base = Path(home) / '.local/share'
    return base.resolve() / 'kejtu'


def state_folder(root=None):
    """Where the archive lives: the checkout's .state/runtime, or runtime in the
    installed data folder. Its parent holds backups, launch inputs and operations."""
    root = Path(root or program_root())
    return installed_data() / 'runtime' if installed(root) else root / '.state/runtime'
