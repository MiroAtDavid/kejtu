"""Install and check this checkout's pinned Linux web and browser runtimes."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
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


def prepared_browsers(root, *, env=None):
    """Describe installed, version-matched browsers without launching them.

    Executable paths come from the pinned Playwright registries. Their layout is
    not duplicated here; an incompatible future registry fails before policy is
    granted. Hashes bind a receipt to the actual files, not an upstream signature.
    """
    root = Path(root).resolve()
    environment = dict(os.environ if env is None else env)
    python = root / '.venv/bin/python'
    script = ('import json,playwright; from importlib.metadata import version; '
              'from pathlib import Path; print(json.dumps({"version":version("playwright"),'
              '"package":str(Path(playwright.__file__).parent / "driver/package")}))')
    reader = json.loads(run([python, '-c', script], env=environment, capture=True).stdout)
    pins = [line.split('==', 1)[1] for line in (root / 'ops/requirements.lock').read_text().splitlines()
            if line.startswith('playwright==')]
    if pins != [reader['version']]:
        raise ValueError('The page-reader Playwright version does not match ops/requirements.lock.')
    reader_package = Path(reader['package'])
    if not reader_package.resolve().is_relative_to(root / '.venv'):
        raise ValueError('The page-reader Playwright package must belong to this checkout’s .venv.')
    node_package = root / '.state/mcp-runtime/playwright/node_modules/playwright-core'
    node_version = json.loads((node_package / 'package.json').read_text())['version']
    node_pin = json.loads((root / 'ops/mcp/package-lock.json').read_text())['packages']['node_modules/playwright-core']['version']
    if node_version != node_pin:
        raise ValueError('The browser Playwright version does not match ops/mcp/package-lock.json.')
    rows = []
    for role, package, version, name, directory in (
        ('page_reader', reader_package, reader['version'], 'chromium-headless-shell', root / '.state/playwright'),
        ('browser', node_package, node_version, 'chromium', root / '.state/mcp-runtime/browsers'),
    ):
        browser_env = {**environment, 'PLAYWRIGHT_BROWSERS_PATH': str(directory)}
        registry = package / 'browsers.json'
        script = ('const {registry}=require(process.argv[1]).registry; '
                  'console.log(registry.findExecutable(process.argv[2]).executablePath());')
        path = managed_executable(run(['/usr/bin/node', '-e', script, package / 'lib/coreBundle.js', name],
                                      env=browser_env, capture=True).stdout.strip(), directory)
        rows.append(dict(role=role, playwright_version=version, registry_sha256=file_sha256(registry),
                         executable=str(path), bytes=path.stat().st_size, sha256=file_sha256(path)))
    return dict(schema=1, kind='kejtu-web-prepared', root=str(root),
                locks={name: file_sha256(root / name) for name in ('ops/requirements.lock', 'ops/mcp/package-lock.json')},
                browsers=rows)


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
    if any(path.is_symlink() for path in (state / 'playwright', runtime / 'browsers', runtime / 'browser.json')):
        raise ValueError('Browser paths must belong to this checkout, without symlinks.')
    with (state / 'runtime-setup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print('Checking the installed page-reader dependencies…', flush=True)
        run([python, '-c', 'import playwright.sync_api, trafilatura, lxml'])

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

        # Python extraction and Node MCP pin different Playwright versions.
        # Each installs its own matching browser; neither upgrades the other.
        env = os.environ.copy()
        env['PLAYWRIGHT_SKIP_BROWSER_GC'] = '1'
        env['PLAYWRIGHT_BROWSERS_PATH'] = str(state / 'playwright')
        print('Installing Chromium for page reading…', flush=True)
        run([python, '-m', 'playwright', 'install', '--only-shell', 'chromium'], env=env)

        env['PLAYWRIGHT_BROWSERS_PATH'] = str(runtime / 'browsers')
        print('Installing Chromium for the browser connection…', flush=True)
        run(['/usr/bin/node', cli, 'install', '--no-shell', 'chromium'], env=env)
        if prepare_only:
            result = prepared_browsers(root)
            write_receipt(receipt, result)
            print('Web runtimes downloaded; browser launch and configuration are still pending.', flush=True)
            return result
        env['PLAYWRIGHT_BROWSERS_PATH'] = str(state / 'playwright')
        run([python, '-c',
             'from playwright.sync_api import sync_playwright\n'
             'with sync_playwright() as p:\n'
             ' b=p.chromium.launch(headless=True,chromium_sandbox=True); b.close()'], env=env)
        env['PLAYWRIGHT_BROWSERS_PATH'] = str(runtime / 'browsers')
        module = node / 'node_modules/playwright'
        script = 'const {chromium}=require(process.argv[1]); console.log(chromium.executablePath());'
        executable = Path(run(['/usr/bin/node', '-e', script, module], env=env, capture=True).stdout.strip())
        config = runtime / 'browser.json'
        if config.exists():
            # An explicitly selected existing executable remains the authority.
            executable = Path(json.loads(config.read_text())['executable'])
        if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError('The selected Chromium executable is unavailable. No browser setting was changed.')
        script = ('const {chromium}=require(process.argv[1]); '
                  '(async()=>{const b=await chromium.launch({executablePath:process.argv[2],'
                  'headless:true,chromiumSandbox:true}); await b.close();})()'
                  '.catch(e=>{console.error(e.message);process.exit(1)});')
        run(['/usr/bin/node', '-e', script, module, executable], env=env)
        if not config.exists():
            with config.open('x') as target:
                json.dump({'executable': str(executable)}, target)
                target.write('\n')
            config.chmod(0o600)
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
