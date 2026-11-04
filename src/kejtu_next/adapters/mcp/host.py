"""Connection management shared by settings and foreground MCP sessions."""
import asyncio
import json
from pathlib import Path
import sys

from .client import Connection
from .files import Files, FILES_SERVER, SHARED_ROOT
from .config import Connections, imported, strings, paths
from ...core.mcp import TOOL, MAX_TOOLS, digest
from ...core.records import Failure


class McpHost:
    def __init__(self, directory, *, factory=Connection, root=None):
        self.connections = Connections(directory)
        self.factory = factory
        self.root = Path(root) if root else Path(__file__).resolve().parents[4]
        self.files = Files(self)
        self.inspections = {}
        self.catalogues = {}
        self.lock = asyncio.Lock()

    def settings(self):
        return self.files.settings()

    def manifest(self):
        value = self.settings()
        return dict(revision=value['revision'], servers=[dict(id=name, description=row['description'],
            permitted_tools=len(row['allowed_tools']), requires_web=row['requires_web'],
            **({'workspace':value['files']['workspace'],'folders':row['shared_folders']} if row.get('shared_folders') else {}))
            for name,row in value['servers'].items() if row['enabled'] and row['allowed_tools']])

    def connection(self, name, row):
        from .windows_runtime import is_builtin
        if sys.platform == 'win32' and not is_builtin(row):
            raise Failure('mcp_not_executed', 'This local connection is not available in this Windows test build yet. No tool was started.')
        if row['transport'] == 'stdio':
            if SHARED_ROOT in row['args']:
                raise Failure('files_required', 'Share a folder and select this tool in Settings → Files before testing or using it.')
            cache = self.connections.path.parent / 'mcp-cache' / name
            cache.mkdir(parents=True, exist_ok=True, mode=0o700)
            if cache.resolve() != cache or cache.is_symlink():
                raise Failure('mcp_cache', 'The connection cache must be a real private directory.')
            row = dict(row, cache=str(cache))
        return self.factory(row)

    def add_browser(self, revision):
        try:
            if sys.platform == 'win32':
                from .windows_connections import resolve
                resolve(self.root, dict(command='@kejtu/browser', network=True))
                browser, runtime = self.root, self.root
            else:
                return self._add_linux_browser(revision)
        except (ValueError, KeyError, OSError):
            raise Failure('mcp_browser_installation', 'Browser setup is incomplete. Repair the Kejtu installation, then try Add browser again.') from None
        return self._save_browser(revision, browser, runtime, windows=True)

    def _add_linux_browser(self, revision):
        try:
            installation = json.loads((self.root/'.state/mcp-runtime/browser.json').read_text())
            browser = Path(installation['executable']).resolve(strict=True)
            runtime = (self.root/'.state/mcp-runtime/playwright').resolve(strict=True)
            if not (runtime/'node_modules/@playwright/mcp/cli.js').is_file():
                raise ValueError()
        except (ValueError, KeyError, OSError):
            raise Failure('mcp_browser_installation', 'Browser setup is incomplete. Run ./kejtu setup-web from the Kejtu folder, then try Add browser again.') from None
        return self._save_browser(revision, browser, runtime)

    def _save_browser(self, revision, browser, runtime, *, windows=False):
        tools = ['browser_navigate','browser_navigate_back','browser_snapshot','browser_click',
            'browser_type','browser_fill_form','browser_press_key','browser_select_option','browser_hover',
            'browser_wait_for','browser_close']
        row = dict(description='Interact with public websites in a separate temporary browser.',
            transport='stdio', enabled=True, allowed_tools=tools,
            repeatable_tools=['browser_snapshot','browser_wait_for','browser_navigate_back','browser_hover'],
            requires_web=True, command=str(Path(sys._base_executable).resolve()),
            args=['-B','-m','kejtu_next.adapters.web.mcp_browser','--runtime',str(runtime),'--browser',str(browser)],
            env={'PYTHONPATH':str(self.root/'src')}, read_paths=[] if windows else [str(self.root/'src'),str(runtime),str(browser.parents[1])],
            write_paths=[], network=True)
        if windows:
            row.update(command='@kejtu/browser', args=[], env={}, read_paths=[], write_paths=[])
        value = self.connections.read()
        if 'playwright' in value['servers']:
            raise Failure('mcp_exists', 'The browser is already configured. Use its existing controls.')
        if len(value['servers']) >= 16:
            raise Failure('mcp_configuration', 'At most 16 connections can be configured.')
        value['servers']['playwright'] = row
        self.connections.save(value, revision)
        return self.connections.status()

    def setup(self, name, revision, *, use_shared_folders=False):
        """Add one installed preset without replacing saved choices or grants."""
        value = self.connections.read()
        if value['revision'] != revision:
            raise Failure('mcp_conflict', 'Connections changed. Refresh before setting up included tools.')
        if name in value['servers']:
            return self.connections.status()
        if name == 'playwright':
            return self.add_browser(revision)
        from .presets import document
        row = document(self.root, name)
        self.files.validate_connection_paths(row['read_paths'], row['write_paths'])
        if len(value['servers']) >= 16:
            raise Failure('mcp_configuration', 'At most 16 connections can be configured.')
        value['servers'][name] = row
        if use_shared_folders and sys.platform == 'linux':
            from .files import grants
            config = value.get('files')
            if config and config['enabled'] and grants(config):
                # The setup action visibly opts these verified new document
                # tools into already-saved shares; no path/access is added.
                config['servers'] = [*config['servers'], name]
        self.connections.save(value, revision)
        return self.connections.status()

    async def inspect(self, name, offset=0):
        async with self.lock:
            value = self.settings()
            row = value['servers'].get(name)
            if row is None:
                raise Failure('mcp_missing', 'Connection not found.')
            revision = value['revision']
            def check():
                if self.connections.read()['revision'] != revision:
                    raise Failure('mcp_conflict', 'Connections changed during inspection. Test the connection again.')
            identity = digest(row)
            if offset:
                cached = self.catalogues.get(name)
                if cached is None or cached[:2] != (revision, identity):
                    raise Failure('mcp_inspect_first', 'Connections changed. Check this connection again before reading more tools.')
                tools = cached[2]
            else:
                # A fresh check must never leave an older successful discovery
                # usable if the current connection fails.
                self.inspections.pop(name, None)
                self.catalogues.pop(name, None)
                client = self.connection(name, row)
                try:
                    data = await client.request({'action':'tools'}, deadline=asyncio.get_running_loop().time()+30, check=check)
                finally:
                    await client.close()
                check()
                tools = data['tools']
                self.inspections[name] = (identity, {t['name'] for t in tools})
                # Every settings page belongs to this exact discovery. A large
                # catalogue must not launch a new process for every 16 tools.
                self.catalogues[name] = (revision, identity, tools)
            # UI discovery pages are bounded independently of the model context.
            rows = [dict(name=t['name'], description=t.get('description','')[:1000],
                input_schema=json.dumps(t['inputSchema'], ensure_ascii=False)) for t in tools[offset:offset+16]]
            if any(len(r['input_schema'].encode()) > 12000 for r in rows):
                raise Failure('mcp_schema_size', 'A tool schema exceeds the settings display limit. It was not silently shortened.')
            return dict(server=name, revision=revision, tools=rows,
                next_offset=offset+len(rows) if offset+len(rows)<len(tools) else None, total_tools=len(tools))

    def import_config(self, text, revision):
        try:
            additions = imported(text)
        except (ValueError, TypeError, KeyError) as error:
            raise Failure('mcp_configuration', str(error)) from None
        value = self.connections.read()
        for row in additions.values():
            if row['transport']=='stdio':
                self.files.validate_connection_paths(row['read_paths'],row['write_paths'])
        if 'files' in value and any(row.get('write_paths') for row in additions.values()):
            raise Failure('files_managed','Choose writable shared folders in Settings → Files; connection imports may specify read-only tool dependencies.')
        # Replacing any connection resets all grants. A copied example cannot
        # inherit permission to run a different executable or remote service.
        value['servers'].update(additions)
        if 'files' in value:
            value['files']['servers']=[name for name in value['files']['servers'] if name not in additions]
        if len(value['servers']) > 16:
            raise Failure('mcp_configuration', 'At most 16 connections can be configured.')
        self.connections.save(value, revision)
        self.inspections.clear()
        self.catalogues.clear()
        return self.connections.status()

    def update(self, args):
        value = self.connections.read()
        row = value['servers'].get(args['server'])
        if row is None:
            raise Failure('mcp_missing', 'Connection not found.')
        action = args['action']
        if action == 'remove':
            del value['servers'][args['server']]
            if 'files' in value:
                value['files']['servers']=[name for name in value['files']['servers'] if name!=args['server']]
        elif action == 'disable':
            row['enabled'] = False
        elif action == 'permissions':
            # Reject invalid permissions before saving, including callers outside
            # native IPC; an unreadable settings file disables every connection.
            try:
                names = strings(args['allowed_tools'], MAX_TOOLS)
                repeatable = strings(args['repeatable_tools'], MAX_TOOLS)
            except ValueError:
                raise Failure('mcp_permissions', 'Invalid tool permission list.') from None
            inspection = self.inspections.get(args['server'])
            if (inspection is None or inspection[0] != digest(self.settings()['servers'][args['server']])
                    or not set(names) <= inspection[1]):
                raise Failure('mcp_inspect_first', 'Test this connection before choosing its current tools.')
            if len(names) != len(set(names)) or any(not TOOL.fullmatch(n) for n in names) or not set(repeatable) <= set(names):
                raise Failure('mcp_permissions', 'Choose exact advertised tools; repeatable tools must also be permitted.')
            row.update(enabled=args['enabled'], allowed_tools=names, repeatable_tools=repeatable)
        elif action == 'access':
            from .windows_connections import known
            if sys.platform == 'win32' and known(row.get('command')):
                raise Failure('mcp_access', 'This packaged program uses fixed settings and normal Windows account access. Choose its permitted tools or disable it.')
            if row['transport'] != 'stdio':
                raise Failure('mcp_access', 'Local file access applies only to local servers.')
            try:
                reads, writes = paths(args['read_paths']), paths(args['write_paths'])
            except ValueError as error:
                raise Failure('mcp_access', str(error)) from None
            if 'files' in value and writes:
                raise Failure('files_managed','Manage writable folders in Settings → Files.')
            self.files.validate_connection_paths(reads,writes)
            row.update(network=args['network'], read_paths=reads, write_paths=writes,
                enabled=False, allowed_tools=[], repeatable_tools=[])
        else:
            raise Failure('mcp_action', 'Unknown connection action.')
        self.connections.save(value, args['expected_revision'])
        self.inspections.pop(args['server'], None)
        self.catalogues.pop(args['server'], None)
        return self.connections.status()
