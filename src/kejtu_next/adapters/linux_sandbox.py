"""One Linux sandbox runtime, with explicit identities for its two launch roles.

The second executable is an identical copy, not another build. Ubuntu attaches
AppArmor policy by executable path; selecting a profile with aa-exec would retain
the caller's unconfined restrictions. Only fixed browser owners select that role.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
USERNS_RESTRICTION = Path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns')
POLICY_DIRECTORY = Path('/etc/apparmor.d')
SYSTEM = Path('/usr/bin/bwrap')
ROLES = ('tools', 'browser')


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def real_file(path, *, executable=False):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('The sandbox runtime has a redirected path. Repair this installation.')
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o6022
            or (executable and not os.access(path, os.X_OK))):
        raise ValueError('The sandbox runtime has an unsafe file type or mode. Repair this installation.')
    return info


def restricted():
    # An unreadable or unexpected setting is not permission to bypass policy.
    if not USERNS_RESTRICTION.exists():
        return False
    value = USERNS_RESTRICTION.read_text().strip()
    if value not in ('0', '1'):
        raise ValueError('Cannot determine the Linux namespace restriction.')
    return value == '1'


def system_runtime():
    """Reuse a maintained, patched system runtime only outside restricted policy.

    0.12.0 fixes CVE-2026-87766. Ubuntu's 0.11.1-1ubuntu0.3 reverted that fix.
    A restricted system needs our exact role attachments even with a newer bwrap.
    """
    if restricted():
        return None
    try:
        info = real_file(SYSTEM, executable=True)
        if info.st_uid != 0:
            return None
        result = subprocess.run([str(SYSTEM), '--version'], capture_output=True,
                                text=True, timeout=5, check=True,
                                env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        match = re.fullmatch(r'bubblewrap (\d+)\.(\d+)\.(\d+)\s*', result.stdout)
        if not match or tuple(map(int, match.groups())) < (0, 12, 0):
            return None
        return dict(kind='system', version='.'.join(match.groups()), executable=str(SYSTEM))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def pin(root):
    return json.loads((Path(root) / 'packaging/source/runtime.lock.json').read_text())['bubblewrap']


def managed_runtime(root):
    root = Path(root).resolve()
    item = pin(root)
    directory = root / '.state/apps/bubblewrap' / item['directory']
    manifest_path = directory / 'build.json'
    real_file(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get('schema') != 1 or manifest.get('source_sha256') != item['sha256']
            or manifest.get('version') != item['version'] or manifest.get('root') != str(root)
            or set(manifest.get('files', {})) != {'bin/bwrap-tools', 'bin/bwrap-browser', 'COPYING', item['archive']}):
        raise ValueError('The installed sandbox does not match this source release. Rerun the source installer.')
    inodes = []
    for relative, expected in manifest['files'].items():
        path = directory / relative
        info = real_file(path, executable=relative.startswith('bin/'))
        if expected != dict(bytes=info.st_size, sha256=sha256(path)):
            raise ValueError('The installed sandbox changed. Preserve it for inspection and repair this installation.')
        if relative.startswith('bin/'):
            if info.st_nlink != 1:
                raise ValueError('Sandbox role executables must be independent regular files.')
            inodes.append((info.st_dev, info.st_ino))
    if (len(set(inodes)) != 2 or manifest['files']['bin/bwrap-tools'] != manifest['files']['bin/bwrap-browser']
            or manifest['files'][item['archive']]['sha256'] != item['sha256']
            or manifest['files']['COPYING']['sha256'] != item['license_sha256']):
        raise ValueError('The sandbox role copies or upstream notices differ from their verified build.')
    return dict(kind='managed', version=item['version'], source_sha256=item['sha256'],
                sha256=manifest['files']['bin/bwrap-tools']['sha256'],
                executables={role: str(directory / 'bin' / ('bwrap-' + role)) for role in ROLES})


def policy_identity(root):
    return hashlib.sha256(os.fsencode(str(Path(root).resolve()))).hexdigest()[:24]


def policy_ready(root, runtime):
    marker = Path(root) / '.state/source-install/apparmor.json'
    real_file(marker)
    saved = json.loads(marker.read_text())
    destination = POLICY_DIRECTORY / ('kejtu-source-' + policy_identity(root))
    info = real_file(destination)
    if (info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o644
            or saved.get('schema') != 2 or saved.get('sandbox') != runtime
            or saved.get('policy_sha256') != sha256(destination)):
        raise ValueError('The exact sandbox AppArmor policy is missing or changed. Rerun the source installer.')


def command(root=ROOT, *, browser=False):
    system = system_runtime()
    if system is not None:
        return [system['executable']]
    runtime = managed_runtime(root)
    if restricted():
        policy_ready(root, runtime)
        if browser:
            approved_browsers(root)
    return [runtime['executables']['browser' if browser else 'tools']]


def approved_browsers(root):
    root = Path(root).resolve()
    marker = root / '.state/source-install/apparmor.json'
    real_file(marker)
    prepared = json.loads(marker.read_text())['prepared']
    lock_names = ('ops/requirements.lock', 'ops/mcp/package-lock.json')
    if (prepared.get('root') != str(root) or prepared.get('kind') != 'kejtu-web-prepared'
            or prepared.get('schema') != 1 or set(prepared.get('locks', {})) != set(lock_names)
            or [r.get('role') for r in prepared.get('browsers', [])] != ['page_reader', 'browser']):
        raise ValueError('The managed browser preparation is incomplete or belongs to another installation.')
    for relative, value in prepared['locks'].items():
        if sha256(root / relative) != value:
            raise ValueError('The browser dependency lock changed. Rerun the source installer.')
    node = root / '.state/mcp-runtime/playwright'
    if (node / 'package-lock.json').read_bytes() != (root / 'ops/mcp/package-lock.json').read_bytes():
        raise ValueError('The browser runtime no longer matches its dependency lock.')
    try:
        reader_package = Path(importlib.metadata.distribution('playwright').locate_file('playwright/driver/package')).resolve()
    except importlib.metadata.PackageNotFoundError:
        raise ValueError('The page-reader dependency is missing. Rerun the source installer.') from None
    if not reader_package.is_relative_to(root / '.venv'):
        raise ValueError('The page-reader runtime belongs to another installation.')
    real_file(node / 'node_modules/@playwright/mcp/cli.js')
    for selected, directory, package in zip(prepared['browsers'],
            (root / '.state/playwright', root / '.state/mcp-runtime/browsers'),
            (reader_package, node / 'node_modules/playwright-core')):
        browser = Path(selected['executable'])
        real_file(browser, executable=True)
        registry = package / 'browsers.json'
        real_file(registry)
        if (not browser.is_relative_to(directory) or sha256(browser) != selected['sha256']
                or browser.stat().st_size != selected['bytes']
                or sha256(registry) != selected['registry_sha256']):
            raise ValueError('The browser or registry differs from its approved managed executable.')
    return prepared['browsers']


def browser_identity(root, row):
    """Recognize execution content, never connection names or imported flags.

    A saved connection is mutable. Both the host and worker recheck its exact
    wrapper, environment, mounts and managed executable before choosing a role.
    Permission/tool allowlists remain independent and are not modified here.
    """
    root = Path(root).resolve()
    module = 'kejtu_next.adapters.web.mcp_browser'
    if row.get('args', [])[:3] != ['-B', '-m', module]:
        return None
    runtime = root / '.state/mcp-runtime/playwright'
    config = root / '.state/mcp-runtime/browser.json'
    if config.is_symlink():
        raise ValueError('The browser configuration is redirected.')
    browser = Path(json.loads(config.read_text())['executable'])
    expected = dict(transport='stdio', command=str(Path(sys._base_executable).resolve()),
                    args=['-B', '-m', module, '--runtime', str(runtime), '--browser', str(browser)],
                    env={'PYTHONPATH': str(root / 'src')},
                    read_paths=[str(root / 'src'), str(runtime), str(browser.parents[1])],
                    write_paths=[], network=True, requires_web=True)
    if any(row.get(key) != value for key, value in expected.items()) or row.get('shared_root'):
        raise ValueError('The included browser command or access changed. Restore its included connection before using it.')
    # A compatible unrestricted system can still run an explicitly chosen browser
    # under its existing namespace contract. Ubuntu's extra policy covers only the
    # two managed engines from the preparation receipt.
    identity = dict(command=expected['command'], args=expected['args'])
    if restricted():
        selected = approved_browsers(root)[1]
        if str(browser) != selected['executable']:
            raise ValueError('The browser differs from its approved managed executable.')
        identity['browser_sha256'] = selected['sha256']
    return identity
