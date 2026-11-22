#!/usr/bin/env python3
"""Plan or install Kejtu's Linux source dependencies at this checkout's final path."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
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
import urllib.request  # the shared artifact module downloads through this same module

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('kejtu_linux_toolchain', ROOT / 'tools/linux_toolchain.py')
_toolchain = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_toolchain)
_spec = importlib.util.spec_from_file_location('kejtu_source_apparmor', ROOT / 'packaging/source/apparmor.py')
_apparmor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_apparmor)
_spec = importlib.util.spec_from_file_location('kejtu_linux_sandbox', ROOT / 'src/kejtu_next/adapters/linux_sandbox.py')
_sandbox = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sandbox)
_spec = importlib.util.spec_from_file_location('kejtu_artifacts', ROOT / 'src/kejtu_next/adapters/artifacts.py')
_artifacts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_artifacts)
_spec = importlib.util.spec_from_file_location('kejtu_source_desktop', ROOT / 'packaging/source/desktop.py')
_desktop = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_desktop)
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


@contextmanager
def _install_errors():
    """Report shared artifact refusals as installer errors, with the same text."""
    try:
        yield
    except _artifacts.ArtifactError as error:
        raise InstallError(str(error)) from error


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


def dependencies(distro):
    packages = list(PACKAGES[distro])
    if _sandbox.system_runtime() is None:
        packages += ['meson', 'ninja-build', 'libcap-dev'] if distro == 'ubuntu' else ['meson', 'ninja', 'libcap']
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


def missing_packages(distro):
    rust_ready = compatible_rust()
    return [package for package in dependencies(distro)
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
    with _install_errors():
        return _artifacts.real_directory(path)


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
    return _artifacts.file_hash(path)


def tree_hash(root):
    with _install_errors():
        return _artifacts.tree_hash(root)


def check_craft(destination, item):
    with _install_errors():
        _artifacts.verify_tree(destination, item)
    # An execution probe of the trusted pinned runtime, deliberately kept here
    # rather than in the data-only shared verification.
    run([destination / item['executable'], '--version'])


def download(cache, item):
    with _install_errors():
        return _artifacts.download(cache, item, user_agent='Kejtu-source-installer')


def cli_tree(destination, item):
    # Only the verified upstream release is eligible for trimming. Its CLI,
    # documentation, font notices and PDF OCR data stay at their original paths.
    if 'upstream_tree_sha256' in item:
        with _install_errors():
            _artifacts.verify_tree(destination, dict(item, tree_sha256=item['upstream_tree_sha256']))
        (destination / 'bin' / item['id']).unlink()
        for name in ('applications', 'icons', 'metainfo', 'mime'):
            component = destination / 'share' / name
            if component.exists():
                shutil.rmtree(component)
    check_craft(destination, item)
    return destination


def extract_craft(archive, staging, item):
    with _install_errors():
        destination = _artifacts.extract_archive(archive, staging, item)
    return cli_tree(destination, item)


def install_craft(root, lock):
    cache = real_directory(root / '.state/source-install/downloads')
    for item in lock['craft']:
        parent = real_directory(root / '.state/apps' / item['id'])
        destination = parent / item['directory']
        previous = parent / ('.' + item['directory'] + '.full')
        if destination.is_symlink() or previous.is_symlink():
            raise InstallError(f'Runtime destination is a symlink: {destination}')
        # A stopped source installation may still have the previous complete
        # release. Keep it recoverable across interruption until publication.
        if previous.exists() and tree_hash(previous) != item.get('upstream_tree_sha256'):
            raise InstallError(f'Unrecognized previous runtime preserved at {previous}.')
        if destination.exists() and not previous.exists() and 'upstream_tree_sha256' in item:
            if tree_hash(destination) == item['upstream_tree_sha256']:
                destination.rename(previous)
        if destination.exists():
            check_craft(destination, item)
        elif previous.exists():
            def prepare(temporary):
                copy = shutil.copytree(previous, temporary / item['directory'], copy_function=os.link)
                return cli_tree(copy, item)
            try:
                with _install_errors():
                    _artifacts.publish(destination, prepare)
                check_craft(destination, item)
            except Exception:
                if not destination.exists():
                    previous.rename(destination)
                raise
        else:
            print(f'Installing {item["id"]} {item["version"]} command-line tools and notices…', flush=True)
            archive = download(cache, item)
            with _install_errors():
                _artifacts.publish(destination, lambda temporary: extract_craft(archive, temporary, item))
        if previous.exists():
            with tempfile.TemporaryDirectory(prefix='.retired-', dir=parent) as temporary:
                previous.rename(Path(temporary) / 'full')


def extract_sandbox(archive, staging, item):
    """Extract the verified upstream source, materializing only its license aliases."""
    with tarfile.open(archive, 'r:xz') as source:
        members = source.getmembers()
        names, aliases, total = set(), {}, 0
        for member in members:
            parts = PurePosixPath(member.name).parts
            if (not parts or parts[0] != item['directory'] or '..' in parts
                    or PurePosixPath(member.name).is_absolute() or member.name in names):
                raise InstallError('Unexpected sandbox source archive path.')
            names.add(member.name)
            relative = PurePosixPath(*parts[1:]).as_posix()
            if member.issym():
                if item['license_aliases'].get(relative) != member.linkname or member.linkname != 'COPYING':
                    raise InstallError('Unexpected sandbox source link.')
                aliases[relative] = member.linkname
            elif not (member.isdir() or member.isfile()):
                raise InstallError('Unexpected sandbox source entry type.')
            total += member.size
        if total != item['unpacked_bytes'] or aliases != item['license_aliases']:
            raise InstallError('Sandbox source archive differs from its pinned layout.')
        source.extractall(staging, members=[m for m in members if not m.issym()], filter='data')
    directory = staging / item['directory']
    for alias in aliases:
        shutil.copyfile(directory / 'COPYING', directory / alias)
    if file_hash(directory / 'COPYING') != item['license_sha256']:
        raise InstallError('The upstream sandbox license failed verification.')
    return directory


def install_sandbox(root, lock, *, jobs=2):
    system = _sandbox.system_runtime()
    if system is not None:
        print(f'Reusing compatible system Bubblewrap {system["version"]}.', flush=True)
        return system
    item = lock['bubblewrap']
    parent = real_directory(root / '.state/apps/bubblewrap')
    destination = parent / item['directory']
    if destination.is_symlink():
        raise InstallError('The sandbox destination is redirected; preserved without replacement.')
    if destination.exists():
        return _sandbox.managed_runtime(root)
    cache = real_directory(root / '.state/source-install/downloads')
    archive = download(cache, item)
    print(f'Building Bubblewrap {item["version"]} once for both AppArmor launch identities…', flush=True)
    with tempfile.TemporaryDirectory(prefix='.install-', dir=parent) as temporary:
        staging = Path(temporary)
        source = extract_sandbox(archive, staging, item)
        build_dir = staging / 'build'
        meson = shutil.which('meson', path=SYSTEM_PATH)
        ninja = shutil.which('ninja', path=SYSTEM_PATH)
        if not meson or not ninja:
            raise InstallError('Meson and Ninja are required to build the pinned sandbox runtime.')
        # Build flags/compiler/pkg-config overrides must not redirect this
        # dependency to unrelated wrappers or introduce runtime library paths.
        env = dict(PATH=SYSTEM_PATH, HOME=str(Path.home()), LANG='C.UTF-8')
        run([meson, 'setup', build_dir, source, '--buildtype=release',
             # No SELinux: this managed copy serves AppArmor-restricted Ubuntu; SELinux
             # systems use their own bwrap. 'auto' linked libselinux wherever its
             # headers existed, which Arch does not ship.
             '-Dtests=false', '-Dman=disabled', '-Dselinux=disabled',
             '-Dbash_completion=disabled', '-Dzsh_completion=disabled'], env=env)
        run([ninja, '-C', build_dir, '-j', jobs], env=env)
        binary = build_dir / 'bwrap'
        version = run([binary, '--version'], env=env, capture=True).stdout.strip()
        if version != 'bubblewrap ' + item['version']:
            raise InstallError('The compiled sandbox reported an unexpected version.')
        published = staging / 'published'
        (published / 'bin').mkdir(parents=True)
        for role in _sandbox.ROLES:
            target = published / 'bin' / ('bwrap-' + role)
            shutil.copyfile(binary, target)
            target.chmod(0o700)
        shutil.copyfile(source / 'COPYING', published / 'COPYING')
        shutil.copyfile(archive, published / item['archive'])
        for path in (published / 'COPYING', published / item['archive']):
            path.chmod(0o644)
        files = {p.relative_to(published).as_posix(): dict(bytes=p.stat().st_size, sha256=file_hash(p))
                 for p in sorted(published.rglob('*')) if p.is_file()}
        manifest = dict(schema=1, root=str(root.resolve()), version=item['version'],
                        source_sha256=item['sha256'], files=files)
        (published / 'build.json').write_text(json.dumps(manifest, indent=2) + '\n')
        (published / 'build.json').chmod(0o644)
        published.rename(destination)
    return _sandbox.managed_runtime(root)


def build(root, python, *, jobs, lock, browser_policy=False, allow_system=True):
    env = _toolchain.environment()
    install_sandbox(root, lock, jobs=jobs)
    interpreter = venv(root, root / '.venv', python)
    run([interpreter, '-m', 'pip', 'install', '--disable-pip-version-check', '-r', root / 'ops/requirements.lock'], env=env)
    run([interpreter, '-m', 'pip', 'install', '--no-deps', '--no-build-isolation', '-e', root], env=env)
    run([interpreter, '-m', 'pip', 'check'], env=env)
    run(['/usr/bin/npm', 'ci', '--no-audit', '--no-fund'], cwd=root / 'desktop', env=env)
    run([interpreter, '-B', root / 'tools/setup_files.py'], cwd=root, env=env)
    run([interpreter, '-B', root / 'tools/setup_terminals.py'], cwd=root, env=env)
    if browser_policy:
        receipt = root / '.state/source-install/web-prepared.json'
        run([interpreter, '-B', root / 'tools/setup_web.py', '--prepare-only', '--receipt', receipt], cwd=root, env=env)
        _apparmor.ensure(root, receipt, run, allow_system=allow_system, env=env)
    run([*_sandbox.command(root), '--ro-bind', '/', '/', '--unshare-user',
         '--unshare-net', '--cap-drop', 'ALL', '--', '/usr/bin/true'], env=env)
    run([interpreter, '-B', root / 'tools/setup_web.py'], cwd=root, env=env)
    install_craft(root, lock)
    run(['/usr/bin/npm', 'run', 'check'], cwd=root / 'desktop', env=env)
    run(['/usr/bin/npm', 'run', 'build'], cwd=root / 'desktop', env=env)
    env['CARGO_TARGET_DIR'] = str(root / '.state/build/rust')
    env['CARGO_BUILD_JOBS'] = str(jobs)
    run([env['CARGO'], 'build', '--locked', '--manifest-path', root / 'desktop/src-tauri/Cargo.toml'], cwd=root, env=env)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', help='Install missing packages and build; otherwise print a read-only plan.')
    parser.add_argument('--no-system-packages', action='store_true', help='Require system dependencies to be installed already; never call sudo.')
    parser.add_argument('--no-desktop-entry', action='store_true', help='Do not register this checkout in the application menu (useful for a second test copy).')
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
        missing = missing_packages(distro)
        print(f'Installation path: {ROOT}\nSystem: {distro}; build jobs: {args.jobs}')
        print('Missing OS packages: ' + (', '.join(missing) or 'none'))
        print('Required downloads: locked Python/npm/Rust dependencies, Files, the shared Chromium headless shell, Terminal Use and Craft document tools.')
        print('No model is downloaded. GPU embeddings require the separately documented compatible Ollama/model setup.')
        browser_policy = _apparmor.required(distro)
        print('OS packages and the exact-path Ubuntu sandbox/browser policy use sudo; app files and runtimes belong to your normal user.')
        if browser_policy:
            print('Ubuntu restricts user namespaces. Install/verify per-checkout AppArmor profiles for the shared managed browser path and two role copies of one pinned Bubblewrap build; keep global restrictions and Chromium sandboxes enabled.')
        for command in package_commands(distro, missing):
            print(shlex.join(command))
        if not args.no_desktop_entry:
            print('Desktop entry: ' + str(_desktop.description(ROOT)[0]))
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
                if not args.no_desktop_entry:
                    _desktop.check(ROOT)
                for command in package_commands(distro, missing):
                    run(command)
                python = check_toolchain()
                build(ROOT, python, jobs=args.jobs, lock=lock,
                      browser_policy=browser_policy, allow_system=not args.no_system_packages)
                if not args.no_desktop_entry:
                    print("Desktop entry installed: " + str(_desktop.install(ROOT)))
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
