"""Operator-owned connections; familiar mcpServers input, explicit local access."""
import os
import sys
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit

from ...core.mcp import NAME, TOOL, MAX_TOOLS, encode, parse
from ...core.records import Failure
from ..storage.files import open_private_regular, atomic_write
from .files import validate as validate_files, FILES_SERVER


def strings(value, maximum=64):
    if not isinstance(value, list) or len(value) > maximum or any(
            not isinstance(v, str) or len(v) > 4096 or '\0' in v for v in value):
        raise ValueError('Expected a bounded list of strings.')
    return value


def mapping(value):
    if not isinstance(value, dict) or len(value) > 24 or any(
            not isinstance(k, str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_-]{0,79}', k)
            or not isinstance(v, str) or len(v) > 8192 or any(ord(c) < 32 for c in v)
            for k, v in value.items()):
        raise ValueError('Invalid environment variables or headers.')
    return value


def paths(value):
    for raw in strings(value, 16):
        p = Path(raw)
        if not p.is_absolute() or str(p.resolve()) != raw or raw in ('/', '/home', '/tmp', '/proc', '/dev', '/etc', '/usr'):
            raise ValueError('Choose specific real absolute directories for file access.')
    return value


def input_fields(value):
    """Package-declared environment inputs. Values are never part of this metadata."""
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise ValueError('A package declares 1–16 account fields.')
    names = set()
    for field in value:
        if (not isinstance(field, dict) or set(field) != {'name', 'label', 'secret'}
                or not isinstance(field['name'], str)
                or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,79}', field['name'])
                or field['name'] in names or field['name'] in ('PLUGIN_ROOT', 'PLUGIN_DATA', 'KEJTU_SHARED_ROOT')
                or not isinstance(field['label'], str) or not 1 <= len(field['label']) <= 100
                or any(ord(c) < 32 for c in field['label']) or type(field['secret']) is not bool):
            raise ValueError('Invalid package account field.')
        names.add(field['name'])
    return value


PLACES = ('${PLUGIN_ROOT}', '${PLUGIN_DATA}')


def package_value(value, root):
    """Expand the two package places exactly once, after any credential resolution."""
    places = {'${PLUGIN_ROOT}': root, '${PLUGIN_DATA}': '/plugin-data'}
    return re.sub(r'\$\{PLUGIN_(?:ROOT|DATA)\}', lambda match: places[match[0]], value)


def working_folder(value):
    """Where a package's program starts: its package root, its data folder, or a plain path inside one.

    Returns the one saved spelling, ``${PLUGIN_ROOT}[/path]`` or ``${PLUGIN_DATA}[/path]``. A path
    without either is taken inside the package root. Nothing can name a place outside the two.
    """
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError('A working folder is a short path inside the package or its data folder.')
    place, rest = next(((p, value[len(p):]) for p in PLACES if value == p or value.startswith(p + '/')), (PLACES[0], '/' + value))
    parts = [part for part in rest.split('/')[1:] if part != '.']
    if (value.startswith('/') or '\\' in value or '$' in rest
            or any(not part or part == '..' or any(ord(c) < 32 or ord(c) == 127 for c in part) for part in parts)):
        raise ValueError('A working folder is a short path inside the package or its data folder.')
    return '/'.join([place, *parts])


def connection(raw, *, saved=False):
    """Normalize client configuration, never execute it or infer tool grants.

    A new entry must name an executable program. A ``saved`` entry is checked for
    structure only: its program was resolved when it was added, and a program that
    is missing now makes only that connection unavailable (see ``available``).

    Only a saved entry may hold a working folder (``cwd``), and only a package's
    program has one: an import never accepts it.
    """
    if not isinstance(raw, dict):
        raise ValueError('Each connection must be an object.')
    common = {'description', 'type', 'transport'}
    kind = raw.get('type', raw.get('transport', 'http' if 'url' in raw else 'stdio'))
    if kind == 'streamable-http':
        kind = 'http'
    if 'type' in raw and 'transport' in raw:
        raise ValueError('Use one transport field.')
    description = raw.get('description', '')
    if not isinstance(description, str) or len(description) > 240 or '\0' in description:
        raise ValueError('Description must contain at most 240 characters.')
    row = dict(description=description, transport=kind, enabled=False, allowed_tools=[], repeatable_tools=[], requires_web=False)
    if kind == 'stdio':
        if set(raw) - common - {'command', 'args', 'env', 'read_paths', 'write_paths', 'network', 'protocol_mode'} - ({'cwd', 'inputs'} if saved else set()):
            raise ValueError('Unknown local connection field. Supported: command, args, env, read_paths, write_paths, network.')
        command = raw.get('command')
        if not isinstance(command, str) or '\0' in command or not command or len(command) > 4096:
            raise ValueError('A local executable is required.')
        from .windows_connections import known, validate
        if sys.platform == 'win32' and known(command):
            validate(raw)
            row.update(command=command, args=[], env={}, read_paths=[], write_paths=[], network=True,
                requires_web=command == '@kejtu/browser')
            return row
        if saved:
            if not Path(command).is_absolute():
                raise ValueError('A saved local connection names its program by absolute path.')
            resolved = command
        else:
            resolved = command if Path(command).is_absolute() else shutil.which(command, path='/usr/bin:/bin')
            if not resolved or not Path(resolved).is_file() or not os.access(resolved, os.X_OK):
                raise ValueError('Executable not found. Install it first or supply its absolute path.')
        network = raw.get('network', False)
        if type(network) is not bool:
            raise ValueError('Network access must be true or false.')
        row.update(command=resolved, args=strings(raw.get('args', [])), env=mapping(raw.get('env', {})),
            read_paths=paths(raw.get('read_paths', [])), write_paths=paths(raw.get('write_paths', [])), network=network)
        if 'protocol_mode' in raw:
            if raw['protocol_mode'] not in ('auto', 'legacy'):
                raise ValueError('Unsupported MCP handshake mode.')
            row['protocol_mode'] = raw['protocol_mode']
        if 'inputs' in raw:
            row['inputs'] = input_fields(raw['inputs'])
        if 'cwd' in raw:
            # Present only when chosen, so entries saved before it existed keep their identity.
            if working_folder(raw['cwd']) != raw['cwd']:
                raise ValueError('A saved working folder uses its one saved spelling.')
            row['cwd'] = raw['cwd']
    elif kind == 'http':
        if set(raw) - common - {'url', 'headers'}:
            raise ValueError('Unknown remote connection field. Supported: url and headers.')
        url = raw.get('url')
        if not isinstance(url, str) or not 1 <= len(url) <= 2048 or any(ord(c) <= 32 for c in url) or '\\' in url:
            raise ValueError('Use an HTTPS URL, or loopback HTTP for a local server.')
        parsed = urlsplit(url)
        _ = parsed.port
        if (not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment
                or not (parsed.scheme == 'https' or parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1'))):
            raise ValueError('Use an HTTPS URL, or loopback HTTP for a local server.')
        row.update(url=url, headers=mapping(raw.get('headers', {})))
    else:
        raise ValueError('Supported transports: stdio and Streamable HTTP. Legacy SSE is not supported.')
    return row


def references(value, literal):
    """Saved keyring references for credential fields: ``{field: identity}``.

    Only a saved row holds them. An import never accepts them (``connection``
    refuses unknown fields), so a pasted configuration cannot point at another
    connection's saved values.
    """
    if (not isinstance(value, dict) or not value or len(value) + len(literal) > 24
            or len(set(value.values())) != len(value) or any(
                not isinstance(k, str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_-]{0,79}', k) or k in literal
                or not isinstance(v, str) or not re.fullmatch('[0-9a-f]{32}', v) for k, v in value.items())):
        raise ValueError('Invalid credential references.')
    return value


def backup_copy(value, own=lambda name, row: False):
    """Saved connections as a backup keeps them, and what that leaves out. Changes nothing.

    ``value`` is what ``Connections.read`` returned. Every value in a connection's
    ``env`` (a local program) or ``headers`` (a remote one) is left out, including
    one an earlier version saved in the file: each is treated as a key, and no rule
    about names could tell which ones really are. The field names stay, so the
    entry still says what has to be supplied again. Keyring references stay too:
    they are random identities, never values, and mean nothing on another computer.

    ``own(name, row)`` names the entries whose values Kejtu wrote itself. Those are
    launch settings, not keys someone entered, and are kept.

    Returns ``(text, left_out, keyring)``: the settings file's text for the backup,
    and two lists of ``{connection, fields}``: values left out of this backup, and
    values that were never in the file because the keyring holds them.
    """
    copy = parse(encode(value))
    left_out, keyring = [], []
    for name, row in sorted(copy['servers'].items()):  # both lists are in name order
        field = 'env' if row['transport'] == 'stdio' else 'headers'
        if row.get('secrets'):
            keyring.append(dict(connection=name, fields=sorted(row['secrets'])))
        if row.get('oauth'):
            keyring.append(dict(connection=name, fields=['account sign-in']))
        filled = sorted(key for key, held in row.get(field, {}).items() if held)
        if filled and not own(name, value['servers'][name]):
            row[field] = {key: '' for key in row[field]}
            left_out.append(dict(connection=name, fields=filled))
    return encode(copy) + '\n', left_out, keyring


def restore_copy(value):
    """Restore connection descriptions with tools off and account sign-ins unconfirmed.

    A saved connected flag describes the old installation, not this computer's
    credentials. Keep only the existing opaque references; never consult a keyring
    or initiate authorization during restore. Explicit Settings actions own those.
    """
    copy = parse(encode(value))
    for row in copy['servers'].values():
        row['enabled'] = False
        if row.get('oauth'):
            row['oauth']['connected'] = False
    return encode(copy) + '\n'


def available(row):
    """Whether a saved connection can start now: its local program is present and executable.

    Remote connections and Windows packaged programs (``@kejtu/…`` names, never a
    path; outside Windows a saved entry cannot hold one) are resolved where they start.
    """
    if row['transport'] != 'stdio':
        return True
    from .windows_connections import known
    if known(row['command']):
        return True
    command = Path(row['command'])
    return command.is_file() and os.access(command, os.X_OK)


def imported(text):
    if len(text.encode()) > 10000:
        raise ValueError('Import is limited to 10 KB. Add larger configurations in separate groups.')
    value = parse(text)
    if not isinstance(value, dict) or set(value) != {'mcpServers'} or not isinstance(value['mcpServers'], dict):
        raise ValueError('Paste a configuration with an mcpServers object.')
    if FILES_SERVER in value['mcpServers']:
        raise ValueError('The built-in Files connection is managed in Settings → Files.')
    if not 1 <= len(value['mcpServers']) <= 16:
        raise ValueError('Import 1–16 connections at a time.')
    result = {}
    for name, raw in value['mcpServers'].items():
        if not NAME.fullmatch(name):
            raise ValueError('Connection names need 1–64 letters, digits, underscores or hyphens.')
        result[name] = connection(raw)
    return result


class Connections:
    def __init__(self, directory):
        self.path = Path(directory) / 'mcp-settings.json'

    def read(self):
        try:
            source = open_private_regular(self.path)
        except FileNotFoundError:
            return dict(version=1, revision=0, servers={})
        except OSError:
            raise Failure('mcp_configuration', 'MCP settings could not be opened safely.') from None
        try:
            with source:
                raw = source.read(1048577)
            if len(raw) > 1048576:
                raise ValueError('Connection settings are too large.')
            value = parse(raw)
            if (set(value) != ({'version','revision','servers','files'} if value.get('version')==2 else {'version','revision','servers'})
                    or value['version'] not in (1,2) or type(value['revision']) is not int or value['revision'] < 0):
                raise ValueError('Invalid connection settings version.')
            if value['version']==2:validate_files(value['files'])
            if not isinstance(value['servers'], dict) or len(value['servers']) > 16:
                raise ValueError('Invalid connection list.')
            for name, row in value['servers'].items():
                if name==FILES_SERVER:raise ValueError('The built-in Files connection is managed by shared settings.')
                if not NAME.fullmatch(name) or type(row.get('enabled')) is not bool or type(row.get('requires_web')) is not bool:
                    raise ValueError('Invalid connection permission.')
                allowed = strings(row['allowed_tools'], MAX_TOOLS)
                if len(allowed) != len(set(allowed)) or any(not TOOL.fullmatch(v) for v in allowed):
                    raise ValueError('Permissions must name exact unique tools.')
                if not set(strings(row['repeatable_tools'], MAX_TOOLS)) <= set(allowed):
                    raise ValueError('Repeatable tools must be permitted.')
                # A Shelf installation marks the connections it registered, so its own
                # entry can be told apart from one added or replaced by hand. An import
                # never accepts the marker (``connection`` refuses unknown fields).
                if 'installation' in row and not (isinstance(row['installation'], str)
                                                  and re.fullmatch('[0-9a-f]{32}', row['installation'])):
                    raise ValueError('Invalid installation marker.')
                core = {k:v for k,v in row.items() if k not in ('enabled','allowed_tools','repeatable_tools','requires_web','installation','secrets','package_artifact','oauth')}
                normal = connection(core, saved=True)  # a missing program affects only its own connection
                if ('cwd' in normal or 'inputs' in normal) and 'installation' not in row:
                    raise ValueError('Only a package’s program has a working folder.')
                if 'package_artifact' in row:
                    from .trees import artifact
                    if 'installation' not in row or row['transport'] != 'stdio':
                        raise ValueError('Only a package program carries its recovery pin.')
                    try:
                        if artifact(row['package_artifact']) != row['package_artifact']:
                            raise ValueError('Invalid package recovery pin.')
                    except Failure:
                        raise ValueError('Invalid package recovery pin.') from None
                if row.get('inputs') and any(f['name'] not in {*row.get('env', {}), *row.get('secrets', {})} for f in row['inputs']):
                    raise ValueError('A package account field has no binding.')
                if 'secrets' in row:
                    references(row['secrets'], normal.get('env', normal.get('headers', {})))
                if 'oauth' in row:
                    auth = row['oauth']
                    if (normal['transport'] != 'http' or normal['headers'] or row.get('secrets')
                            or not isinstance(auth, dict) or set(auth) != {'secret', 'connected'}
                            or type(auth['connected']) is not bool or not isinstance(auth['secret'], str)
                            or not re.fullmatch('[0-9a-f]{32}', auth['secret'])):
                        raise ValueError('Invalid saved account sign-in.')
            return value
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            raise Failure('mcp_configuration', 'MCP settings could not be validated. Connections are unavailable until repaired.') from None

    def save(self, value, revision):
        if self.read()['revision'] != revision:
            raise Failure('mcp_conflict', 'Connections changed. Refresh before saving again.')
        value = dict(value, version=2 if 'files' in value else 1, revision=revision+1)
        raw = encode(value)
        if len(raw.encode()) > 1048576:
            raise Failure('mcp_configuration', 'Connection settings exceed 1 MB.')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == 'win32':
            # The shared native storage owner creates/checks a private DACL and
            # publishes atomically. Never replace an unreadable saved policy.
            try:
                atomic_write(self.path, (raw+'\n').encode('utf-8'), prefix='.mcp-', published=lambda: None)
                if self.read() != value:
                    raise OSError('Settings readback changed.')
            except OSError:
                raise Failure('mcp_configuration', 'File access could not be confirmed. Refresh the saved settings before trying again.') from None
            return value
        with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw+'\n'); handle.flush(); os.fsync(handle.fileno())
        try:
            temporary.replace(self.path)
            fd = os.open(self.path.parent, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally:
            temporary.unlink(missing_ok=True)
        return value

    def status(self):
        value = self.read()
        rows = []
        for name, row in value['servers'].items():
            # Credential values, command arguments and URL query strings can all
            # contain secrets. Return a deliberately small operator projection.
            address = row['command'] if row['transport'] == 'stdio' else urlsplit(row['url']).hostname
            # Where this connection's credential values are kept: the desktop keyring
            # (only references are saved here), this settings file (a connection saved
            # before keys moved to the keyring), or nowhere because it has none.
            literal, secrets = row.get('env', row.get('headers', {})), row.get('secrets', {})
            storage = 'settings' if any(literal.values()) else 'keyring' if secrets or row.get('oauth') else 'none'
            rows.append(dict(id=name, description=row['description'], enabled=row['enabled'], transport=row['transport'],
                address=address, allowed_tools=row['allowed_tools'], repeatable_tools=row['repeatable_tools'],
                requires_web=row['requires_web'], network=row.get('network', True), available=available(row),
                read_paths=row.get('read_paths', []), write_paths=row.get('write_paths', []),
                credential_fields=[*literal, *secrets, *(['account sign-in'] if row.get('oauth') else [])], credential_storage=storage,
                **({'installation_id': row['installation']} if row.get('installation') else {}),
                **({'inputs': row['inputs'], 'inputs_ready': all(f['name'] in secrets or bool(literal.get(f['name']))
                    for f in row['inputs'])} if row.get('inputs') else {})))
        return dict(revision=value['revision'], servers=rows)
