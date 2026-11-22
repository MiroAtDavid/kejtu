"""Install and check this checkout's pinned Linux web and browser runtimes."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.setup_runtime import install_directory, install_sdk, run


def file_sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def managed_executable(path, directory):
    """A policy receipt never grants access to a redirected or custom browser."""
    path, directory = Path(path), Path(directory)
    if not path.is_absolute() or not path.is_relative_to(directory) or '..' in path.parts:
        raise ValueError('The browser executable is outside its managed download directory.')
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('Managed browser executable paths must not contain symlinks.')
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError(f'The managed browser executable is unavailable: {path}')
    return path


def normalize_managed_permissions(files):
    """Remove installer-umask write access from exact verified runtime files.

    Validate the entire set before changing modes. Open every path component
    without following links, then chmod the held file descriptor so a renamed
    path cannot redirect the operation. Custom browser selections never enter
    this set; content, ownership and existing executable bits are preserved.
    """
    opened = []
    try:
        for path, directory, expected_sha256 in files:
            path, directory = Path(path), Path(directory)
            if not path.is_absolute() or not path.is_relative_to(directory) or '..' in path.parts:
                raise ValueError('Browser permission repair requires an exact managed runtime path.')
            descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
            try:
                for part in path.parts[1:-1]:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = child
                file_descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                          dir_fd=descriptor)
                opened.append((file_descriptor, None))
            finally:
                os.close(descriptor)
            info = os.fstat(file_descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or info.st_mode & 0o7000):
                raise ValueError('Managed browser permission repair requires ordinary, singly linked files owned by this user.')
            with os.fdopen(os.dup(file_descriptor), 'rb') as stream:
                actual_sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual_sha256 != expected_sha256:
                raise ValueError('A managed browser file changed during preparation; rerun setup.')
            opened[-1] = (file_descriptor, stat.S_IMODE(info.st_mode))
        for descriptor, mode in opened:
            if mode & 0o022:
                os.fchmod(descriptor, mode & ~0o022)
    finally:
        for descriptor, _ in opened:
            os.close(descriptor)


def prepared_browsers(root, *, env=None, normalize_permissions=False):
    """Describe installed, version-matched browsers without launching them.

    Executable paths come from the pinned Playwright registries. Their layout is
    not duplicated here; an incompatible future registry fails before policy is
    granted. Hashes bind a receipt to the actual files, not an upstream signature.
    """
    root = Path(root).resolve()
    environment = dict(os.environ if env is None else env)
    node_package = root / '.state/mcp-runtime/playwright/node_modules/playwright-core'
    node_version = json.loads((node_package / 'package.json').read_text())['version']
    node_pin = json.loads((root / 'ops/mcp/package-lock.json').read_text())['packages']['node_modules/playwright-core']['version']
    if node_version != node_pin:
        raise ValueError('The browser Playwright version does not match ops/mcp/package-lock.json.')
    node_runtime = root / '.state/mcp-runtime/playwright'
    if (node_runtime / 'package-lock.json').read_bytes() != (root / 'ops/mcp/package-lock.json').read_bytes():
        raise ValueError('The installed browser lock does not match ops/mcp/package-lock.json.')
    cli = node_runtime / 'node_modules/@playwright/mcp/cli.js'
    files = [(cli, node_runtime, file_sha256(cli))]
    directory = root / '.state/mcp-runtime/browsers'
    registry = node_package / 'browsers.json'
    script = ('const {registry}=require(process.argv[1]).registry; '
              'console.log(registry.findExecutable(process.argv[2]).executablePath());')
    selected = run(['/usr/bin/node', '-e', script, node_package / 'lib/coreBundle.js', 'chromium-headless-shell'],
                   env={**environment, 'PLAYWRIGHT_BROWSERS_PATH': str(directory)}, capture=True).stdout.strip()
    path = managed_executable(selected, directory)
    row = dict(role='browser', playwright_version=node_version, registry_sha256=file_sha256(registry),
               executable=str(path), bytes=path.stat().st_size, sha256=file_sha256(path))
    if normalize_permissions:
        normalize_managed_permissions([*files, (registry, node_runtime, row['registry_sha256']),
                                       (path, directory, row['sha256'])])
    return dict(schema=2, kind='kejtu-web-prepared', root=str(root),
                locks={name: file_sha256(root / name) for name in ('ops/requirements.lock', 'ops/mcp/package-lock.json')},
                browsers=[row])


def write_receipt(path, data):
    path = Path(path)
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('The web preparation receipt must not use a symlink.')
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.web-receipt-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(data, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def run_nested(arguments, *, env):
    """Bound one owned Bubblewrap/browser tree, including failure and interruption."""
    process = subprocess.Popen([str(arg) for arg in arguments], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, start_new_session=True)
    try:
        try:
            output, _ = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            raise ValueError('Browser startup inside the Linux tool sandbox timed out.') from None
        if process.returncode:
            raise ValueError('Browser startup inside the Linux tool sandbox failed: ' + output[-2000:])
    finally:
        # Each prefix uses --die-with-parent and its own PID namespace. Cleanup
        # also runs after a successful helper exit to collect browser children.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()


def check_nested_browsers(root, executable, node, env):
    """Exercise both production namespace/mount prefixes with an in-memory page.

    These fixed installer payloads never visit a network URL. Full MCP/public
    proxy behavior has separate acceptance controls; direct launch alone cannot
    prove that Chromium's nested sandbox works under Ubuntu's AppArmor policy.
    """
    from kejtu_next.adapters.web.reader import browser_arguments
    from kejtu_next.adapters.mcp.host import McpHost
    from kejtu_next.adapters.mcp.worker import sandbox
    script = ('const {chromium}=require(process.argv[1]);'
              '(async()=>{const b=await chromium.launch({executablePath:process.argv[2],'
              'headless:true,chromiumSandbox:true});const p=await b.newPage();'
              'await p.setContent("<p>Kejtu sandbox check</p>");'
              'if(await p.innerText("body")!=="Kejtu sandbox check")throw Error("Page mismatch");'
              'await b.close();})().catch(e=>{console.error(e.message);process.exit(1)});')
    probe = ['/usr/bin/node', '-e', script, str(node / 'node_modules/playwright')]
    reader = browser_arguments(root)
    reader_env = {key: env[key] for key in ('PATH', 'LANG', 'LC_ALL') if key in env}
    reader_env['PLAYWRIGHT_BROWSERS_PATH'] = str(root / '.state/mcp-runtime/browsers')
    # Omitting executablePath exercises the reader's actual headless-shell selection.
    run_nested(reader[:reader.index('--') + 1] + probe, env=reader_env)
    with tempfile.TemporaryDirectory(prefix='web-check-', dir=root / '.state') as temporary:
        host = McpHost(Path(temporary), root=root, factory=lambda row: row)
        host.add_browser(host.connections.read()['revision'])
        row = host.settings()['servers']['playwright']
        browser = sandbox(host.connection('playwright', row))
        browser = browser[:browser.index('--') + 1]
        browser += probe + [str(executable)]
        run_nested(browser, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})


def install(root=ROOT, *, prepare_only=False, receipt=None):
    if prepare_only and receipt is None:
        raise ValueError('--prepare-only requires --receipt PATH.')
    root = Path(root).resolve()
    python = root / '.venv/bin/python'
    if sys.platform != 'linux' or not python.is_file():
        raise ValueError('Web setup requires Linux and this checkout’s .venv. Complete the README dependency installation first.')
    for command in ('node', 'npm', 'bwrap'):
        if not shutil.which(command, path='/usr/bin:/bin'):
            raise ValueError(f'Install {command} with your Linux package manager, then run ./kejtu setup-web again.')
    state = root / '.state'
    runtime = state / 'mcp-runtime'
    if state.is_symlink() or runtime.is_symlink():
        raise ValueError('Web runtime directories must be real directories in this checkout.')
    state.mkdir(mode=0o700, exist_ok=True)
    runtime.mkdir(mode=0o700, exist_ok=True)
    if any(path.is_symlink() for path in (runtime / 'browsers', runtime / 'browser.json')):
        raise ValueError('Browser paths must belong to this checkout, without symlinks.')
    with (state / 'runtime-setup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print('Checking the installed page-reader dependencies…', flush=True)
        run([python, '-c', 'import trafilatura, lxml'])

        install_sdk(root)

        node = runtime / 'playwright'
        def install_node(target):
            for name in ('package.json', 'package-lock.json'):
                shutil.copy2(root / 'ops/mcp' / name, target / name)
            run(['/usr/bin/npm', 'ci', '--prefix', target, '--ignore-scripts', '--no-audit', '--no-fund'])
        print('Installing the pinned Playwright MCP server…', flush=True)
        install_directory(node, install_node)
        if (node / 'package-lock.json').read_bytes() != (root / 'ops/mcp/package-lock.json').read_bytes():
            raise ValueError('The existing browser MCP runtime uses another lockfile. It was not replaced.')
        cli = node / 'node_modules/playwright/cli.js'
        if not (node / 'node_modules/@playwright/mcp/cli.js').is_file():
            raise ValueError('The existing browser MCP installation is incomplete; it was not replaced.')

        env = os.environ.copy()
        env['PLAYWRIGHT_SKIP_BROWSER_GC'] = '1'
        env['PLAYWRIGHT_BROWSERS_PATH'] = str(runtime / 'browsers')
        print('Installing Chromium headless shell for the page reader and browser connection…', flush=True)
        run(['/usr/bin/node', cli, 'install', '--only-shell', 'chromium'], env=env)
        # npm/pip may retain group-write bits from a desktop user's umask 0002.
        # Repair only this checked managed set, including on an existing install,
        # before the unchanged strict launch checks or an AppArmor receipt.
        prepared = prepared_browsers(root, normalize_permissions=True)
        if prepare_only:
            result = prepared
            write_receipt(receipt, result)
            print('Web runtimes downloaded; browser launch and configuration are still pending.', flush=True)
            return result
        module = node / 'node_modules/playwright'
        executable = Path(prepared['browsers'][0]['executable'])
        config = runtime / 'browser.json'
        previous = json.loads(config.read_text()) if config.exists() else None
        if previous is not None:
            # Upgrade the old managed default while preserving explicit custom paths.
            script = 'const {chromium}=require(process.argv[1]); console.log(chromium.executablePath());'
            old_default = run(['/usr/bin/node', '-e', script, module], env=env, capture=True).stdout.strip()
            if previous['executable'] != old_default:
                executable = Path(previous['executable'])
        if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError('The selected Chromium executable is unavailable. No browser setting was changed.')
        script = ('const {chromium}=require(process.argv[1]); '
                  '(async()=>{const b=await chromium.launch({executablePath:process.argv[2],'
                  'headless:true,chromiumSandbox:true}); await b.close();})()'
                  '.catch(e=>{console.error(e.message);process.exit(1)});')
        run(['/usr/bin/node', '-e', script, module, executable], env=env)
        updated = dict(previous or {}, executable=str(executable))
        if updated != previous:
            write_receipt(config, updated)
        try:
            print('Checking both tools inside their Linux browser sandboxes…', flush=True)
            check_nested_browsers(root, executable, node, env)
        except BaseException:
            if updated != previous:
                if previous is None:
                    config.unlink()
                else:
                    write_receipt(config, previous)
            raise
        result = dict(page_reader=True, mcp=True, browser=str(executable))
        print('Web runtimes are ready. First launch enables Tavily and the browser connection.', flush=True)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true', help='Download matched browsers and write a receipt without launching or configuring them.')
    parser.add_argument('--receipt', type=Path, help='Preparation receipt destination (requires --prepare-only).')
    args = parser.parse_args()
    if bool(args.receipt) != args.prepare_only:
        parser.error('--prepare-only and --receipt PATH must be used together.')
    try:
        install(prepare_only=args.prepare_only, receipt=args.receipt)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f'Web setup did not finish: {error}\nFix the reported dependency and rerun ./kejtu setup-web.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
