"""Keep source builds and desktop launches on the same Linux toolchain."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

SYSTEM_PATH = '/usr/bin:/bin'


def environment(base=None):
    """Select Rust from the caller, while requiring Kejtu's system Node/npm."""
    result = dict(os.environ if base is None else base)
    search = result.get('PATH', os.defpath)
    selected = {}
    for name in ('cargo', 'rustc'):
        command = shutil.which(name, path=search)
        if command is None:
            raise ValueError(f'{name} is unavailable. Install the source dependencies before launching Kejtu.')
        # Preserve proxy names: resolving a rustup symlink to "rustup" would
        # change which command it dispatches. These are executable paths, not IDs.
        selected[name] = str(Path(command).absolute())
    directories = list(dict.fromkeys([str(Path(command).parent) for command in selected.values()]
                                    + SYSTEM_PATH.split(':')))
    result['PATH'] = os.pathsep.join(directories)
    for name in ('node', 'npm'):
        expected = Path('/usr/bin') / name
        actual = shutil.which(name, path=result['PATH'])
        if actual is None or not expected.is_file() or Path(actual).resolve() != expected.resolve():
            raise ValueError(f'The selected Rust tool directory shadows system {name}. '
                             'Use a Rust-only toolchain directory alongside /usr/bin:/bin, then retry.')
    for name, expected in selected.items():
        actual = shutil.which(name, path=result['PATH'])
        if actual is None or str(Path(actual).absolute()) != expected:
            raise ValueError('Cargo and rustc resolve to conflicting toolchain directories. '
                             'Select one consistent Rust toolchain in PATH, then retry.')
    result['CARGO'], result['RUSTC'] = selected['cargo'], selected['rustc']
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inspect', 'launch'))
    args = parser.parse_args(argv)
    try:
        if sys.platform != 'linux':
            raise ValueError('This source toolchain helper is for Linux only.')
        selected = environment()
        if args.action == 'inspect':
            print(json.dumps({key: selected[key] for key in ('PATH', 'CARGO', 'RUSTC')}, indent=2))
            return 0
        root = Path(__file__).resolve().parents[1]
        os.chdir(root / 'desktop')
        os.execve('/usr/bin/npm', ['npm', 'run', 'tauri', 'dev', '--', '--no-watch'], selected)
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
