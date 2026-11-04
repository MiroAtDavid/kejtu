"""Install this checkout's pinned Files runtime without granting folder access."""
import argparse
import fcntl
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.setup_runtime import install_directory, install_sdk, run


def check_runtime(directory, source):
    package = directory / 'node_modules/@modelcontextprotocol/server-filesystem'
    expected = json.loads((source / 'package.json').read_text())['dependencies']['@modelcontextprotocol/server-filesystem']
    if ((directory / 'package-lock.json').read_bytes() != (source / 'package-lock.json').read_bytes()
            or json.loads((package / 'package.json').read_text()).get('version') != expected
            or not (package / 'dist/index.js').is_file()
            or (package / 'LICENSE').read_bytes() != (source / 'LICENSE').read_bytes()):
        raise ValueError('The Files runtime is incomplete or belongs to another release. It was not replaced.')
    run(['/usr/bin/node', '--check', package / 'dist/index.js'])


def install(root=ROOT):
    root = Path(root).resolve()
    if sys.platform != 'linux' or not (root / '.venv/bin/python').is_file():
        raise ValueError('Files setup requires Linux and this checkout’s .venv. Complete the README dependency installation first.')
    for command in ('node', 'npm', 'bwrap'):
        if not shutil.which(command, path='/usr/bin:/bin'):
            raise ValueError(f'Install {command} with your Linux package manager, then run ./kejtu setup-files again.')
    state = root / '.state'
    runtime = state / 'mcp-runtime'
    if state.is_symlink() or runtime.is_symlink():
        raise ValueError('Files runtime directories must be real directories in this checkout.')
    state.mkdir(mode=0o700, exist_ok=True)
    runtime.mkdir(mode=0o700, exist_ok=True)
    source = root / 'ops/mcp/filesystem'
    destination = runtime / 'filesystem'
    # Files and web share one SDK; simultaneous setup must not publish competing
    # runtime directories. Neither installer reads or writes access settings.
    with (state / 'runtime-setup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        install_sdk(root)

        def populate(target):
            for name in ('package.json', 'package-lock.json'):
                shutil.copy2(source / name, target / name)
            run(['/usr/bin/npm', 'ci', '--prefix', target, '--omit=dev',
                 '--ignore-scripts', '--no-audit', '--no-fund'])
            # The pinned npm tarball omits its license; retain the exact upstream
            # notice shipped with this release, as the low-level installer does.
            shutil.copy2(source / 'LICENSE',
                         target / 'node_modules/@modelcontextprotocol/server-filesystem/LICENSE')
            check_runtime(target, source)

        print('Checking the pinned Files component…', flush=True)
        install_directory(destination, populate)
        check_runtime(destination, source)
    print('Files is ready. Refresh Settings → Files and save your folder choices to grant access.', flush=True)
    return dict(files=True)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        install()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f'Files setup did not finish: {error}\nFix the reported dependency and rerun ./kejtu setup-files.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
