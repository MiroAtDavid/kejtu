#!/usr/bin/env python3
"""Plan or install Kejtu's Linux source dependencies at this checkout's final path."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import signal
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('kejtu_linux_toolchain', ROOT / 'tools/linux_toolchain.py')
_toolchain = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_toolchain)
_spec = importlib.util.spec_from_file_location('kejtu_source_apparmor', ROOT / 'packaging/source/apparmor.py')
_apparmor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_apparmor)
SYSTEM_PATH = _toolchain.SYSTEM_PATH
# Native packages follow Tauri's Linux prerequisites; Chromium uses the libraries
# listed by Playwright. Package managers remain the authority for transitive deps.
PACKAGES = {
    'ubuntu': '''python3 python3-venv python3-dev nodejs npm rustc cargo
        build-essential pkg-config libwebkit2gtk-4.1-dev libssl-dev
        libayatana-appindicator3-dev librsvg2-dev patchelf curl file git
        bubblewrap poppler-utils libsecret-1-0 libsecret-tools gnome-keyring apparmor
        libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libatspi2.0-0t64
        libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3
        libxrandr2 libxkbcommon0 libdrm2 libgbm1 libasound2t64 libcups2t64
        libcairo2 libpango-1.0-0 libwayland-client0 fonts-liberation'''.split(),
    'manjaro': '''python nodejs npm rust base-devel pkgconf webkit2gtk-4.1
        openssl appmenu-gtk-module libappindicator-gtk3 librsvg patchelf curl
        file git bubblewrap poppler libsecret gnome-keyring nss nspr at-spi2-core
        libx11 libxcb libxcomposite libxdamage libxext libxfixes libxrandr
        libxkbcommon libdrm mesa alsa-lib libcups cairo pango wayland ttf-liberation'''.split(),
}
TOOLCHAIN = {'node': (22, 12, 0), 'rustc': (1, 93, 0), 'cargo': (1, 93, 0)}


class InstallError(RuntimeError):
    pass


def process_group(group):
    rows = {}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == group and int(fields[3]) == group and fields[0] != 'Z':
                rows[int(path.name)] = fields[19]
        except (OSError, ValueError, IndexError):
            pass
    return rows


@contextmanager
def finishing_command():
    # A second interrupt must not release the install lock during child cleanup.
    saved = {kind: signal.signal(kind, signal.SIG_IGN) for kind in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for kind, handler in saved.items():
            signal.signal(kind, handler)


def clean_group(process, leader_start):
    for sig, seconds in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
        members = process_group(process.pid)
        if not members:
            return
        if process.pid in members and members[process.pid] != leader_start:
            raise InstallError('The build process identity changed; no unrelated process was signalled.')
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        until = time.monotonic() + seconds
        while process_group(process.pid) and time.monotonic() < until:
            time.sleep(.05)
    if process_group(process.pid):
        raise InstallError('A build child did not stop. Check the reported build process before rerunning.')


def run(args, *, cwd=None, env=None, capture=False):
    args = [str(value) for value in args]
    environment = dict(os.environ if env is None else env)
    # Dependency bootstrap can run before Rust is installed. Build commands pass
    # the complete shared toolchain environment once prerequisites are present.
    if env is None:
        environment['PATH'] = SYSTEM_PATH
    for variable in ('PYTHONPATH', 'PYTHONHOME', 'NODE_OPTIONS', 'PIP_TARGET', 'PIP_PREFIX'):
        environment.pop(variable, None)
    if not capture:
        print('+ ' + shlex.join(args), flush=True)
    privileged = args[0] == 'sudo'
    process = subprocess.Popen(args, cwd=cwd, env=environment, text=True,
                               stdout=subprocess.PIPE if capture else None,
                               start_new_session=not privileged)
    leader_start = process_group(process.pid).get(process.pid) if not privileged else None
    try:
        output, _ = process.communicate()
    except BaseException:
        with finishing_command():
            if privileged:
                print('Waiting for the current privileged system operation to finish safely before stopping…', flush=True)
            else:
                clean_group(process, leader_start)
            process.communicate()
        raise
    finally:
        if not privileged:
            with finishing_command():
                clean_group(process, leader_start)
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, args, output=output)
    return subprocess.CompletedProcess(args, process.returncode, output)


def distribution(info=None):
    info = info if info is not None else platform.freedesktop_os_release()
    name = info.get('ID')
    if name == 'ubuntu' and info.get('VERSION_ID') == '26.04':
        return name
    if name == 'manjaro':
        return name
    raise InstallError('Supported source-install targets are Ubuntu 26.04 and Manjaro x86_64.')


def package_installed(distro, package):
    args = (['dpkg-query', '-W', '-f=${db:Status-Status}', package] if distro == 'ubuntu'
            else ['pacman', '-Q', package])
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return result.returncode == 0 and (distro != 'ubuntu' or result.stdout == 'installed')


def dependencies(distro, media=False):
    packages = list(PACKAGES[distro])
    if media:
        packages.append('mpv')
    return packages


def compatible_rust():
    for tool in ('rustc', 'cargo'):
        path = shutil.which(tool)
        if path is None:
            return False
        result = subprocess.run([path, '--version'], text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        match = re.search(r'(\d+)\.(\d+)\.(\d+)', result.stdout)
        if result.returncode or not match or tuple(map(int, match.groups())) < TOOLCHAIN[tool]:
            return False
    return True


def missing_packages(distro, media=False):
    rust_ready = compatible_rust()
    return [package for package in dependencies(distro, media)
            if not (rust_ready and package in ('rust', 'rustc', 'cargo'))
            and not package_installed(distro, package)]


def package_commands(distro, missing):
    if not missing:
        return []
    if distro == 'ubuntu':
        return [['sudo', 'apt-get', 'update'],
                ['sudo', 'apt-get', 'install', '--no-remove', '--no-upgrade', '-y', *missing]]
    # Never perform a partial rolling-distribution upgrade with -Sy. If the
    # local database is stale, pacman fails and the user updates the OS normally.
    return [['sudo', 'pacman', '-S', '--needed', '--noconfirm', *missing]]


def real_directory(path):
    """Reject redirected installer destinations before any write or mkdir."""
    path = Path(path)
    for item in (path, *path.parents):
        if item.is_symlink():
            raise InstallError(f'Installation paths must not be symlinks: {item}')
    if path.exists() and not path.is_dir():
        raise InstallError(f'Expected a directory: {path}')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


@contextmanager
def lock_file(path):
    real_directory(path.parent)
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
    with os.fdopen(os.open(path, flags, 0o600), 'a+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise InstallError('Another install or managed app operation is running. Try again after it finishes.') from error
        yield


def require_stopped(root):
    spec = importlib.util.spec_from_file_location('kejtu_source_control', root / 'ops/control.py')
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    if control.owned():
        raise InstallError('Save drafts and stop this installation with ./kejtu stop --composer-empty before installing.')
    with socket.socket() as probe:
        probe.settimeout(.3)
        if probe.connect_ex(('127.0.0.1', 1431)) == 0:
            raise InstallError('Kejtu port 1431 is occupied. Close the running app before installation.')


def check_toolchain():
    python = Path('/usr/bin/python3')
    data = json.loads(run([python, '-c', 'import json,sys,ensurepip; print(json.dumps(list(sys.version_info[:3])))'], capture=True).stdout)
    if tuple(data) < (3, 14, 0):
        raise InstallError('Python 3.14 or newer with venv/ensurepip is required. Update the OS packages first.')
    for tool, minimum in TOOLCHAIN.items():
        command = shutil.which(tool, path=SYSTEM_PATH if tool == 'node' else None)
        if not command:
            raise InstallError(f'{tool} is missing after dependency installation.')
        version = run([command, '--version'], capture=True).stdout
        match = re.search(r'(\d+)\.(\d+)\.(\d+)', version)
        if not match or tuple(map(int, match.groups())) < minimum:
            raise InstallError(f'{tool} {".".join(map(str, minimum))}+ is required; found {version.strip()}. Update the toolchain first.')
    for tool in ('npm', 'bwrap', 'pdfinfo', 'pdftotext', 'pdftoppm', 'secret-tool'):
        if not shutil.which(tool, path=SYSTEM_PATH):
            raise InstallError(f'The required system command {tool} is missing.')
    run(['pkg-config', '--exists', 'gtk+-3.0', 'webkit2gtk-4.1', 'openssl'])
    run(['/usr/bin/bwrap', '--ro-bind', '/', '/', '--unshare-user', '--unshare-net', '--', '/usr/bin/true'])
    return python


def venv(root, destination, python):
    real_directory(destination.parent)
    marker = real_directory(root / '.state/source-install') / (destination.name + '-creating.json')
    if destination.is_symlink():
        raise InstallError(f'Virtual environment is a symlink: {destination}')
    if not destination.exists() and not marker.exists():
        with marker.open('x') as stream:
            json.dump({'path': str(destination)}, stream)
    if marker.exists():
        if marker.is_symlink() or json.loads(marker.read_text()) != {'path': str(destination)}:
            raise InstallError(f'Unexpected virtual-environment recovery marker: {marker}')
        # venv is created at its final path: pip scripts embed this location.
        # Repeating creation without --clear repairs our interrupted bootstrap.
        run([python, '-m', 'venv', destination])
    elif not (destination / 'pyvenv.cfg').is_file():
        raise InstallError(f'Existing directory is not a known virtual environment: {destination}. It was preserved.')
    interpreter = destination / 'bin/python'
    code = 'import json,sys; print(json.dumps({"version":list(sys.version_info[:3]),"prefix":sys.prefix}))'
    details = json.loads(run([interpreter, '-c', code], capture=True).stdout)
    if tuple(details['version']) < (3, 14, 0) or Path(details['prefix']) != destination:
        raise InstallError(f'Existing virtual environment is incompatible or moved: {destination}. It was preserved.')
    run([interpreter, '-m', 'ensurepip'])
    if marker.exists():
        marker.unlink()
    return interpreter


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def tree_hash(root):
    records = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise InstallError(f'Unexpected runtime entry: {path}')
        if path.is_file():
            records.append([path.relative_to(root).as_posix(), path.stat().st_size,
                            bool(path.stat().st_mode & 0o111), file_hash(path)])
    return hashlib.sha256(json.dumps(records, separators=(',', ':')).encode()).hexdigest()


def check_craft(destination, item):
    if tree_hash(destination) != item['tree_sha256']:
        raise InstallError(f'Installed {item["id"]} differs from the pinned complete runtime; preserved at {destination}. Restore it from a backup or move it aside before retrying.')
    run([destination / item['executable'], '--version'])


def download(cache, item):
    path = cache / item['archive']
    if path.is_symlink():
        raise InstallError(f'Archive cache is a symlink: {path}')
    if path.exists():
        if path.stat().st_size != item['bytes'] or file_hash(path) != item['sha256']:
            raise InstallError(f'Cached archive failed integrity verification: {path}. Move it aside and retry.')
        return path
    with tempfile.NamedTemporaryFile(dir=cache, prefix='.download-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            request = urllib.request.Request(item['url'], headers={'User-Agent': 'Kejtu-source-installer'})
            with urllib.request.urlopen(request, timeout=60) as response:
                if not response.url.startswith('https://'):
                    raise InstallError('Runtime download redirected outside HTTPS.')
                copied = 0
                while chunk := response.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > item['bytes']:
                        raise InstallError(f'{item["id"]} download exceeds the pinned size.')
                    stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
            if copied != item['bytes'] or file_hash(temporary) != item['sha256']:
                raise InstallError(f'{item["id"]} download failed integrity verification.')
            temporary.rename(path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def extract_craft(archive, staging, item):
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        total = 0
        for member in members:
            parts = PurePosixPath(member.name).parts
            if (not parts or parts[0] != item['directory'] or '..' in parts
                    or PurePosixPath(member.name).is_absolute()
                    or not (member.isdir() or member.isfile())):
                raise InstallError('Runtime archive contains an unexpected path or entry type.')
            total += member.size
            if total > item['unpacked_bytes']:
                raise InstallError('Runtime archive exceeds the pinned extracted size.')
        source.extractall(staging, members=members, filter='data')
    destination = staging / item['directory']
    check_craft(destination, item)
    return destination


def install_craft(root, lock):
    cache = real_directory(root / '.state/source-install/downloads')
    for item in lock['craft']:
        parent = real_directory(root / '.state/apps' / item['id'])
        destination = parent / item['directory']
        if destination.is_symlink():
            raise InstallError(f'Runtime destination is a symlink: {destination}')
        if destination.exists():
            check_craft(destination, item)
            continue
        print(f'Installing {item["id"]} {item["version"]}, including upstream notices…', flush=True)
        archive = download(cache, item)
        with tempfile.TemporaryDirectory(prefix='.install-', dir=parent) as temporary:
            prepared = extract_craft(archive, Path(temporary), item)
            prepared.rename(destination)


def build(root, python, *, media, jobs, lock, browser_policy=False, allow_system=True):
    env = _toolchain.environment()
    interpreter = venv(root, root / '.venv', python)
    run([interpreter, '-m', 'pip', 'install', '--disable-pip-version-check', '-r', root / 'ops/requirements.lock'], env=env)
    run([interpreter, '-m', 'pip', 'install', '--no-deps', '--no-build-isolation', '-e', root], env=env)
    run([interpreter, '-m', 'pip', 'check'], env=env)
    run(['/usr/bin/npm', 'ci', '--no-audit', '--no-fund'], cwd=root / 'desktop', env=env)
    run([interpreter, '-B', root / 'tools/setup_files.py'], cwd=root, env=env)
    if browser_policy:
        receipt = root / '.state/source-install/web-prepared.json'
        run([interpreter, '-B', root / 'tools/setup_web.py', '--prepare-only', '--receipt', receipt], cwd=root, env=env)
        _apparmor.ensure(root, receipt, run, allow_system=allow_system, env=env)
    run([interpreter, '-B', root / 'tools/setup_web.py'], cwd=root, env=env)
    install_craft(root, lock)
    if media:
        media_python = venv(root, root / '.state/video-tools', python)
        run([media_python, '-m', 'pip', 'install', '--disable-pip-version-check', '-r', root / 'ops/private-video-requirements.lock'], env=env)
        run([media_python, '-m', 'pip', 'check'], env=env)
    run(['/usr/bin/npm', 'run', 'check'], cwd=root / 'desktop', env=env)
    run(['/usr/bin/npm', 'run', 'build'], cwd=root / 'desktop', env=env)
    env['CARGO_TARGET_DIR'] = str(root / '.state/build/rust')
    env['CARGO_BUILD_JOBS'] = str(jobs)
    run([env['CARGO'], 'build', '--locked', '--manifest-path', root / 'desktop/src-tauri/Cargo.toml'], cwd=root, env=env)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', help='Install missing packages and build; otherwise print a read-only plan.')
    parser.add_argument('--media', action='store_true', help='Also install mpv and the pinned video extraction environment.')
    parser.add_argument('--no-system-packages', action='store_true', help='Require system dependencies to be installed already; never call sudo.')
    parser.add_argument('--jobs', type=int, default=2, help='Concurrent native build jobs (default: 2).')
    args = parser.parse_args(argv)
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    try:
        if sys.platform != 'linux' or platform.machine() != 'x86_64':
            raise InstallError('This installer supports Linux x86_64 only.')
        if not 1 <= args.jobs <= 64:
            raise InstallError('--jobs must be between 1 and 64.')
        distro = distribution()
        lock = json.loads((ROOT / 'packaging/source/runtime.lock.json').read_text())
        missing = missing_packages(distro, args.media)
        print(f'Installation path: {ROOT}\nSystem: {distro}; build jobs: {args.jobs}')
        print('Missing OS packages: ' + (', '.join(missing) or 'none'))
        print('Required downloads: locked Python/npm/Rust dependencies, Files, two matched Chromium engines, and Craft document runtimes (202 MB compressed).')
        print('Optional media: ' + ('mpv and locked yt-dlp environment selected.' if args.media else 'not selected; add --media for radio/video.'))
        print('No model is downloaded. GPU embeddings require the separately documented compatible Ollama/model setup.')
        browser_policy = _apparmor.required(distro)
        print('OS packages and the exact-path Ubuntu browser policy use sudo; app files and runtimes belong to your normal user.')
        if browser_policy:
            print('Ubuntu restricts browser user namespaces. Install/verify a per-checkout AppArmor profile for the two pinned browser paths; keep the global restriction and both browser sandboxes enabled.')
        for command in package_commands(distro, missing):
            print(shlex.join(command))
        if not args.install:
            print('No changes made. Add --install to proceed from this final installation path.')
            return 0
        if os.geteuid() == 0:
            raise InstallError('Run the installer as your desktop user, without sudo. It requests sudo for OS packages and the Ubuntu browser policy.')
        if missing and args.no_system_packages:
            raise InstallError('Required OS packages are missing; --no-system-packages made no changes.')
        with lock_file(ROOT / '.state/source-install/install.lock'):
            with lock_file(ROOT / '.state/operations/control.lock'):
                require_stopped(ROOT)
                for command in package_commands(distro, missing):
                    run(command)
                python = check_toolchain()
                build(ROOT, python, media=args.media, jobs=args.jobs, lock=lock,
                      browser_policy=browser_policy, allow_system=not args.no_system_packages)
        print('Installation finished. Saved data, credentials and permissions were not reset.')
        print('Next: ./kejtu start' if (ROOT / '.state/runtime/core.sqlite3').is_file() else 'Next: ./kejtu initialize')
        return 0
    except (InstallError, OSError, ValueError, KeyError, subprocess.CalledProcessError, tarfile.TarError) as error:
        print(f'Installation stopped: {error}\nCorrect the reported problem and rerun the same command. Existing app data was preserved.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nInstallation interrupted. Rerun the same command to continue; existing app data was preserved.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
