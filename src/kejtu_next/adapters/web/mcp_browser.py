"""Fixed Playwright MCP profile with public-only proxy and ephemeral browser state.

Linux uses the generic stdio filesystem sandbox. The trusted Windows launcher
runs under its parent's process Job; Chromium retains its own sandbox. The
standard upstream server and host-owned tool permissions remain unchanged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from .network import public_proxy

INIT_PAGE = r'''exports.default = async ({page}) => {
  const context = page.context();
  await context.route('**/*', async route => {
    const request = route.request();
    let url;
    try { url = new URL(request.url()); } catch { return route.abort(); }
    const host = url.hostname.toLowerCase().replace(/\.$/, '');
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
        (url.port && !['80','443'].includes(url.port)) || host === 'localhost' ||
        ['.localhost','.local','.internal','.onion'].some(s => host.endsWith(s)) ||
        ['image', 'media', 'font'].includes(request.resourceType())) return route.abort();
    return route.continue();
  });
  await context.routeWebSocket('**/*', socket => socket.close());
  page.on('download', download => download.cancel());
};
'''


def windows_inputs():
    """Only bundled runtime paths; model or imported arguments cannot select code."""
    root = Path(__file__).resolve().parents[4]
    node = root / '.venv/Scripts/site-packages/playwright/driver/node.exe'
    runtime = root / 'package-runtime/browser/mcp'
    browser = root / 'package-runtime/browser/interactive/chrome-headless-shell-win64/chrome-headless-shell.exe'
    cli = runtime / 'node_modules/@playwright/mcp/cli.js'
    for target in (node, cli, browser):
        if not target.is_file():
            raise ValueError('The packaged browser runtime is missing. No browser was started.')
        for candidate in (target, *target.parents):
            if candidate == root:
                break
            if candidate.is_symlink() or candidate.is_junction():
                raise ValueError('The packaged browser runtime path has changed. No browser was started.')
    return node, runtime, browser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    windows = sys.platform == 'win32'
    if windows:
        # The fixed symbolic connection selects this entry. Neither command-line
        # paths nor ambient Node/Chromium overrides may replace its package code.
        if argv:
            print('The packaged browser does not accept runtime path overrides.', file=sys.stderr)
            return 1
        try:
            node, runtime, browser = windows_inputs()
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 1
    else:
        parser = argparse.ArgumentParser()
        parser.add_argument('--runtime', type=Path, required=True)
        parser.add_argument('--browser', type=Path, required=True)
        args = parser.parse_args(argv)
        node, runtime, browser = Path('/usr/bin/node'), args.runtime, args.browser
    with tempfile.TemporaryDirectory(prefix='kejtu-mcp-browser-') as directory, public_proxy(900) as proxy:
        folder = Path(directory)
        init = folder / 'init.cjs'
        init.write_text(INIT_PAGE, encoding='utf-8')
        config = {'browser': {'browserName': 'chromium', 'isolated': True,
            'launchOptions': {'headless': True, 'chromiumSandbox': True, 'executablePath': str(browser),
                'proxy': {'server': f'http://127.0.0.1:{proxy.server_port}', 'username': 'kejtu',
                          'password': proxy.password, 'bypass': '<-loopback>'},
                'args': ['--disable-quic', '--force-webrtc-ip-handling-policy=disable_non_proxied_udp']},
            'contextOptions': {'acceptDownloads': False, 'serviceWorkers': 'block', 'permissions': [],
                               'locale': 'de-AT', 'timezoneId': 'Europe/Vienna'},
            'initPage': [str(init)]}, 'imageResponses': 'omit', 'codegen': 'none',
            'outputDir': str(folder / 'output'), 'saveSession': False,
            # MCP0.0.80 stores automatic snapshots as file links. Explicit
            # browser_snapshot calls still return inline text with element refs.
            'snapshot': {'mode': 'none' if windows else 'full'}, 'console': {'level': 'error'},
            'timeouts': {'action': 5000, 'navigation': 15000, 'settle': 200}}
        config_file = folder / 'config.json'
        config_file.write_text(json.dumps(config), encoding='utf-8')
        options = {}
        if windows:
            from ..mcp.windows_runtime import environment
            # Child stdin/stdout are inherited binary handles: Python does not
            # decode/re-encode MCP traffic through a Windows console code page.
            # CREATE_NO_WINDOW needs explicit standard handles on Windows:
            # leaving all three as None omits STARTF_USESTDHANDLES and can lose
            # the MCP pipes. Python duplicates only these binary handles.
            options = {'env': dict(environment(folder), CHROME_LOG_FILE=str(folder / 'chrome.log')), 'stdin': sys.stdin.buffer,
                       'stdout': sys.stdout.buffer, 'stderr': sys.stderr.buffer,
                       'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
        return subprocess.call([str(node), str(runtime / 'node_modules/@playwright/mcp/cli.js'),
                                '--config', str(config_file)], cwd=folder, **options)


if __name__ == '__main__':
    raise SystemExit(main())
