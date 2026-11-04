"""Relocatable identities for the reviewed Windows connection runtimes.

These are trusted programs, not filesystem/network sandboxes. Only fixed
packaged executables use this route; imports cannot supply a replacement path,
arguments or environment. Exact tool grants remain owned by Connections.
"""
from pathlib import Path
import sys

PREFIX = '@kejtu/'
PROGRAMS = {
    'gridcraft': 'gridcraft/gridcraft-cli.exe',
    'pdfcraft': 'pdfcraft/pdfcraft-cli.exe',
    'wordcraft': 'wordcraft/wordcraft-cli.exe',
}


def known(command):
    return command in {PREFIX + name for name in ('browser', *PROGRAMS)}


def validate(row):
    if (row.get('transport', row.get('type', 'stdio')) != 'stdio'
            or not known(row.get('command')) or row.get('args')
            or row.get('env') or row.get('read_paths') or row.get('write_paths')
            or row.get('network') is not True):
        raise ValueError('Packaged Windows connections use fixed settings and normal Windows account access.')


def installed(root, relative):
    root = Path(root).resolve()
    target = root / relative
    if not target.is_file():
        raise ValueError('The packaged connection runtime is missing. Repair the Kejtu installation.')
    for part in (target, *target.parents):
        if part == root:
            break
        if part.is_symlink() or part.is_junction():
            raise ValueError('The packaged connection runtime path has changed.')
    return target


def resolve(root, row):
    validate(row)
    name = row['command'][len(PREFIX):]
    if name == 'browser':
        installed(root, 'package-runtime/browser/interactive/chrome-headless-shell-win64/chrome-headless-shell.exe')
        installed(root, 'package-runtime/browser/mcp/node_modules/@playwright/mcp/cli.js')
        installed(root, '.venv/Scripts/site-packages/playwright/driver/node.exe')
        installed(root, 'src/kejtu_next/adapters/web/mcp_browser.py')
        return sys.executable, ['-B', '-m', 'kejtu_next.adapters.web.mcp_browser']
    executable = installed(root, 'package-runtime/craft/' + PROGRAMS[name])
    return str(executable), ['mcp', *(['--author', 'Kejtu'] if name == 'wordcraft' else [])]
