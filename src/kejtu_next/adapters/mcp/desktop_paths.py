"""Linux desktop storage used by the pinned Tauri application."""
import os
from pathlib import Path


# Keep aligned with the native identifier; a control checks tauri.conf.json.
DESKTOP_ID = 'dev.kejtu.next.development'


def linux_desktop_paths():
    """Match Tauri 2.11.5 / dirs 6: absolute XDG bases, otherwise HOME.

    Return native paths before resolving links so the worker can recheck their
    identity. Merely inspecting Files must never create desktop directories.
    """
    home = os.environ.get('HOME')
    if not home:
        import pwd
        home = pwd.getpwuid(os.getuid()).pw_dir
    roots = []
    for variable, fallback in (('XDG_DATA_HOME', '.local/share'),
                               ('XDG_CONFIG_HOME', '.config')):
        value = os.environ.get(variable, '')
        base = Path(value) if Path(value).is_absolute() else Path(home) / fallback
        roots.append((base / DESKTOP_ID).absolute())
    return tuple(roots)
