"""Disposable MCP SDK process; one serial connection per server and chat job.

Only this module imports the optional SDK. Its standard MCP transport handles
version negotiation; stdout is reserved for the application IPC envelope.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import json
import os
from pathlib import Path
import sys

from ...core.mcp import MAX_TOOLS, digest
from ...core.records import Failure

MAX_BYTES = 2_000_000


def sandbox(row):
    if sys.platform == 'win32':
        raise Failure('mcp_unavailable', 'Local connected tools are not available in this Windows test build yet. No tool was started.')
    args = ['/usr/bin/bwrap', '--unshare-user', '--unshare-pid', '--unshare-ipc',
            '--unshare-uts', '--die-with-parent', '--new-session', '--cap-drop', 'ALL',
            '--ro-bind', '/usr', '/usr', '--proc', '/proc', '--dev', '/dev',
            '--tmpfs', '/tmp', '--dir', '/etc', '--clearenv',
            '--setenv', 'HOME', '/tmp', '--setenv', 'PATH', '/usr/bin:/bin',
            '--setenv', 'LANG', 'C.UTF-8']
    if not row['network']:
        args += ['--unshare-net']
    if row.get('cache'):
        args += ['--bind', row['cache'], '/mcp-cache',
                 '--setenv', 'npm_config_cache', '/mcp-cache/npm',
                 '--setenv', 'UV_CACHE_DIR', '/mcp-cache/uv',
                 '--setenv', 'XDG_CACHE_HOME', '/mcp-cache']
    if row.get('shared_root'):
        # Keep scratch separate from user paths under /tmp. A common-root
        # server must not "save" an unshared project into a private shadow tree.
        args += ['--tmpfs', '/mcp-tmp', '--setenv', 'HOME', '/mcp-tmp',
                 '--setenv', 'TMPDIR', '/mcp-tmp']
    for name in ('lib', 'lib64', 'bin', 'sbin'):
        path = Path('/') / name
        if path.is_symlink():
            args += ['--symlink', os.readlink(path), str(path)]
        elif path.exists():
            args += ['--ro-bind', str(path), str(path)]
    for name in ('resolv.conf', 'nsswitch.conf', 'hosts', 'ssl', 'fonts', 'ld.so.cache', 'os-release'):
        path = Path('/etc') / name
        if path.exists():
            args += ['--ro-bind', str(path.resolve()), str(path)]
    def bind(value, flag):
        path=Path(value)
        if not path.is_dir() or str(path.resolve())!=value:
            raise ValueError('An approved MCP directory is unavailable or has changed identity.')
        args.extend([flag,value,value])
    shared=row.get('shared_folders')
    if shared is None:
        for key,flag in (('read_paths','--ro-bind'),('write_paths','--bind')):
            for value in row[key]:bind(value,flag)
    else:
        # Parent mounts must precede children: a read-only child of writable Home
        # stays read-only; a writable workspace inside a read-only parent works.
        for grant in sorted(shared,key=lambda r:len(Path(r['path']).parts)):
            bind(grant['path'],'--bind' if grant['access']=='write' else '--ro-bind')
        # Tool code/dependencies never become writable through a broad Home grant.
        for value in sorted(row['read_paths'],key=lambda p:len(Path(p).parts)):
            bind(value,'--ro-bind')
    # This applies to legacy connection mounts too, before Files is configured.
    # Mask broad parent grants last, then restore only exact approved program
    # dependencies. The temporary parent stays writable only during mount setup.
    private=row.get('private_paths',[])
    for value in private:
        path=Path(value)
        if not path.is_dir() or str(path.resolve())!=value:
            raise ValueError('A private app directory is unavailable or has changed identity.')
        args.extend(['--tmpfs',value])
    for value in row.get('private_read_paths',[]):
        if value not in row['read_paths'] or not any(Path(p) in Path(value).parents for p in private):
            raise ValueError('An unapproved private program mount was requested.')
        bind(value,'--ro-bind')
    for value in reversed(private):
        args.extend(['--remount-ro',value])
    for key, value in row['env'].items():
        args += ['--setenv', key, value]
    if row.get('shared_root') and not any(r['path']=='/tmp' and r['access']=='write' for r in shared):
        # Only this tmpfs mount becomes read-only; explicit child write mounts
        # keep their saved access. An explicitly writable /tmp stays writable.
        args += ['--remount-ro', '/tmp']
    return args + ['--remount-ro', '/', '--chdir', '/mcp-tmp' if row.get('shared_root') else '/tmp',
                   '--', row['command'], *row['args']]


def dump(value):
    return value.model_dump(mode='json', by_alias=True, exclude_none=True)


async def catalogue(client):
    from mcp_types import PaginatedRequestParams
    cursor, seen, rows = None, set(), []
    for _ in range(32):
        page = await client.session.list_tools(params=PaginatedRequestParams(cursor=cursor))
        rows.extend(dump(tool) for tool in page.tools)
        if len(rows) > MAX_TOOLS or len(json.dumps(rows).encode()) > MAX_BYTES:
            raise ValueError('MCP tool catalogue exceeds the supported size.')
        cursor = page.next_cursor
        if cursor is None:
            names = [row['name'] for row in rows]
            if len(names) != len(set(names)):
                raise ValueError('MCP server advertised ambiguous duplicate tool names.')
            return rows
        if cursor in seen:
            raise ValueError('MCP server repeated a tool-list cursor.')
        seen.add(cursor)
    raise ValueError('MCP tool catalogue exceeded its page allowance.')


def validate_arguments(schema, arguments):
    from jsonschema import validators
    from referencing import Registry
    from referencing.exceptions import NoSuchResource
    def refuse(uri):
        raise NoSuchResource(ref=uri)
    validator = validators.validator_for(schema)
    validator.check_schema(schema)
    validator(schema, registry=Registry(retrieve=refuse)).validate(arguments)


async def main():
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
    initial = json.loads(sys.stdin.buffer.readline(1_048_577))
    row = initial['server']
    if sys.platform == 'win32':
        from .windows_runtime import is_builtin, own_process_tree
        if not is_builtin(row):
            print(json.dumps(dict(ok=False, fatal=True, not_executed=True, error='Local connected tools are not available in this Windows test build yet. No tool was started.')), flush=True)
            return
        import site
        runtime = Path(__file__).resolve().parents[4] / 'package-runtime/mcp/python'
        site.addsitedir(str(runtime))
        own_process_tree()
    # The parent owns the deadline, cancellation and connection lifetime. No
    # sampling, elicitation or filesystem-roots callback is supplied to a server.
    from importlib.metadata import version
    if version('mcp') != '2.2.0':
        raise ValueError('This MCP integration requires the pinned SDK 2.2.0.')
    from mcp import Client, StdioServerParameters
    from mcp.client.streamable_http import streamable_http_client
    from mcp_types import CallToolResult, Implementation
    import httpx2

    async with AsyncExitStack() as stack:
        if row['transport'] == 'stdio':
            if sys.platform == 'win32':
                from .windows_runtime import launch
                command, args, env = launch(row)
                transport = StdioServerParameters(command=command, args=args, env=env,
                    cwd=row['cache'])
            else:
                args = sandbox(row)
                transport = StdioServerParameters(command=args[0], args=args[1:], env={
                    'HOME': '/tmp', 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        else:
            http = await stack.enter_async_context(httpx2.AsyncClient(
                headers=row['headers'], trust_env=False, follow_redirects=False,
                timeout=httpx2.Timeout(60, connect=10)))
            transport = streamable_http_client(row['url'], http_client=http)
        client = await stack.enter_async_context(Client(transport, read_timeout_seconds=60,
            client_info=Implementation(name='kejtu', version='A049'), input_required_max_rounds=0))
        while True:
            line = await asyncio.to_thread(sys.stdin.buffer.readline, 65_537)
            if not line:
                break
            dispatched = False
            try:
                if len(line) > 65_536:
                    raise ValueError('MCP request exceeded its allowance.')
                request = json.loads(line)
                tools = await catalogue(client)
                if request['action'] == 'tools':
                    result = {'tools': tools}
                elif request['action'] == 'call':
                    tool = next((t for t in tools if t['name'] == request['tool']), None)
                    if tool is None or digest(tool) != request['schema_revision']:
                        raise ValueError('The server changed this tool. Discover its current definition before calling it.')
                    def self_contained(value):
                        if isinstance(value, dict):
                            for key, item in value.items():
                                if key in ('$ref', '$dynamicRef') and (not isinstance(item, str) or not item.startswith('#')):
                                    raise ValueError('External JSON Schema references are not fetched by this MCP host.')
                                self_contained(item)
                        elif isinstance(value, list):
                            for item in value:
                                self_contained(item)
                    self_contained(tool)
                    validate_arguments(tool['inputSchema'], request['arguments'])
                    # Drive exactly one call. Input-required responses cannot
                    # cause hidden retries, model requests, logins or consent.
                    dispatched = True
                    response = await client.session.call_tool(request['tool'], request['arguments'],
                        read_timeout_seconds=request['timeout'], allow_input_required=True)
                    if not isinstance(response, CallToolResult):
                        raise ValueError('The server requested additional input; no automatic continuation was executed. Check the service before retrying.')
                    result = {'result': dump(response)}
                else:
                    raise ValueError('Unsupported MCP worker operation.')
                encoded = json.dumps({'ok': True, **result}, ensure_ascii=False, allow_nan=False)
                if len(encoded.encode()) > MAX_BYTES:
                    raise ValueError('MCP response exceeded 2 MB. Completion may have occurred; inspect the service before retrying.')
            except Exception as exc:
                message = str(exc)[:1000]
                for value in (*row.get('headers', {}).values(), *row.get('env', {}).values()):
                    if value:
                        message = message.replace(value, '[credential]')
                encoded = json.dumps({'ok': False, 'not_executed': not dispatched,
                    'error': type(exc).__name__ + ': ' + message})
            print(encoded, flush=True)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as exc:
        # Connection failures may contain credentials. Keep their raw diagnostics
        # out of command results; endpoint/configuration are known to the operator.
        print(json.dumps({'ok': False, 'fatal': True, 'error': f'MCP connection failed ({type(exc).__name__}). Check the configured server and credentials.'}), flush=True)
