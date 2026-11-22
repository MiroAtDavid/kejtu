"""Connection management shared by settings and foreground MCP sessions."""
import asyncio
import json
from pathlib import Path
import sys

from .client import Connection
from .files import Files, FILES_SERVER, SHARED_ROOT
from .config import PLACES, Connections, available, backup_copy, connection, imported, strings, paths, working_folder, package_value
from .backups import PackageBackups
from .data import PackageData
from .secrets import credential_fields, credential_refs, credential_value
from .trees import PackageTrees, relative_command
from .layout import private_data
from ...core.mcp import TOOL, MAX_TOOLS, INSPECTION_SCHEMA_BYTES, digest
from ...core.reference import exact_page
from ...core.records import Failure


def backup_settings(directory, root, browser=None):
    """The connection settings as a backup keeps them; None when none are saved. Reads only.

    No key value goes into a backup (``backup_copy``). Only the included browser
    keeps its exact, application-owned launch settings. Package values go
    through the ordinary credential owner. ``browser(root, row)`` is the sandbox owner's check of the browser's
    exact launch entry, supplied by the caller that wires the two together; the
    name of an entry never decides. A saved file that cannot be read safely
    raises: the caller must not copy it as it is.
    """
    connections = Connections(directory)
    if not connections.path.exists() and not connections.path.is_symlink():
        return None

    def own(name, row):
        if browser is None or row['transport'] != 'stdio':
            return False
        try:
            return browser(root, row) is not None
        except (OSError, ValueError, KeyError, StopIteration):
            return False  # it only looks like the browser: its values are someone's
    return backup_copy(connections.read(), own)


class McpHost:
    def __init__(self, directory, *, factory=None, root=None, secrets=None, trees=None):
        self.connections = Connections(directory)
        # Credential values live in the desktop keyring (``secrets``). The launch
        # owner reads them itself, right before its worker starts.
        self.secrets = secrets
        from .oauth import RemoteSignIn
        self.oauth = RemoteSignIn(self)
        self.factory = factory or (lambda row: Connection(row, secrets=self.secrets, authorize=self.oauth.authorize))
        self.root = Path(root) if root else Path(__file__).resolve().parents[4]
        # Program trees of Shelf packages, beside the included tools' runtimes.
        self.trees = trees if trees is not None else PackageTrees(
            self.root, data=private_data(self.connections.path.parent, self.root))
        # Data and cache folders of Shelf packages, beside the connection settings.
        self.data = PackageData(self.connections.path.parent)
        # What a backup takes of those folders, and what a restore left to do.
        self.backups = PackageBackups(self.connections.path.parent)
        self.files = Files(self)
        self.inspections = {}
        self.catalogues = {}
        self.lock = asyncio.Lock()
        # Only the explicitly selected Development procedure may retain its
        # terminal connection. These are process leases, never saved grants.
        self.terminal_sessions = {}
        self.terminals_closed = False

    async def take_terminal(self, conversation_id, revision):
        lease = self.terminal_sessions.get(conversation_id)
        if lease is None:
            return None
        lease['monitor'].cancel()
        await asyncio.gather(lease['monitor'], return_exceptions=True)
        if self.terminal_sessions.get(conversation_id) is not lease:
            return None  # Explicit Stop took ownership while the monitor joined.
        try:
            lease['check']()
            valid = (lease['revision'] == revision and
                     asyncio.get_running_loop().time() < lease['deadline'])
        except Failure:
            valid = False
        if not valid or self.terminals_closed:
            await lease['connection'].close()
            del self.terminal_sessions[conversation_id]
            return None
        del self.terminal_sessions[conversation_id]
        return lease['connection'], lease['deadline']

    async def keep_terminal(self, conversation_id, connection, revision, deadline, check):
        if self.terminals_closed or asyncio.get_running_loop().time() >= deadline:
            await connection.close()
            return
        check()
        await self.close_terminals(conversation_id)
        # Recheck after awaiting cleanup: shutdown may have started meanwhile.
        check()
        # The existing connection-count ceiling also bounds retained workers.
        if self.terminals_closed or asyncio.get_running_loop().time() >= deadline or len(self.terminal_sessions) >= 16:
            await connection.close()
            return
        lease = dict(connection=connection, revision=revision, deadline=deadline, check=check)
        self.terminal_sessions[conversation_id] = lease

        async def watch():
            try:
                while asyncio.get_running_loop().time() < deadline:
                    check()
                    await asyncio.sleep(.1)
            except asyncio.CancelledError:
                return  # The next turn or explicit close takes ownership.
            except Exception:
                pass  # Loss of permission/source eligibility closes the worker.
            # Keep ownership if verified cleanup itself fails.
            await connection.close()
            if self.terminal_sessions.get(conversation_id) is lease:
                del self.terminal_sessions[conversation_id]

        lease['monitor'] = asyncio.create_task(watch(), name='terminal-permission-lease')

    async def close_terminals(self, conversation_id=None, *, shutdown=False):
        if shutdown:
            self.terminals_closed = True
        for cid, lease in list(self.terminal_sessions.items()):
            if conversation_id is not None and cid != conversation_id:
                continue
            lease['monitor'].cancel()
            await asyncio.gather(lease['monitor'], return_exceptions=True)
            await lease['connection'].close()
            if self.terminal_sessions.get(cid) is lease:
                del self.terminal_sessions[cid]

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
        from .terminals import check_node
        check_node(self.root, name, row)
        if not available(row):
            raise Failure('mcp_unavailable', 'This connection’s program is missing. Reinstall it or remove the connection; nothing was started.')
        if row['transport'] == 'stdio':
            if SHARED_ROOT in row['args']:
                raise Failure('files_required', 'Share a folder and select this tool in Settings → Files before testing or using it.')
            # Launch folders are chosen here, never taken from a saved or imported entry.
            row = {key: value for key, value in row.items() if key not in ('cache', 'data', 'root')}
            if row.get('installation'):
                # A package's program writes only inside its installation's own folders,
                # never one keyed by a name that another connection may reuse later.
                data, cache = self.data.launch(row['installation'], name)
                row['data'] = str(data)
                located = self.trees.locate(row['command'])
                if located:
                    row.update(self._package_launch(row, self.trees.path(located[0]), data))
            else:
                cache = self.connections.path.parent / 'mcp-cache' / name
                cache.mkdir(parents=True, exist_ok=True, mode=0o700)
                if cache.resolve() != cache or cache.is_symlink():
                    raise Failure('mcp_cache', 'The connection cache must be a real private directory.')
            row['cache'] = str(cache)
            if sys.platform == 'linux':
                from ..linux_sandbox import browser_identity
                try:
                    identity = browser_identity(self.root, row, allow_previous=True)
                except (OSError, ValueError, KeyError, StopIteration):
                    raise Failure('mcp_browser_installation', 'The included browser runtime or connection changed. Repair its setup before using it.') from None
                # Internal launch metadata is never persisted or accepted from an
                # imported connection. The worker independently checks it again.
                row.pop('_browser_identity', None)
                if identity is not None:
                    if row['args'] != identity['args']:
                        row.update(args=identity['args'], read_paths=identity['read_paths'])
                        self.files.protect(row)
                    row['_browser_identity'] = identity
        return self.factory(row)

    @staticmethod
    def _package_launch(row, root, data):
        """How a package's program is started: its two places filled in, and where it starts.

        ``${PLUGIN_ROOT}`` is the program's own tree and ``${PLUGIN_DATA}`` its data
        folder as the program sees it. Both are filled in once, as text, in the
        arguments, the environment values and the working folder, and nowhere else.
        The program starts in its package root unless its entry names a folder inside
        the root or the data folder; that folder must be a real one, reached through
        no link.
        """
        if str(root) not in row['read_paths']:
            raise Failure('mcp_unavailable', 'This connection can no longer read its own program folder. Add the folder '
                          'back under Advanced, or reinstall the package; nothing was started.')
        seen = {PLACES[0]: str(root), PLACES[1]: '/plugin-data'}  # the data folder is mounted there
        held = {PLACES[0]: Path(root), PLACES[1]: Path(data)}

        def fill(text):  # one pass over the text as written; what is filled in is not read again
            return package_value(text, str(root))
        try:
            place, _, rest = working_folder(row.get('cwd', PLACES[0])).partition('/')
        except ValueError:
            raise Failure('mcp_configuration', 'This connection’s working folder is not valid. Nothing was started.') from None
        folder = held[place] / rest if rest else held[place]
        if folder.is_symlink() or not folder.is_dir() or folder.resolve() != folder:
            raise Failure('mcp_unavailable', 'This connection’s working folder is missing from its package, '
                          'or is not a plain folder. Reinstall the package; nothing was started.')
        return dict(root=str(root), cwd=seen[place] + ('/' + rest if rest else ''),
                    args=[fill(value) for value in row['args']], env={key: fill(value) for key, value in row['env'].items()})

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
        if 'playwright' in self.connections.read()['servers']:
            raise Failure('mcp_exists', 'The browser is already configured. Use its existing controls.')
        return self.register('playwright', row, revision)

    def setup(self, name, revision, *, use_shared_folders=False):
        """Add one installed preset without replacing saved choices or grants."""
        value = self.connections.read()
        if value['revision'] != revision:
            raise Failure('mcp_conflict', 'Connections changed. Refresh before setting up included tools.')
        if name in value['servers']:
            return self.connections.status()
        if name == 'playwright':
            return self.add_browser(revision)
        from .terminals import NAMES, terminal
        if name in NAMES:
            row = terminal(self.root, name)
            # Terminal setup is an individual action. Folder sharing has its own switch.
            use_shared_folders = False
        else:
            from .presets import document
            row = document(self.root, name)
        self.files.validate_connection_paths(row['read_paths'], row['write_paths'])
        return self.register(name, row, revision, use_shared_folders=use_shared_folders)

    @staticmethod
    def remote(name, url, description):
        """A verified remote connection for a Shelf package, through the one connection parser.

        Switched off, no tools granted, and it follows the Web switch.
        """
        if name == FILES_SERVER:
            raise Failure('mcp_configuration', 'The built-in Files connection is managed in Settings → Files.')
        try:
            row = connection(dict(url=url, description=description))
        except (ValueError, TypeError) as error:
            raise Failure('mcp_configuration', str(error)) from None
        row['requires_web'] = True
        return row

    def program(self, name, tree, command, args, description, env=None, cwd=None, inputs=None, protocol_mode=None, network=False):
        """A verified local program for a Shelf package: an executable inside its own installed tree.

        Switched off, no tools granted, only declared network access, and its own tree mounted,
        read-only. The tree may not be there yet: the entry is checked for structure
        and stays unavailable until its program is.

        ``env`` comes from the package and is stored through the credential owner. ``cwd`` is
        where the program starts, inside its tree or its data folder. Both, and the
        arguments, may name ``${PLUGIN_ROOT}`` and ``${PLUGIN_DATA}``; those are
        filled in when the program starts (``_package_launch``).
        """
        if sys.platform != 'linux':
            raise Failure('mcp_installation', 'Local programs from packages are not available on this platform yet.')
        if name == FILES_SERVER:
            raise Failure('mcp_configuration', 'The built-in Files connection is managed in Settings → Files.')
        try:
            row = connection(dict(command=str(Path(tree) / relative_command(command)), args=args, description=description,
                                  network=network, read_paths=[str(tree)], env=env or {},
                                  **({"inputs": inputs} if inputs else {}),
                                  **({"protocol_mode": protocol_mode} if protocol_mode else {}),
                                  **({'cwd': working_folder(cwd)} if cwd is not None else {})), saved=True)
        except (ValueError, TypeError) as error:
            raise Failure('mcp_configuration', str(error)) from None
        self.files.validate_connection_paths(row['read_paths'], row['write_paths'])
        return row

    def program_users(self, tree):
        """Names of saved connections that run from, or can read, this folder: whoever added them.

        A program inside the folder counts. So does a folder a connection may read,
        by the rule its sandbox applies (``Files.protect``): Kejtu's private folders
        are hidden from every connection and only the program folders it selected
        are shown again. Such a folder reaches this one when it is this folder, lies
        inside it, or contains it, so an interpreter reading the parent folder can
        run a script from here. A broader grant, such as Home, never shows it.
        """
        tree = Path(tree)

        def inside(value):
            path = Path(value)
            return path == tree or tree in path.parents

        def shown(row):
            row = dict(row)
            self.files.protect(row)
            return row['private_read_paths']
        return [name for name, row in self.connections.read()['servers'].items() if row['transport'] == 'stdio'
                and (inside(row['command']) or any(inside(path) or Path(path) in tree.parents for path in shown(row)))]

    @staticmethod
    def fingerprint(row):
        """Digest of a connection's configuration, without its on/off state, grants or marker."""
        # Account values are user choices, not changes to a package's declaration.
        # Ignore only declared bindings; every other launch field remains exact.
        row = dict(row)
        if row.get('inputs'):
            names = {field['name'] for field in row['inputs']}
            row['env'] = {**row.get('env', {}), **dict.fromkeys(names, '')}
            secrets = {k: v for k, v in row.get('secrets', {}).items() if k not in names}
            row.pop('secrets', None)
            if secrets:
                row['secrets'] = secrets
        return digest({k: v for k, v in row.items() if k not in ('enabled', 'allowed_tools', 'repeatable_tools', 'installation', 'package_artifact', 'oauth')})

    def source_fingerprint(self, row):
        """Compare a program's declaration across restores without reading its keys.

        Only the owner-derived executable/read root is relocatable. Literal env,
        arguments and the package's declared working folder remain exact text.
        """
        declaration = dict(row)
        located = self.trees.locate(row['command']) if row['transport'] == 'stdio' else None
        if located:
            root = str(self.trees.path(located[0]))
            declaration['command'] = '${PLUGIN_ROOT}/' + located[1]
            declaration['read_paths'] = ['${PLUGIN_ROOT}' if path == root else path for path in row['read_paths']]
        return self.fingerprint(declaration)

    def register(self, name, row, revision, *, replace=False, use_shared_folders=False, keys_handled=False):
        """Add one verified connection: the single path for included tools and Shelf packages.

        The caller supplies a row it has built and checked (an included preset, or a
        Shelf component through ``connection``). Other connections and their grants
        are untouched. A name already in use is refused unless ``replace`` is the
        caller's explicit choice; replacing resets that entry's grants and shares,
        as an import does.
        """
        if name == FILES_SERVER:
            raise Failure('mcp_configuration', 'The built-in Files connection is managed in Settings → Files.')
        value = self.connections.read()
        if value['revision'] != revision:
            raise Failure('mcp_conflict', 'Connections changed. Refresh before adding a connection.')
        if name in value['servers'] and not replace:
            raise Failure('mcp_exists', 'A connection with this name already exists. Choose to replace it, keep it, or cancel.')
        if credential_refs(value['servers'].get(name, {})) and not keys_handled:
            # Its saved keys would be left in the keyring. Replacing a keyed connection
            # goes through the import entry, which deletes them once it is replaced.
            raise Failure('mcp_credentials', 'This connection has saved keys. Replace or remove it in Settings → Connections first.')
        if name not in value['servers'] and len(value['servers']) >= 16:
            raise Failure('mcp_configuration', 'At most 16 connections can be configured.')
        if name in value['servers'] and 'files' in value:
            value['files']['servers'] = [n for n in value['files']['servers'] if n != name]
        value['servers'][name] = row
        if use_shared_folders and sys.platform == 'linux':
            from .files import grants
            config = value.get('files')
            if config and config['enabled'] and grants(config):
                # The setup action visibly opts these verified new document
                # tools into already-saved shares; no path/access is added.
                config['servers'] = [*config['servers'], name]
        self.connections.save(value, revision)
        self.inspections.pop(name, None)
        self.catalogues.pop(name, None)
        return self.connections.status()

    async def register_package(self, name, row, expected, publish, settle=None):
        """Use the credential owner's staged replacement, with a final ownership check.

        Values are untrusted regardless of whether they came from a package. Only
        references reach settings. Publication and its Shelf receipt stay synchronous.
        """
        async with self.lock, self.oauth.refresh_lock:
            if self.connections.read()['servers'].get(name) != expected:
                raise Failure('mcp_conflict', 'This connection changed while the package was being prepared. Refresh before trying again.')
            wanted = {field: value for field, value in row[credential_fields(row)].items() if value}
            retained = set(credential_refs(row))
            old = [value for value in credential_refs((expected or {})) if value not in retained]
            references = {}
            try:
                if wanted or old:
                    self._keyring()
                    if wanted:
                        references = await self.secrets.store(name, wanted)
                    else:
                        await self.secrets.usable(old[0])
                if settle is not None:
                    await settle()
                current = self.connections.read()
                if current['servers'].get(name) != expected:
                    raise Failure('mcp_conflict', 'This connection changed while the package was being prepared. Refresh before trying again.')
                protected = dict(row)
                if references:
                    field = credential_fields(row)
                    protected[field] = {k: v for k, v in row[field].items() if not v}
                    protected['secrets'] = dict(row.get('secrets', {}), **references)
                publish(protected, current['revision'])
            except BaseException:
                if references or old:
                    await self._forget_unreferenced([*references.values(), *old], quiet=True)
                raise
            if old:
                await self._forget_unreferenced(old)

    async def unregister_package(self, name, installation, revision, settle=None):
        """Remove only the selected package entry, with the ordinary keyring cleanup."""
        async with self.lock, self.oauth.refresh_lock:
            before = self.connections.read()
            row = before['servers'].get(name)
            if row is None:
                return 'absent'
            if row.get('installation') != installation:
                return 'replaced'
            if before['revision'] != revision:
                raise Failure('mcp_conflict', 'Connections changed. Refresh before removing the package.')
            old = list(credential_refs(row))
            if old:
                self._keyring()
                await self.secrets.usable(old[0])
            if settle is not None:
                await settle()
            try:
                self._apply(dict(server=name, action='remove', expected_revision=revision), keys_handled=True)
                self.connections.status()  # confirm the settings remain readable after publication
            except BaseException:
                if old:
                    await self._forget_unreferenced(old, quiet=True)
                raise
            if old:
                await self._forget_unreferenced(old)
            return 'removed'

    def unregister(self, name, installation, revision):
        """Remove a connection only while it still carries this installation's marker.

        Returns 'removed', 'absent' (already gone) or 'replaced' (now a different
        entry, added or replaced by hand, which is kept). Removal itself is the
        ordinary remove action.
        """
        row = self.connections.read()['servers'].get(name)
        if row is None:
            return 'absent'
        if row.get('installation') != installation:
            return 'replaced'
        self.update(dict(server=name, action='remove', expected_revision=revision))
        return 'removed'

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
            rows = []
            for tool in tools[offset:offset+16]:
                text = json.dumps(tool['inputSchema'], ensure_ascii=False)
                page = exact_page(tool['name'], text, 0, INSPECTION_SCHEMA_BYTES)
                item = dict(name=tool['name'], description=tool.get('description', '')[:1000],
                            input_schema=page.content, annotations=self._labels(tool))
                if page.end < page.total_bytes:
                    item['schema_page'] = dict(sha256=page.sha256, total_bytes=page.total_bytes,
                                               next_offset=page.end)
                rows.append(item)
            return dict(server=name, revision=revision, tools=rows,
                next_offset=offset+len(rows) if offset+len(rows)<len(tools) else None, total_tools=len(tools))

    def schema_page(self, name, tool, revision, sha256, offset):
        """Read the already inspected definition; never reconnect or execute a tool.

        Both connection identity and exact schema bytes must still match the page
        the UI holds. A new discovery under the same revision may change a schema.
        """
        value = self.settings()
        row = value['servers'].get(name)
        cached = self.catalogues.get(name)
        if (row is None or value['revision'] != revision or cached is None
                or cached[:2] != (revision, digest(row))):
            raise Failure('mcp_inspect_first', 'Connections changed. Check this connection again before reading its schema.')
        definition = next((t for t in cached[2] if t['name'] == tool), None)
        if definition is None:
            raise Failure('mcp_inspect_first', 'This tool is no longer in the checked connection. Check the connection again.')
        text = json.dumps(definition['inputSchema'], ensure_ascii=False)
        page = exact_page(tool, text, offset, INSPECTION_SCHEMA_BYTES)
        if page.sha256 != sha256:
            raise Failure('mcp_conflict', 'The tool schema changed. Check the connection again before reading more.')
        if page.reason:
            raise Failure('mcp_schema_page', 'The requested schema page is invalid. Check the connection again.')
        return dict(content=page.content, sha256=page.sha256, total_bytes=page.total_bytes,
                    offset=page.offset, end=page.end,
                    next_offset=page.end if page.end < page.total_bytes else None)

    @staticmethod
    def _labels(tool):
        """What a tool's publisher says about it, for the settings screen only.

        A publisher's word, not something Kejtu checked or enforces: it never grants a
        tool, limits one, or decides whether a call may repeat. Anything that is not a
        plain true/false or a short title is left out.
        """
        said = tool.get('annotations')
        said = said if isinstance(said, dict) else {}
        names = dict(readOnlyHint='read_only', destructiveHint='destructive', idempotentHint='idempotent', openWorldHint='open_world')
        labels = {ours: said[theirs] for theirs, ours in names.items() if type(said.get(theirs)) is bool}
        if isinstance(said.get('title'), str) and said['title'].strip():
            labels['title'] = ' '.join(said['title'].split())[:120]
        return labels

    def _additions(self, text):
        """Parse an import and check it against the saved settings; nothing is written."""
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
        if len({**value['servers'], **additions}) > 16:
            raise Failure('mcp_configuration', 'At most 16 connections can be configured.')
        return additions, value

    def import_config(self, text, revision, *, references=None, keys_handled=False):
        """Save imported connections and return the new status; a credential value is never written.

        A connection with env or header values needs their keyring ``references``
        (``{name: {field: identity}}``), and replacing one that has saved keys needs a
        caller that deletes them (``keys_handled``). ``import_configuration`` does both.
        """
        self._save_import(text, revision, references, keys_handled)
        return self.connections.status()

    def _save_import(self, text, revision, references, keys_handled):
        """The settings write of an import, and nothing after it: once this returns, it is published."""
        additions, value = self._additions(text)
        if not keys_handled and any(credential_refs(value['servers'].get(name, {})) for name in additions):
            raise Failure('mcp_credentials', 'Replace this connection in Settings → Connections so its saved keys are deleted too.')
        for name, row in additions.items():
            fields, given = credential_fields(row), (references or {}).get(name, {})
            if {field for field, text_value in row[fields].items() if text_value} != set(given):
                raise Failure('mcp_credentials', 'Credential values are kept in the desktop keyring. '
                              'Add this connection in Settings → Connections; nothing was saved.')
            if given:
                row[fields] = {field: text_value for field, text_value in row[fields].items() if not text_value}
                row['secrets'] = dict(given)
        # Replacing any connection resets all grants. A copied example cannot
        # inherit permission to run a different executable or remote service.
        value['servers'].update(additions)
        if 'files' in value:
            value['files']['servers']=[name for name in value['files']['servers'] if name not in additions]
        self.connections.save(value, revision)
        self.inspections.clear()
        self.catalogues.clear()

    async def import_configuration(self, text, revision, *, before_save=lambda: None):
        """The import entry: credential values go to the keyring, and only references are saved.

        A connection without credential values never touches the keyring. Otherwise
        the order is: the keyring is shown to work (by saving the new values, or by
        reading one old value when there are none); then the caller's last check and
        the settings write, with no wait between them; and only once the settings are
        written, the deletion of a replaced connection's old values.

        A connection in the settings therefore never loses its keys to an interrupted
        or refused import. After a failure, exactly those values of this import that
        the saved settings provably do not reference are deleted, best effort: an
        error is not taken as proof that the settings were not written.
        """
        async with self.lock, self.oauth.refresh_lock:
            additions, value = self._additions(text)
            wanted = {name: {field: text_value for field, text_value in row[credential_fields(row)].items() if text_value}
                      for name, row in additions.items()}
            wanted = {name: values for name, values in wanted.items() if values}
            old = [identity for name in additions
                   for identity in credential_refs(value['servers'].get(name, {}))]
            if not wanted and not old:
                return self.import_config(text, revision)
            if value['revision'] != revision:  # before the keyring is touched
                raise Failure('mcp_conflict', 'Connections changed. Refresh before saving again.')
            self._keyring()
            stored = {}
            try:
                for name, values in wanted.items():
                    stored[name] = await self.secrets.store(name, values)
                if not stored:
                    await self.secrets.usable(old[0])
                before_save()
                # Compare-and-set on the revision: a change saved during the waits above
                # refuses this one here, before any old value is touched.
                self._save_import(text, revision, stored, True)
            except BaseException:
                staged = [identity for saved in stored.values() for identity in saved.values()]
                await self._forget_unreferenced(staged + old, quiet=True)
                raise
            await self._forget_unreferenced(old)
            return self.connections.status()

    async def change(self, args, *, before_save=lambda: None):
        """One connection change. Removing a connection also deletes its keyring values.

        The keyring is shown to work first, so a locked keyring refuses the removal
        before anything changes. The settings are then written right after the caller's
        last check, and the values are deleted once no connection references them. No
        other action touches the keyring or waits, so turning a connection off stays
        immediate.
        """
        if args['action'] == 'credentials':
            return await self.configure_inputs(args, before_save)
        if args['action'] != 'remove':
            return self.update(args)
        async with self.lock, self.oauth.refresh_lock:
            value = self.connections.read()
            old = list(credential_refs(value['servers'].get(args['server'], {})))
            if not old:
                return self.update(args)
            if value['revision'] != args['expected_revision']:  # before the keyring is touched
                raise Failure('mcp_conflict', 'Connections changed. Refresh before saving again.')
            self._keyring()
            await self.secrets.usable(old[0])
            before_save()
            try:
                self._apply(args, keys_handled=True)
            except BaseException:
                # An error is not proof the removal was not written: delete the values
                # only if the saved settings provably no longer reference them.
                await self._forget_unreferenced(old, quiet=True)
                raise
            await self._forget_unreferenced(old)
            return self.connections.status()

    async def configure_inputs(self, args, before_save=lambda: None):
        """Fill only declared fields, through the existing staged credential owner.

        Neither status, Shelf receipts nor backups carry their values. Changing
        accounts resets tool grants; no discovery or external action runs here.
        """
        name, revision = args['server'], args['expected_revision']
        current = self.connections.read()
        if current['revision'] != revision:
            raise Failure('mcp_conflict', 'Connections changed. Refresh before saving account details.')
        original = current['servers'].get(name)
        if not original or not original.get('installation') or not original.get('inputs'):
            raise Failure('mcp_credentials', 'This package has no account fields to fill in.')
        fields = {field['name'] for field in original['inputs']}
        given = args['values']
        if (not isinstance(given, list) or len(given) != len(fields)
                or any(not isinstance(item, dict) or set(item) != {'name', 'value'} for item in given)
                or {item['name'] for item in given} != fields):
            raise Failure('mcp_credentials', 'Fill in each declared account field once; nothing was saved.')
        values = {item['name']: credential_value(item['value']) for item in given}
        row = dict(original, env={**original['env'], **values}, enabled=False, allowed_tools=[], repeatable_tools=[])
        kept = {k: v for k, v in row.get('secrets', {}).items() if k not in fields}
        row.pop('secrets', None)
        if kept:
            row['secrets'] = kept

        def publish(protected, observed_revision):
            before_save()
            if observed_revision != revision:
                raise Failure('mcp_conflict', 'Connections changed. Refresh before saving account details.')
            saved = self.connections.read()
            saved['servers'][name] = protected
            self.connections.save(saved, revision)
            self.inspections.pop(name, None)
            self.catalogues.pop(name, None)

        await self.register_package(name, row, original, publish)
        return self.connections.status()

    async def _forget_unreferenced(self, identities, *, quiet=False):
        """Delete, of these keyring values, the ones the saved settings provably do not reference.

        Unreadable settings prove nothing, so nothing is deleted then. ``quiet`` is
        for cleaning up after another failure, which is the one reported. Otherwise
        the settings were already written: a deletion that fails is reported as such,
        the change stands, and the unused values may remain in the keyring.
        """
        failure = Failure('credential_cleanup', 'The change was saved, but keys that are no longer used could not be '
                          'deleted from the desktop keyring. Unlock it and delete the unused “Kejtu — connection” items there.')
        try:
            used = {identity for row in self.connections.read()['servers'].values()
                    for identity in credential_refs(row)}
        except Failure:
            if quiet or not identities:
                return
            raise failure from None
        try:
            await self.secrets.forget([identity for identity in identities if identity not in used], quiet=quiet)
        except Failure:
            raise failure from None

    def _keyring(self):
        if self.secrets is None:
            raise Failure('credential_store', 'The desktop keyring is unavailable in this session; nothing was changed.')

    def update(self, args, *, keys_handled=False):
        self._apply(args, keys_handled=keys_handled)
        return self.connections.status()

    def _apply(self, args, *, keys_handled=False):
        """The settings write of one change, and nothing after it: once this returns, it is published."""
        value = self.connections.read()
        row = value['servers'].get(args['server'])
        if row is None:
            raise Failure('mcp_missing', 'Connection not found.')
        action = args['action']
        if action == 'remove':
            if credential_refs(row) and not keys_handled:
                # ``change`` deletes this connection's keyring values once it is removed.
                raise Failure('mcp_credentials', 'Remove this connection in Settings → Connections so its saved keys are deleted too.')
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
            # A cached check stays valid while the connection is off, so a program that
            # went missing since then is refused here, before anything is saved.
            if args['enabled'] and not available(row):
                raise Failure('mcp_unavailable', 'This connection’s program is missing. Reinstall it before turning it on; nothing was saved.')
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
