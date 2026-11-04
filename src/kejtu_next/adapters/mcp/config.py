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


def connection(raw):
    """Normalize client configuration, never execute it or infer tool grants."""
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
        if set(raw) - common - {'command', 'args', 'env', 'read_paths', 'write_paths', 'network'}:
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
        resolved = command if Path(command).is_absolute() else shutil.which(command, path='/usr/bin:/bin')
        if not resolved or not Path(resolved).is_file() or not os.access(resolved, os.X_OK):
            raise ValueError('Executable not found. Install it first or supply its absolute path.')
        network = raw.get('network', False)
        if type(network) is not bool:
            raise ValueError('Network access must be true or false.')
        row.update(command=resolved, args=strings(raw.get('args', [])), env=mapping(raw.get('env', {})),
            read_paths=paths(raw.get('read_paths', [])), write_paths=paths(raw.get('write_paths', [])), network=network)
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
                core = {k:v for k,v in row.items() if k not in ('enabled','allowed_tools','repeatable_tools','requires_web')}
                connection(core)
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
            rows.append(dict(id=name, description=row['description'], enabled=row['enabled'], transport=row['transport'],
                address=address, allowed_tools=row['allowed_tools'], repeatable_tools=row['repeatable_tools'],
                requires_web=row['requires_web'], network=row.get('network', True),
                read_paths=row.get('read_paths', []), write_paths=row.get('write_paths', []),
                credential_fields=list(row.get('env', row.get('headers', {})))))
        return dict(revision=value['revision'], servers=rows)
