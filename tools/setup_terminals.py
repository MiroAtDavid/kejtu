"""Stage the two optional Linux terminal programs without changing any connection settings."""
import argparse
import fcntl
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from kejtu_next.adapters.artifacts import real_directory, tree_hash
from kejtu_next.adapters.mcp.terminals import DIRECTORY
from tools.setup_runtime import install_directory, run
from tools.terminal_development.prepare import adapt


def verify(directory, root):
    source = root / 'ops/mcp/terminals'
    program = directory / 'terminal-use'
    package = program / 'node_modules/terminal-use'
    quick = directory / 'quick-terminal/quick_terminal.py'
    if (quick.resolve() != quick
            or quick.read_bytes() != (source / 'quick_terminal.py').read_bytes()
            or (program / 'package-lock.json').read_bytes() != (source / 'package-lock.json').read_bytes()
            or (package / 'dist/mcp/tools/commands.js').read_bytes() !=
                (root / 'tools/terminal_development/commands.js').read_bytes()
            or not all((package / name).is_file() for name in ('LICENSE', 'fonts/OFL.txt', 'bin/terminal-use.js'))
            or tree_hash(program) != (directory / 'terminal-use.sha256').read_text().strip()):
        raise ValueError(f'Terminal runtime at {directory} differs from this release. Move it aside before repairing it.')


def install(root=ROOT):
    root = Path(root).resolve()
    if sys.platform != 'linux':
        raise ValueError('The bundled terminal programs require Linux.')
    state = real_directory(root / '.state')
    runtime = real_directory(state / 'mcp-runtime')
    if not (runtime / 'python/mcp').is_dir():
        raise ValueError('Prepare the shared MCP Python runtime with setup_files.py first.')
    source = root / 'ops/mcp/terminals'
    destination = runtime / DIRECTORY

    def populate(target):
        program = target / 'terminal-use'
        program.mkdir()
        for name in ('package.json', 'package-lock.json'):
            shutil.copy2(source / name, program / name)
        run(['/usr/bin/npm', 'ci', '--prefix', program, '--omit=dev', '--ignore-scripts',
             '--no-bin-links', '--no-audit', '--no-fund'])
        adapt(program / 'node_modules/terminal-use')
        quick = target / 'quick-terminal'
        quick.mkdir()
        shutil.copy2(source / 'quick_terminal.py', quick / 'quick_terminal.py')
        (target / 'terminal-use.sha256').write_text(tree_hash(program) + '\n')
        verify(target, root)

    with (state / 'runtime-setup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        install_directory(destination, populate)
        verify(destination, root)
    print('Terminal Use and Quick Terminal are ready for individual setup on the Shelf.')


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        install()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f'Terminal setup did not finish: {error}')
