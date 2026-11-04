"""Exact-path Ubuntu user-namespace policy for this checkout's managed browsers."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat

RESTRICTION = Path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns')
POLICY_DIRECTORY = Path('/etc/apparmor.d')
UNCONFINED_RESTRICTION = Path('/proc/sys/kernel/apparmor_restrict_unprivileged_unconfined')

_spec = importlib.util.spec_from_file_location('kejtu_policy_sandbox', Path(__file__).resolve().parents[2] / 'src/kejtu_next/adapters/linux_sandbox.py')
sandbox_owner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sandbox_owner)

# Only fixed OS programs run with privilege. Policy bytes are an argument, never
# shell source or a user-writable Python script. Root staging closes the race
# between policy validation and publishing; unexpected existing content is kept.
APPLY = r'''
set -eu
destination=$1
expected=$2
previous=$3
body=$4
case "$destination" in /etc/apparmor.d/kejtu-source-*) ;; *) exit 2;; esac
[ "$(id -u)" = 0 ]
[ ! -L /etc/apparmor.d ] && [ -d /etc/apparmor.d ]
case "$(stat -c '%u:%a' /etc/apparmor.d)" in 0:755|0:750|0:700) ;; *) echo 'Unexpected AppArmor directory ownership/mode.' >&2; exit 2;; esac
if [ -e "$destination" ] || [ -L "$destination" ]; then
    [ ! -L "$destination" ] && [ -f "$destination" ] || exit 2
    [ "$(stat -c '%u:%a' "$destination")" = 0:644 ] || exit 2
    actual=$(sha256sum "$destination")
    actual=${actual%% *}
    if [ "$actual" != "$expected" ] && [ "$actual" != "$previous" ]; then
        echo "Existing AppArmor profile differs; preserved: $destination" >&2
        exit 2
    fi
fi
temporary=$(mktemp /etc/apparmor.d/.kejtu-policy-XXXXXXXX)
trap 'rm -f -- "$temporary"' EXIT
printf '%s' "$body" > "$temporary"
actual=$(sha256sum "$temporary")
[ "${actual%% *}" = "$expected" ]
/usr/sbin/apparmor_parser --skip-kernel-load --skip-cache "$temporary"
chmod 0644 "$temporary"
if [ -f "$destination" ] && cmp -s "$temporary" "$destination"; then
    rm -f -- "$temporary"
else
    mv -T -- "$temporary" "$destination"
fi
/usr/sbin/apparmor_parser --replace --skip-cache "$destination"
'''


def required(distro):
    return distro == 'ubuntu' and RESTRICTION.is_file() and RESTRICTION.read_text().strip() == '1'


def digest(body):
    return hashlib.sha256(body.encode()).hexdigest()


def quoted_path(path):
    text = str(path)
    # Quoting handles spaces and Unicode. Reject AppArmor pattern/variable
    # syntax rather than attempting to escape a broader policy language.
    if any(ord(char) < 32 or ord(char) == 127 or char in '\\"*?[]{}^@' for char in text):
        raise ValueError('This checkout path contains characters unsupported by the exact-path AppArmor policy. Use a final path without quotes, backslashes or pattern characters.')
    return '"' + text + '"'


def policy(root, receipt, sandbox=None):
    root = Path(root).resolve()
    if receipt.get('schema') != 1 or receipt.get('kind') != 'kejtu-web-prepared' or receipt.get('root') != str(root):
        raise ValueError('The browser receipt belongs to a different checkout; relocation is not supported.')
    identifier = hashlib.sha256(os.fsencode(str(root))).hexdigest()[:24]
    rows = receipt['browsers']
    if [row.get('role') for row in rows] != ['page_reader', 'browser']:
        raise ValueError('The browser receipt must contain exactly the two managed browser roles.')
    body = '# Kejtu managed browser user namespaces. Regenerate with the source installer.\nabi <abi/4.0>,\n'
    for row, directory in zip(rows, (root / '.state/playwright', root / '.state/mcp-runtime/browsers')):
        path = Path(row['executable'])
        if not path.is_absolute() or not path.is_relative_to(directory) or '..' in path.parts:
            raise ValueError('The policy executable is outside its managed browser directory.')
        body += f'\nprofile kejtu-{identifier}-{row["role"]} {quoted_path(path)} flags=(unconfined) {{\n  userns,\n}}\n'
    if sandbox is not None:
        if sandbox.get('kind') != 'managed' or set(sandbox.get('executables', {})) != {'tools', 'browser'}:
            raise ValueError('The policy needs both verified managed sandbox roles.')
        version = sandbox.get('version', '')
        if not re.fullmatch(r'\d+\.\d+\.\d+', version):
            raise ValueError('Invalid sandbox version in the policy receipt.')
        for role, value in sandbox['executables'].items():
            expected = root / '.state/apps/bubblewrap' / ('bubblewrap-' + version) / 'bin' / ('bwrap-' + role)
            if value != str(expected):
                raise ValueError('Sandbox policy attachment is outside its exact managed role path.')
        body = body.replace('abi <abi/4.0>,', 'abi <abi/5.0>,')
        tools, child, browser = (f'kejtu-{identifier}-{name}' for name in ('tools', 'tool-child', 'browser-launch'))
        # Preserve Ubuntu's child capability restriction for generic tools. NNP
        # requires a stacked transition; no local/global bwrap policy is edited.
        common = ('  allow file rwlkm /{**,},\n  allow network,\n  allow unix,\n'
                  '  allow ptrace,\n  allow signal,\n  allow mqueue,\n  allow io_uring,\n'
                  '  allow userns,\n  allow mount,\n  allow umount,\n  allow pivot_root,\n  allow dbus,\n')
        body += (f'\nprofile {tools} {quoted_path(sandbox["executables"]["tools"])} flags=(attach_disconnected,mediate_deleted) {{\n'
                 '  allow capability,\n' + common + f'  allow pix /** -> &{tools}//&{child},\n}}\n'
                 f'\nprofile {child} flags=(attach_disconnected,mediate_deleted) {{\n' + common +
                 f'  allow pix /** -> &{child},\n  audit deny capability,\n}}\n'
                 f'\nprofile {browser} {quoted_path(sandbox["executables"]["browser"])} flags=(unconfined) {{\n  userns,\n}}\n')
    return POLICY_DIRECTORY / ('kejtu-source-' + identifier), body


def existing_policy(path, body):
    if not path.exists() and not path.is_symlink():
        return False
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o644:
        raise ValueError(f'The existing AppArmor profile has unexpected ownership/type/mode; preserved: {path}')
    return path.read_text() == body


def ensure(root, receipt_path, run, *, allow_system=True, env=None):
    """Apply/reload only a known generated profile; never change global policy."""
    root = Path(root).resolve()
    spec = importlib.util.spec_from_file_location('kejtu_web_setup', root / 'tools/setup_web.py')
    web = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(web)
    receipt_path = Path(receipt_path)
    if receipt_path.is_symlink():
        raise ValueError('The browser receipt must not be a symlink.')
    receipt = json.loads(receipt_path.read_text())
    if receipt != web.prepared_browsers(root, env=env):
        raise ValueError('The prepared browser identity or content changed. Rerun preparation before granting policy.')
    sandbox = sandbox_owner.managed_runtime(root)
    destination, body = policy(root, receipt, sandbox)
    current = dict(prepared=receipt, sandbox=sandbox)
    marker = root / '.state/source-install/apparmor.json'
    previous = None
    if marker.is_symlink():
        raise ValueError('The AppArmor installation receipt must not be a symlink.')
    if marker.exists():
        saved = json.loads(marker.read_text())
        old = dict(prepared=saved['prepared'], sandbox=saved.get('sandbox'))
        policy(root, old['prepared'], old['sandbox'])  # Refuse relocation before interpreting history.
        pending = saved.get('previous')
        if pending is not None and saved.get('schema') != 2:
            pending = dict(prepared=pending, sandbox=None)
        if pending is not None and old != current:
            raise ValueError('An earlier browser policy update is unfinished. Complete that installation before changing browser versions.')
        previous = pending if old == current else old
    previous_body = policy(root, previous['prepared'], previous['sandbox'])[1] if previous is not None else None
    matching = existing_policy(destination, body)
    if destination.exists() and not matching and (previous_body is None or not existing_policy(destination, previous_body)):
        raise ValueError(f'The existing AppArmor profile was changed outside the installer; preserved: {destination}')
    if not allow_system:
        if not matching:
            raise ValueError('Ubuntu requires this checkout’s browser AppArmor profile. Rerun without --no-system-packages so the installer can install it with sudo.')
        web.write_receipt(marker, dict(schema=2, **current, policy_sha256=digest(body), previous=None))
        print(f'Reusing browser AppArmor policy: {destination}', flush=True)
        return
    # Save the old/new identities before sudo so interruption after atomic policy
    # publish can be repaired without treating our own new file as a conflict.
    web.write_receipt(marker, dict(schema=2, **current, policy_sha256=digest(body), previous=previous))
    before_unconfined = UNCONFINED_RESTRICTION.read_text() if UNCONFINED_RESTRICTION.exists() else None
    print(f'Installing sandbox/browser AppArmor policy: {destination}\n'
          'Exact attachments: two managed browsers and two role copies of one Bubblewrap build. '
          'Ordinary tools retain the child capability restriction; the trusted browser chain can create its nested sandbox.', flush=True)
    run(['sudo', '/bin/sh', '-c', APPLY, 'kejtu-browser-policy', destination,
         digest(body), digest(previous_body) if previous_body else '-', body])
    if not existing_policy(destination, body):
        raise ValueError('The browser AppArmor policy did not match after installation.')
    if RESTRICTION.read_text().strip() != '1':
        raise ValueError('Ubuntu’s global user-namespace restriction changed during installation; it was not changed by this installer.')
    if before_unconfined is not None and UNCONFINED_RESTRICTION.read_text() != before_unconfined:
        raise ValueError('The global unconfined restriction changed during installation; it was not changed by this installer.')
    web.write_receipt(marker, dict(schema=2, **current, policy_sha256=digest(body), previous=None))
