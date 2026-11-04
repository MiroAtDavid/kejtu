"""Install and check this checkout's pinned Linux web and browser runtimes."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.setup_runtime import install_directory, install_sdk, run



def install(root=ROOT):
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
        run([python, '-c',
             'from playwright.sync_api import sync_playwright\n'
             'with sync_playwright() as p:\n'
             ' b=p.chromium.launch(headless=True,chromium_sandbox=True); b.close()'], env=env)

        env['PLAYWRIGHT_BROWSERS_PATH'] = str(runtime / 'browsers')
        print('Installing Chromium for the browser connection…', flush=True)
        run(['/usr/bin/node', cli, 'install', '--no-shell', 'chromium'], env=env)
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
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        install()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f'Web setup did not finish: {error}\nFix the reported dependency and rerun ./kejtu setup-web.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
