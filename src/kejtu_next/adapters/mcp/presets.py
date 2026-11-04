"""Configuration for the reviewed document runtimes, never an installer.

Linux uses the existing isolated Craft layouts. Windows delegates to its
packaged-runtime owner. Neither route creates a personal folder grant.
"""
import os
from pathlib import Path
import sys

from .config import connection
from .files import SHARED_ROOT
from ...core.records import Failure


DOCUMENTS = {
    'gridcraft': ('0.6.1', 'Create and edit spreadsheets.'),
    'pdfcraft': ('0.7.1', 'Read, edit and export PDFs.'),
    'wordcraft': ('0.6.1', 'Create and edit documents.'),
}


def document(root, name):
    if name not in DOCUMENTS:
        raise Failure('mcp_configuration', 'Unknown included connection.')
    version, description = DOCUMENTS[name]
    if sys.platform == 'win32':
        from .windows_connections import resolve
        raw = dict(command=f'@kejtu/{name}', network=True, description=description)
        try:
            resolve(root, raw)
        except (ValueError, OSError):
            raise Failure('mcp_installation', f'{name.title()} is missing from this installation. Repair the included document tools.') from None
        return connection(raw)
    if sys.platform != 'linux':
        raise Failure('mcp_installation', 'Included document tool setup is not available on this platform yet.')
    directory = Path(root) / '.state/apps' / name / f'{name}-{version}-linux-x86_64'
    executable = directory / 'bin' / f'{name}-cli'
    # Do not discover arbitrary programs on PATH or follow a replaced install
    # directory outside the application's explicit runtime area.
    if (not executable.is_file() or not os.access(executable, os.X_OK)
            or executable.resolve() != executable):
        raise Failure('mcp_installation', f'{name.title()} is not installed in this Kejtu runtime. Install the supported document tools first.')
    args = ['mcp']
    if name == 'pdfcraft':
        args += ['--root', SHARED_ROOT]
    elif name == 'wordcraft':
        args += ['--author', 'Kejtu', '--automation-read-root', SHARED_ROOT,
                 '--automation-write-root', SHARED_ROOT]
    return connection(dict(command=str(executable), args=args,
        description=description, network=False,
        read_paths=[str(directory if name == 'pdfcraft' else directory / 'bin')]))
