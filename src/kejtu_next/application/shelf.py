"""Shelf installations: one coordinator over the existing owners.

It executes nothing itself. A package is a source (a GitHub repository at an exact
commit, or a local folder) and its components:

- skills, fetched, verified and stored by SkillControls exactly as Settings
  stores an imported skill, and removed through SkillStore;
- remote connections (Streamable HTTP over HTTPS, no credentials yet), registered
  switched off through ``McpHost.register``, the path the included tools use, and
  removed through its ordinary remove action;
- local programs: one pinned archive, published as a package tree by the tree
  owner's disposable process, and a connection to an executable inside that tree.
  Nothing in a tree is run when it is installed.

Every declared component is recorded as a prepared step before any effect, so the
record always knows the whole package. A skill effect records its outcome and
membership inside the library's own transaction. A connection lives in the
settings file, outside the archive, so its entry carries this installation's
marker: inspection reads that marker and settles the step exactly. A settings
effect that ended in an error is failed only when the marker proves it did not
happen; otherwise it stays uncertain. Nothing is guessed, no operation repeats an
effect, and an operation is always closed, so a later repair needs no restart.

A package tree's location is its pinned content hash, so inspection settles an
unknown tree step by hashing what is there: the pinned tree, or not. Identical
pinned trees are shared and deleted only with their last installation. A
program's connection is registered first and stays unavailable until its tree is
there, so no wait ever comes between settling background work and a connection
change.

A package folder can be described before anything is installed: the disposable
reader reads it as data, in the bundle formats it knows, and each part it found
is judged by the same owner checks an install runs. Describing changes nothing.
"""
import asyncio
from contextlib import contextmanager
import re
import time
import uuid

from ..core.included_packages import included_origin
from ..core.mcp import NAME as CONNECTION_NAME
from ..core.records import Failure
from ..core.skills import content_fingerprint, name as skill_name, path as skill_path

_REPOSITORY = re.compile(r'https://github\.com/[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z')
_COMMIT = re.compile(r'[0-9a-f]{40}\Z')
_CODE = re.compile(r'[a-z_]{1,64}\Z')
MAX_COMPONENTS = 16
MAX_CONNECTIONS = 16


def _code(error):
    return error.code if _CODE.fullmatch(getattr(error, 'code', '') or '') else 'effect_failed'


class ShelfControls:
    def __init__(self, store, skills, connections=None):
        self.store, self.skills, self.mcp = store, skills, connections
        self.lock = asyncio.Lock()

    def status(self):
        return self.store.shelf.status()

    def lookup(self, mutation_key):
        """The recorded receipt for a key; callers compare its intent digest."""
        return self.store.shelf.receipt(mutation_key)

    def changes_connections(self, operation, args):
        """Whether a request may add or remove a connection, so background users settle first.

        A removal counts a connection it holds, and one an unsettled step may turn
        out to have registered: inspection can recover it just before removing it.
        This reads the record only, so a skill-only package never needs the settings.
        """
        if operation == 'shelf.install':
            return any(c['kind'] in ('connection', 'program') for c in args['components'])
        if operation in ('shelf.remove', 'shelf.update'):
            shelf, installation = self.store.shelf, args['installation_id']
            return (any(c['kind'] in ('connection', 'program') for c in args.get('components', []))
                    or any(m['kind'] == 'connection' for m in shelf.members(installation))
                    or any(s['component_kind'] == 'connection' for s in shelf.unresolved(installation)))
        return False

    def catalogue(self):
        from ..core.shelf_catalogue import public
        try:
            return public()
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise Failure('shelf_catalogue', 'The bundled Shelf catalogue could not be read.') from error

    @staticmethod
    def _catalogue_entry(args):
        from ..core.shelf_catalogue import selected
        source = args['source']
        try:
            entry = selected(source['id'], source['digest'])
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise Failure('shelf_catalogue', str(error)[:500]) from None
        package = entry['package']
        if (args['version'] != package['version'] or args['components'] != package['components']
                or args.get('identity', package['name']) != package['name']):
            raise Failure('shelf_catalogue', 'Reopen the Shelf entry before installing; its contents must match the preview.')
        return entry

    async def _catalogue_packages(self, args, settle):
        if args['source']['kind'] != 'catalogue':
            return {}
        entry = self._catalogue_entry(args)
        deadline = time.monotonic() + 160
        def check():
            if not self.store.web.settings()['enabled']:
                raise Failure('shelf_web', 'Enable Web access to download a package from its publisher.')
            if time.monotonic() > deadline:
                raise Failure('shelf_time', 'The package download exceeded its time allowance.')
        check()
        if self.skills.importer is None:
            raise Failure('skill_import_unavailable', 'Package importing is unavailable in this session.')
        try:
            result = await self.skills.importer.catalogue(entry['id'], entry['digest'], check)
        except (ValueError, OSError, UnicodeError, KeyError, TypeError, RecursionError) as error:
            raise Failure('shelf_download', str(error)[:500]) from None
        check()
        if settle is not None:
            await settle()
        return result['packages']

    # Describe

    async def describe(self, args):
        """What a package folder holds and what of it can be installed here. Changes nothing.

        The disposable reader reads the folder as data, in any of the formats it
        knows. Each part it found is then judged by the owner an install asks
        (``_checked``): one that owner refuses is listed with the reason instead. A
        part the package works through that cannot be used makes it not installable.
        """
        directory = args['directory']
        if not directory.startswith('/') or '\0' in directory:
            raise Failure('shelf_source', 'Choose a complete folder path.')
        if self.skills.importer is None:
            raise Failure('skill_import_unavailable', 'Reading packages is unavailable in this session.')
        deadline = time.monotonic() + 10

        def check():
            if time.monotonic() > deadline:
                raise ValueError('Reading the package took longer than its time allowance.')
        try:
            found = await self.skills.importer.describe(directory, check)
        except (ValueError, OSError, UnicodeError, KeyError, TypeError, RecursionError) as error:
            raise Failure('shelf_package', str(error)[:500]) from None
        components, unsupported = [], list(found['unsupported'])
        for component in found['components']:
            try:
                self._checked(component)
                components.append(component)
            except Failure as error:
                reason = error.detail.replace('; nothing was installed.', '.')  # the owner's words, said of a description
                unsupported.append(dict(kind='skill' if component['kind'] == 'skill' else 'tool', name=component['name'],
                                        essential=True, reason=reason.encode()[:512].decode('utf-8', 'ignore')))
        return dict(found, components=components, unsupported=unsupported[:64],
                    installable=bool(components) and not any(u['essential'] for u in unsupported))

    # Backups and restores

    async def data(self):
        """What each installed package keeps, whether a backup takes it, and what a backup or a restore left to say.

        Sizes are read from the folders; the choices and the two notes belong to the
        package backup owner. Changes nothing. A restore's list is narrowed to what is
        still true: a package whose program files are back is no longer listed.
        """
        if self.mcp is None:
            raise Failure('mcp_unavailable', 'Connections are unavailable in this session.')
        backups = self.mcp.backups
        choices = backups.choices()
        items, total = [], 0
        until = time.monotonic() + 20  # for all of them together; a folder not counted in time says so
        for item in self.store.shelf.status()['items']:
            if not any(c['kind'] == 'plugin_data' for c in item['components']):
                continue
            size, files, complete = await asyncio.to_thread(
                backups.data.measure, item['id'], max(0, min(3, until - time.monotonic())))
            include = item['id'] not in choices['leave_out']
            items.append(dict(installation_id=item['id'], bytes=size, files=files, complete=complete, include=include))
            total += size if include else 0
        return dict(revision=choices['revision'], limit_bytes=choices['limit_bytes'], total_bytes=total, items=items,
                    last_backup=self._last_backup(backups.last()), restored=self._restored(backups.restored()))

    async def data_choice(self, args):
        """Take one installed package's data into backups, or leave it out."""
        if self.mcp is None:
            raise Failure('mcp_unavailable', 'Connections are unavailable in this session; nothing was changed.')
        record = self.store.shelf.installation(args['installation_id'])
        if record is None or record['state'] == 'removed':
            raise Failure('shelf_missing', 'This installation is not present.')
        self.mcp.backups.choose(args['installation_id'], args['include'], args['expected_revision'])
        return await self.data()

    async def restored_seen(self):
        """The person has read what the restore left to do; the note goes."""
        if self.mcp is None:
            raise Failure('mcp_unavailable', 'Connections are unavailable in this session; nothing was changed.')
        self.mcp.backups.seen()
        return await self.data()

    @staticmethod
    def _last_backup(note):
        try:
            if note is None:
                return None
            return dict(created_at=str(note['created_at'])[:64], data_bytes=int(note['data_bytes']), limit_bytes=int(note['limit_bytes']),
                        over_limit=bool(note['over_limit']), largest_identity=str(note['largest_identity'])[:128],
                        largest_bytes=int(note['largest_bytes']))
        except (KeyError, TypeError, ValueError):
            return None

    def _restored(self, note):
        """A restore's note in the shape the screen takes, without what is no longer true."""
        try:
            if note is None:
                return None
            missing = lambda installation: any(
                member['kind'] == 'package_tree' and not self.mcp.trees.path(member['key']).is_dir()
                for member in self.store.shelf.members(installation))
            short = lambda text, limit: str(text).encode('utf-8', 'replace')[:limit].decode('utf-8', 'ignore')
            return dict(
                restored_at=short(note['restored_at'], 64), backup_created_at=short(note['backup_created_at'], 64),
                reinstall=[dict(installation_id=p['installation_id'], identity=short(p['identity'], 512))
                           for p in note['reinstall'][:100] if missing(p['installation_id'])],
                reconnect=[dict(connection=short(r['connection'], 64), fields=[short(f, 80) for f in r['fields'][:24]],
                                reason='keyring' if r['reason'] == 'keyring' else 'left_out') for r in note['reconnect'][:64]],
                data_not_in_backup=[dict(installation_id=p['installation_id'], identity=short(p['identity'], 512), bytes=int(p['bytes']))
                                    for p in note['data_not_in_backup'][:100]],
                connections_in_backup=bool(note['connections_in_backup']), moved_from=short(note.get('moved_from', ''), 1024))
        except (KeyError, TypeError, ValueError, AttributeError):
            return None

    # Install

    async def install(self, args, intent, settle=None):
        async with self.lock:
            shelf, key = self.store.shelf, args['mutation_key']
            existing = self._existing(key, intent)
            if existing is not None:
                return existing
            # Everything that can be checked is checked before anything is recorded:
            # the source, each component, collisions and the connection limit.
            origin, components = self._package(args)
            prepared = await self._catalogue_packages(args, settle)
            if args['source']['kind'] == 'catalogue':
                origin, components = self._package(args)
                for component in components:
                    if component['kind'] == 'skill':
                        component['prepared'] = prepared[component['name']]
            installation = uuid.uuid4().hex
            shelf.begin('install', key, intent, installation_id=installation,
                        identity=args['identity'], version=args['version'], origin=origin)
            with self._closing(key):
                # The whole package is declared before any effect, so a stop at any
                # point leaves a record of every part, done or not.
                units = self._units(components, installation)
                steps = [shelf.prepare(key, unit['kind'], unit['name'], unit['action']) for unit in units]
                # Connections first: their owner stages any keys, settles background
                # work again, and publishes settings plus record without an await. A program's
                # connection stays unavailable until its tree arrives. The package's own
                # folders come next: creating them is immediate, so a download whose result
                # is lost can still be confirmed by repair as the last missing part. Trees
                # and skills follow.
                rank = dict(connection=0, plugin_data=1, cache=1, package_tree=2, skill=3)
                order = sorted(range(len(units)), key=lambda i: rank[units[i]['kind']])
                for position, index in enumerate(order):
                    unit, step = units[index], steps[index]
                    try:
                        if unit['kind'] == 'connection':
                            await self._register_connection(key, step, unit, installation, settle=settle)
                        elif unit['kind'] == 'package_tree':
                            await self._materialize(key, step, unit)
                        elif unit['kind'] in ('plugin_data', 'cache'):
                            self._create_folder(key, step, unit)
                        else:
                            await self._install_skill(key, step, unit)
                    except Failure as error:
                        self._cleanup_warning(key, unit['name'], error)
                        # Failed only when provably nothing took effect; otherwise the
                        # step stays for inspection.
                        if self._outcome(key, step) == 'prepared' and self._nothing_happened(unit, installation, error):
                            shelf.settle(key, step, 'failed', _code(error))
                        for later in order[position + 1:]:
                            shelf.settle(key, steps[later], 'skipped', 'not_attempted')
                        return shelf.finish(key, 'failed')
                return shelf.finish(key, 'completed')

    @staticmethod
    def _units(components, installation):
        """The recorded parts of a package: a program is its connection and its package tree.

        A package with a program also gets two folders of its own, keyed by this
        installation: its data, and a disposable cache.
        """
        units, trees = [], {}
        for component in components:
            if component['kind'] != 'program':
                units.append(dict(component, action='register'))
                continue
            units.append(dict(component, kind='connection', action='register'))
            if component['tree'] not in trees:  # programs of one archive share its tree
                trees[component['tree']] = dict(kind='package_tree', name=component['tree'], action='materialize',
                                                pin=component['pin'], commands=[])
                units.append(trees[component['tree']])
            # The tree must hold every program this package declares in it.
            trees[component['tree']]['commands'].append(component['command'])
        if trees:
            units += [dict(kind=kind, name=installation, action='materialize') for kind in ('plugin_data', 'cache')]
        return units

    def _nothing_happened(self, unit, installation, error):
        if unit['kind'] == 'skill':
            return True  # a skill step still prepared never committed
        if unit['kind'] == 'connection':
            # Ours only with our marker; and during an update the old entry is ours too,
            # so the new one is there only when the entry is exactly the new configuration.
            try:
                row = self.mcp.connections.read()['servers'].get(unit['name'])
            except Failure:
                return False
            return (row is None or row.get('installation') != installation
                    or self.mcp.fingerprint(row) != unit['detail']['configuration_sha256'])
        if unit['kind'] in ('plugin_data', 'cache'):
            try:
                return self.mcp.data.state(unit['kind'], unit['name']) == 'absent'
            except Failure:
                return False  # a link in its path proves nothing either way
        # A tree is published by a process that either refuses, saying why, or is
        # ended without saying: only a refusal proves this install published nothing.
        return error.code in self.mcp.trees.refusals

    async def _materialize(self, key, step, unit, *, restore=False):
        shelf, name = self.store.shelf, unit['name']
        detail = dict(tree_sha256=unit['pin']['tree_sha256'])
        owner = shelf.owner('package_tree', name)
        if owner is not None:
            # Another installation holds this tree. Its membership is not proof of what is
            # on disk now, so the pin is checked by hashing before this one shares it.
            state = await self.mcp.trees.verify(name, self._allowance(web=False), unit['commands'])
            if state == 'absent' and restore:
                await self.mcp.trees.materialize(unit['pin'], unit['commands'], self._allowance())
                state = 'ok'
            if state != 'ok':
                code, text = dict(absent=('shelf_tree_missing', 'is missing'),
                                  different=('shelf_tree_differs', 'no longer matches its pinned contents'),
                                  incomplete=('shelf_program_missing', 'does not contain a declared program'))[state]
                raise Failure(code, f'The package tree another installation uses {text}; this package was not installed.')
            installation = shelf.receipt(key)['installation_id']
            role = 'owner' if owner == installation else 'shared'
            shelf.settle(key, step, 'applied', 'already_present', attach=('package_tree', name, detail, role))
            return
        state = await self.mcp.trees.materialize(unit['pin'], unit['commands'], self._allowance())
        shelf.settle(key, step, 'applied', None if state == 'published' else 'already_present',
                     attach=('package_tree', name, detail))

    def _create_folder(self, key, step, unit):
        kind, installation = unit['kind'], unit['name']
        state = self.mcp.data.create(kind, installation)
        self.store.shelf.settle(key, step, 'applied', None if state == 'created' else 'already_present',
                                attach=(kind, installation, dict(folder=self.mcp.data.relative(kind, installation))))

    def _allowance(self, *, web=True):
        """The check a package-tree step runs under: its time, and Web access staying on for a download."""
        deadline = time.monotonic() + self.mcp.trees.seconds

        def check():
            if time.monotonic() > deadline:
                raise Failure('shelf_time', 'The package step exceeded its time allowance and was stopped.')
            if web and not self.store.web.settings()['enabled']:
                raise Failure('shelf_web', 'Web access was turned off; the download was stopped.')
        return check

    async def _register_connection(self, key, step, component, installation, replaces=None, settle=None):
        row = dict(component['row'], installation=installation)
        if 'pin' in component:
            # Keep the acquisition pin with the owning connection, so ordinary
            # settings backup/restore can reconstruct programs without another store.
            row['package_artifact'] = component['pin']

        def publish(protected, revision):
            source_digest = component.get('source_configuration_sha256', component['detail']['configuration_sha256'])
            component['detail'] = self._connection_detail(protected)
            if protected.get('secrets'):
                component['detail']['source_configuration_sha256'] = source_digest
            self.mcp.register(component['name'], protected, revision, replace=component['present'], keys_handled=True)
            # No await between settings publication and its record. Unknown writes
            # remain inspectable by marker and the actual saved fingerprint.
            self.store.shelf.settle(key, step, 'applied', attach=('connection', component['name'], component['detail']),
                                   **({'detach': replaces} if replaces else {}))

        await self.mcp.register_package(component['name'], row, component['expected'], publish, settle)

    async def _install_skill(self, key, step, component, replaces=None):
        shelf, name = self.store.shelf, component['name']

        def record(package):  # inside the library's install transaction
            shelf.settle_within(key, step, 'applied', attach=('skill', name, self._pin(package['manifest'], package['revision'])),
                                **({'detach': replaces} if replaces else {}))
        # A new installation always lands switched off, even over an identical skill
        # someone enabled before: enabling it is a decision of its own.
        await self.skills.install_package(
            component['source_kind'], component['location'], self.store.skills.epoch(), update=component['present'],
            expected_commit=component['commit'], expected_name=name, disabled=True, record=record,
            **({'prepared': component['prepared']} if 'prepared' in component else {}))

    # Update

    async def update(self, args, intent, settle=None):
        """Replace an installed package by another exact version of it. Never automatic.

        The new version is prepared beside the old one: nothing the package uses is
        touched until every new tree is in place, so a download that fails leaves it
        working as it was. Then its connections switch, with no wait after background
        work was settled once more (``settle``). A switched connection lands off with
        no tools granted, like a new one; the old tree is released afterwards, and one
        another installation still uses stays. The package's data folder is kept.
        """
        async with self.lock:
            shelf, key, installation = self.store.shelf, args['mutation_key'], args['installation_id']
            existing = self._existing(key, intent)
            if existing is not None:
                return existing
            self._supported(installation)
            if included_origin(shelf.installation(installation)['origin']):
                raise Failure('package_included', 'This package is included with Kejtu. Disable its skills instead; it updates with the app.')
            origin, components = self._package(args, installation)
            prepared = await self._catalogue_packages(args, settle)
            if args['source']['kind'] == 'catalogue':
                origin, components = self._package(args, installation)
                for component in components:
                    if component['kind'] == 'skill':
                        component['prepared'] = prepared[component['name']]
            shelf.begin('update', key, intent, installation_id=installation, version=args['version'], origin=origin)
            with self._closing(key):
                held = {(m['kind'], m['key']): m for m in shelf.members(installation)}
                # Declared before any effect: every part the new version puts in place.
                # Verify held trees too: membership can survive a backup without its files.
                # A connection whose configuration is unchanged is left as it is, grants included.
                def unchanged(unit):
                    old = held.get((unit['kind'], unit['name']))
                    if old is None:
                        return False
                    if unit['kind'] == 'package_tree':
                        return False  # membership is not proof that the pinned files still exist
                    if unit['kind'] in ('plugin_data', 'cache'):
                        return self.mcp.data.state(unit['kind'], installation) == 'present'
                    if unit['kind'] != 'connection':
                        return True
                    current = unit['expected']
                    pinned = old['detail'].get('source_configuration_sha256', old['detail']['configuration_sha256'])
                    return (current is not None and self.mcp.fingerprint(current) == old['detail']['configuration_sha256']
                            and pinned == unit.get('source_configuration_sha256', unit['detail']['configuration_sha256']))
                units = [u for u in self._units(components, installation) if u['kind'] == 'skill' or not unchanged(u)]
                steps = [shelf.prepare(key, unit['kind'], unit['name'], unit['action']) for unit in units]
                rank = dict(package_tree=0, plugin_data=1, cache=1, connection=2, skill=3)
                order = sorted(range(len(units)), key=lambda i: rank[units[i]['kind']])
                for position, index in enumerate(order):
                    unit, step = units[index], steps[index]
                    old = held.get((unit['kind'], unit['name']))
                    try:
                        if unit['kind'] == 'package_tree':
                            await self._materialize(key, step, unit, restore=True)
                        elif unit['kind'] in ('plugin_data', 'cache'):
                            self._create_folder(key, step, unit)
                        elif unit['kind'] == 'connection':
                            await self._register_connection(key, step, unit, installation, replaces=old['id'] if old else None, settle=settle)
                        else:
                            await self._install_skill(key, step, unit, replaces=old['id'] if old else None)
                    except Failure as error:
                        self._cleanup_warning(key, unit['name'], error)
                        if self._outcome(key, step) == 'prepared' and self._nothing_happened(unit, installation, error):
                            shelf.settle(key, step, 'failed', _code(error))
                        for later in order[position + 1:]:
                            shelf.settle(key, steps[later], 'skipped', 'not_attempted')
                        return shelf.finish(key, 'failed')
                # What the new version no longer has is released last, in removal's order
                # and by removal's rules: connections and skills, then trees, then folders.
                kept = {(u['kind'], u['name']) for u in self._units(components, installation)}
                gone = [m for m in shelf.members(installation) if (m['kind'], m['key']) not in kept]
                for member in gone:
                    if member['kind'] in ('connection', 'skill'):
                        removed = (await self._remove_connection(key, member, installation, settle=settle)
                                   if member['kind'] == 'connection' else self._remove_skill(key, member, installation))
                        if not removed:
                            return shelf.finish(key, 'failed')
                for member in gone:
                    if member['kind'] == 'package_tree' and not await self._remove_tree(key, member):
                        return shelf.finish(key, 'failed')
                for member in gone:
                    if member['kind'] in ('plugin_data', 'cache') and not await self._remove_folder(
                            key, member, delete=member['kind'] == 'cache'):
                        return shelf.finish(key, 'failed')
                return shelf.finish(key, 'completed')

    # Repair and remove

    async def repair(self, args, intent, settle=None):
        async with self.lock:
            shelf, key, installation = self.store.shelf, args['mutation_key'], args['installation_id']
            existing = self._existing(key, intent)
            if existing is not None:
                return existing
            self._supported(installation)
            shelf.begin('repair', key, intent, installation_id=installation)
            with self._closing(key):
                self._inspect(installation)
                await self._inspect_trees(installation)
                if not shelf.unresolved(installation):
                    if args.get('reinstall', False):
                        if not await self._reinstall(key, installation, settle):
                            return shelf.finish(key, 'failed')
                    else:
                        members = shelf.members(installation)
                        trees = {m['key'] for m in members if m['kind'] == 'package_tree'}
                        trees.update(m['detail']['tree'] for m in members if m['kind'] == 'connection' and 'tree' in m['detail'])
                        for tree in sorted(trees):
                            state = await self.mcp.trees.verify(tree, self._allowance(web=False), self._declared(installation, tree))
                            if state != 'ok':
                                step = shelf.prepare(key, 'package_tree', tree, 'materialize')
                                shelf.settle(key, step, 'failed', 'reinstall_required')
                                return shelf.finish(key, 'failed')
                return shelf.finish(key, 'failed' if shelf.unresolved(installation) else 'completed')

    async def _reinstall(self, key, installation, settle):
        """Explicit reconstruction of recorded programs; keep the installation and its data.

        Recovery pins live in the existing connection settings. Older installations
        without them need the original package supplied through the ordinary update.
        No configuration is inferred from a name, and a changed user entry is preserved.
        """
        shelf, rows = self.store.shelf, self.mcp.connections.read()['servers']
        programs = []
        for member in shelf.members(installation):
            if member['kind'] != 'connection' or 'tree' not in member['detail']:
                continue
            row = rows.get(member['key'])
            if row is None or row.get('installation') != installation:
                raise Failure('mcp_conflict', 'A package connection was removed or replaced. Keep that change, or supply the original package as an update.')
            if 'package_artifact' not in row:
                raise Failure('shelf_source', 'This older installation has no saved download pin. Choose its original package folder and apply an update to reinstall its programs.')
            pin = self.mcp.trees.pinned(row['package_artifact'])
            if self.mcp.trees.key(pin) != member['detail']['tree']:
                raise Failure('shelf_source', 'The saved recovery pin does not match the installed program. Supply the original package as an update.')
            self.mcp.trees.ready(pin)
            tree, command = member['detail']['tree'], member['detail']['program']
            # Start off with no tool grants. Retain the saved network choice and
            # account declaration; add no shared folders. Keyring references stay
            # opaque: reconnect is independent of reinstall.
            restored = self.mcp.program(member['key'], self.mcp.trees.path(tree), command, row['args'], row['description'], row['env'], row.get('cwd'),
                                        inputs=row.get('inputs'), protocol_mode=row.get('protocol_mode'),
                                        network=row.get('network', False))
            if row.get('secrets'):
                restored['secrets'] = dict(row['secrets'])
            programs.append(dict(kind='program', name=member['key'], row=restored, pin=pin, tree=tree, command=command,
                                 present=True, expected=row, detail=self._connection_detail(restored), replaces=member['id'],
                                 **({'source_configuration_sha256': member['detail']['source_configuration_sha256']}
                                    if 'source_configuration_sha256' in member['detail'] else {})))
        if not programs:
            raise Failure('shelf_source', 'This installation has no recorded programs to reinstall.')
        units = self._units(programs, installation)
        steps = [shelf.prepare(key, u['kind'], u['name'], u['action']) for u in units]
        rank = dict(package_tree=0, plugin_data=1, cache=1, connection=2)
        order = sorted(range(len(units)), key=lambda i: rank[units[i]['kind']])
        for position, index in enumerate(order):
            unit, step = units[index], steps[index]
            try:
                if unit['kind'] == 'package_tree':
                    await self._materialize(key, step, unit, restore=True)
                elif unit['kind'] == 'connection':
                    await self._register_connection(key, step, unit, installation, replaces=unit['replaces'], settle=settle)
                else:
                    self._create_folder(key, step, unit)
            except Failure as error:
                self._cleanup_warning(key, unit['name'], error)
                if self._outcome(key, step) == 'prepared' and self._nothing_happened(unit, installation, error):
                    shelf.settle(key, step, 'failed', _code(error))
                for later in order[position + 1:]:
                    shelf.settle(key, steps[later], 'skipped', 'not_attempted')
                return False
        return True

    async def remove(self, args, intent, settle=None):
        async with self.lock:
            shelf, key, installation = self.store.shelf, args['mutation_key'], args['installation_id']
            existing = self._existing(key, intent)
            if existing is not None:
                return existing
            self._supported(installation)
            if included_origin(shelf.installation(installation)['origin']):
                raise Failure('package_included', 'This package is included with Kejtu. Disable its skills instead; it updates with the app.')
            shelf.begin('remove', key, intent, installation_id=installation)
            with self._closing(key):
                # Settle earlier unknown results before deciding what to remove. Connections
                # and skills first, with no wait since the caller settled background work;
                # package trees after them. A tree is deleted only when no connection in
                # the settings uses it any more, whoever added that connection.
                self._inspect(installation)
                for member in shelf.members(installation):
                    if member['kind'] in ('connection', 'skill'):
                        removed = (await self._remove_connection(key, member, installation, settle=settle)
                                   if member['kind'] == 'connection' else self._remove_skill(key, member, installation))
                        if not removed:
                            return shelf.finish(key, 'failed')
                await self._inspect_trees(installation)
                for member in shelf.members(installation):
                    if member['kind'] == 'package_tree' and not await self._remove_tree(key, member):
                        return shelf.finish(key, 'failed')
                # Last, the package's own folders. No program of it can run any more. The
                # cache always goes; the data stays unless its deletion was chosen.
                for member in shelf.members(installation):
                    if member['kind'] in ('plugin_data', 'cache') and not await self._remove_folder(
                            key, member, delete=member['kind'] == 'cache' or args.get('delete_data', False)):
                        return shelf.finish(key, 'failed')
                return shelf.finish(key, 'failed' if shelf.unresolved(installation) else 'completed')

    async def _remove_tree(self, key, member):
        shelf, name = self.store.shelf, member['key']
        if member['others']:
            self._release(key, member, absent=False)  # another installation still uses this tree
            return True
        try:
            users = self.mcp.program_users(self.mcp.trees.path(name))
        except Failure:
            # Nothing was attempted: the settings must be readable to tell who still uses it.
            step = shelf.prepare(key, 'package_tree', name, 'delete')
            shelf.settle(key, step, 'failed', 'settings_unreadable')
            return False
        if users:
            # A connection kept in the settings (replaced there, or added by hand) still runs
            # from this tree or reads it. It keeps its program; this installation only
            # releases its claim.
            step = shelf.prepare(key, 'package_tree', name, 'keep')
            shelf.settle(key, step, 'applied', 'used_by_connection', detach=member['id'])
            return True
        step = shelf.prepare(key, 'package_tree', name, 'delete')
        try:
            removed = await self.mcp.trees.remove(name)
        except Failure as error:  # refused before anything was moved
            shelf.settle(key, step, 'failed', _code(error))
            return False
        except OSError:  # the one rename did not happen
            shelf.settle(key, step, 'failed', 'tree_delete_failed')
            return False
        if removed:
            shelf.settle(key, step, 'applied', detach=member['id'])
        else:
            shelf.settle(key, step, 'skipped', 'already_absent', detach=member['id'])
        return True

    async def _remove_folder(self, key, member, *, delete):
        shelf, kind, installation = self.store.shelf, member['kind'], member['key']
        try:
            # A folder holding nothing has nothing to keep: it goes either way.
            delete = delete or self.mcp.data.holds_nothing(kind, installation)
        except (Failure, OSError):
            pass  # unreadable or behind a link: it is kept, or the deletion below refuses and says why
        if not delete:
            # The folder stays where it is; this installation only ends its claim.
            step = shelf.prepare(key, kind, installation, 'keep')
            shelf.settle(key, step, 'applied', 'kept_by_choice', detach=member['id'])
            return True
        step = shelf.prepare(key, kind, installation, 'delete')
        try:
            removed = await self.mcp.data.remove(kind, installation)
        except Failure as error:  # refused before anything was moved
            shelf.settle(key, step, 'failed', _code(error))
            return False
        except OSError:  # the one rename did not happen
            shelf.settle(key, step, 'failed', 'folder_delete_failed')
            return False
        if removed:
            shelf.settle(key, step, 'applied', detach=member['id'])
        else:
            shelf.settle(key, step, 'skipped', 'already_absent', detach=member['id'])
        return True

    def _remove_skill(self, key, member, installation):
        shelf, name, pinned = self.store.shelf, member['key'], member['detail'].get('revision')
        head = self.store.skills.head(name)
        if not member['others'] and head is not None and head == pinned:
            step = shelf.prepare(key, 'skill', name, 'unregister')

            def record():  # inside the library's removal transaction
                shelf.settle_within(key, step, 'applied', detach=member['id'])
            try:
                self.store.skills.remove(name, key, self.store.skills.epoch(), revision=pinned, record=record)
            except Failure as error:
                shelf.settle(key, step, 'failed', _code(error))
                return False
            return True
        # Nothing of ours to unregister: another installation still uses it, an
        # explicit Settings update replaced it (that revision is the user's now), or
        # it is already gone. The library keeps what it holds; this installation only
        # releases its claim.
        self._release(key, member, absent=head is None)
        return True

    async def _remove_connection(self, key, member, installation, settle=None):
        shelf, name = self.store.shelf, member['key']
        state = None if member['others'] else self._entry(name, installation)
        if state == 'unreadable':
            # Nothing was attempted: the settings must be readable to tell whose entry it is.
            step = shelf.prepare(key, 'connection', name, 'unregister')
            shelf.settle(key, step, 'failed', 'settings_unreadable')
            return False
        if state != 'ours':
            # Shared, already gone, or replaced by hand (an import drops the marker):
            # the settings keep what they hold; this installation releases its claim.
            self._release(key, member, absent=state == 'absent')
            return True
        step = shelf.prepare(key, 'connection', name, 'unregister')
        try:
            result = await self.mcp.unregister_package(name, installation, self.mcp.connections.read()['revision'], settle)
        except Failure as error:
            self._cleanup_warning(key, name, error)
            if error.code == 'credential_cleanup' and self._entry(name, installation) == 'absent':
                shelf.settle(key, step, 'applied', detach=member['id'])
                return False
            # The settings write comes before its read-back, so an error does not prove
            # the entry is still there. Failed only when our marked entry provably
            # remains; otherwise the step stays uncertain and inspection settles it.
            if self._entry(name, installation) == 'ours':
                shelf.settle(key, step, 'failed', _code(error))
            return False
        if result == 'removed':
            shelf.settle(key, step, 'applied', detach=member['id'])
        else:
            shelf.settle(key, step, 'skipped', 'already_absent' if result == 'absent' else 'replaced_outside_shelf',
                         detach=member['id'])
        return True

    def _cleanup_warning(self, key, name, error):
        """A saved change and failed key cleanup are separate recorded outcomes."""
        if error.code == 'credential_cleanup':
            step = self.store.shelf.prepare(key, 'secret', name, 'delete')
            self.store.shelf.settle(key, step, 'failed', 'credential_cleanup')

    def _release(self, key, member, *, absent):
        shelf = self.store.shelf
        if absent and not member['others']:
            step = shelf.prepare(key, member['kind'], member['key'], 'unregister')
            shelf.settle(key, step, 'skipped', 'already_absent', detach=member['id'])
        else:
            step = shelf.prepare(key, member['kind'], member['key'], 'keep')
            shelf.settle(key, step, 'applied', 'shared' if member['others'] else 'replaced_outside_shelf',
                         detach=member['id'])

    # Internal

    def _existing(self, key, intent):
        receipt = self.store.shelf.receipt(key)
        if receipt['status'] == 'absent':
            return None
        if receipt['intent_sha256'] != intent:
            raise Failure('mutation_conflict', 'That operation key belongs to another Shelf intent.')
        return receipt  # never repeat an operation's effects

    def _package(self, args, installation=None):
        """Check a package request before anything is recorded.

        With ``installation`` the request is an update of that installation: a name
        it already holds is its own to replace, and nothing else may be replaced.
        """
        source = args['source']
        entry = self._catalogue_entry(args) if source['kind'] == 'catalogue' else None
        if entry is not None:
            source = dict(kind='github', repository=entry['repository'], commit=entry['commit'])
        if source['kind'] == 'github':
            if not _REPOSITORY.fullmatch(source['repository']) or not _COMMIT.fullmatch(source['commit']):
                raise Failure('shelf_source', 'Use a public GitHub repository URL and a full 40-character commit.')
            origin = dict(kind='github', repository=source['repository'], commit=source['commit'])
        else:
            if not source['directory'].startswith('/') or '\0' in source['directory']:
                raise Failure('shelf_source', 'Choose a complete folder path.')
            origin = dict(kind='folder', directory=source['directory'])
        requested = args['components']
        if entry is not None:
            origin.update(path=entry['path'], catalogue_id=entry['id'], catalogue_digest=entry['digest'])
            requested = [dict(c, path='/'.join(p for p in (entry['path'], c['path']) if p))
                         if c['kind'] == 'skill' else c for c in requested]
        if not 1 <= len(requested) <= MAX_COMPONENTS:
            raise Failure('shelf_record', 'A package lists 1–16 components; nothing was installed.')
        skills = [c['name'] for c in requested if c['kind'] == 'skill']
        connections = [c['name'] for c in requested if c['kind'] != 'skill']  # a program is a connection too
        if len(set(skills)) != len(skills) or len(set(connections)) != len(connections):
            raise Failure('shelf_record', 'Each component is listed once; nothing was installed.')
        replace = args.get('replace_existing', False)
        build = dict(skill=lambda c: self._skill(c, source, replace, installation),
                     connection=lambda c: self._connection(c, replace, installation),
                     program=lambda c: self._program(c, replace, installation))
        components = [build[c['kind']](c) for c in requested]
        if source['kind'] == 'github' and entry is None and sum(c['kind'] == 'skill' for c in components) > 1:
            # Each skill would be downloaded on its own. Several skills from one
            # repository need the single package download that arrives with 1d-3.
            raise Failure('shelf_unsupported', 'This version installs one skill per GitHub package; nothing was installed.')
        added = [c for c in components if c['kind'] != 'skill' and not c['present']]
        if added and len(self.mcp.connections.read()['servers']) + len(added) > MAX_CONNECTIONS:
            raise Failure('mcp_configuration', 'At most 16 connections can be configured; nothing was installed.')
        downloads = [c for c in components if c['kind'] == 'program'
                     and self.store.shelf.owner('package_tree', c['tree']) is None]
        if downloads and not self.store.web.settings()['enabled']:
            raise Failure('shelf_web', 'Enable Web access before installing a package that downloads a program; '
                          'nothing was installed.')
        return origin, components

    def _checked(self, requested):
        """One requested part as its owner accepts it; nothing about what is installed already.

        An install and a description both ask here, so a part a description lists
        is one an install takes, short of a name that is already in use.
        """
        if requested['kind'] == 'skill':
            try:
                skill_name(requested['name'])
                if requested['path']:
                    skill_path(requested['path'])
            except ValueError as error:
                raise Failure('shelf_record', str(error)) from None
            return {}
        name = self._connection_name(requested)
        if requested['kind'] == 'connection':
            return dict(name=name, row=self.mcp.remote(name, requested['url'], requested['description']))
        trees = self.mcp.trees
        pin = trees.pinned(requested['artifact'])
        trees.ready(pin)
        tree = trees.key(pin)
        # Settings that come with the package, and where its program starts. Both are
        # passed to the credential owner; launch fills in the package's two places.
        settings = requested.get('env', [])
        if len({item['name'] for item in settings}) != len(settings):
            raise Failure('shelf_record', 'A program lists each of its settings once; nothing was installed.')
        row = self.mcp.program(name, trees.path(tree), requested['command'], requested['args'], requested['description'],
                               env={item['name']: item['value'] for item in settings}, cwd=requested.get('cwd'),
                               inputs=requested.get('inputs'), protocol_mode=requested.get('protocol_mode'),
                               network=requested.get('network', False))
        if any(row['env'].get(field['name']) != '' for field in row.get('inputs', [])):
            raise Failure('shelf_record', 'Account fields must start empty; enter their values during package setup.')
        return dict(name=name, row=row, pin=pin, tree=tree,
                    **({'source_configuration_sha256': self.mcp.source_fingerprint(row)} if any(row['env'].values()) else {}))

    def _skill(self, requested, source, replace, installation=None):
        self._checked(requested)
        name, path = requested['name'], requested['path']
        if source['kind'] == 'github':
            location = source['repository'] + '/tree/' + source['commit'] + ('/' + path if path else '')
            kind, commit = 'github', source['commit']
        else:
            location, kind, commit = source['directory'].rstrip('/') + ('/' + path if path else ''), 'folder', None
        # Collisions are settled before anything is recorded: a name another
        # installation owns, or a hand-installed skill without explicit consent.
        owner = self.store.shelf.owner('skill', name)
        if owner is not None and owner != installation:
            raise Failure('shelf_collision', 'Another installed package already provides this skill. '
                          'Remove it first, or keep it and cancel this install.')
        present = self.store.skills.head(name) is not None
        if present and not replace and owner is None:
            raise Failure('skill_exists', 'A skill with this name is already installed. '
                          'Choose to replace it, keep it, or cancel.')
        return dict(kind='skill', name=name, location=location, source_kind=kind, commit=commit, present=present)

    def _connection(self, requested, replace, installation=None):
        part = self._checked(requested)
        return dict(part, kind='connection', present=self._claim(part['name'], replace, installation),
                    detail=self._connection_detail(part['row']), expected=self.mcp.connections.read()['servers'].get(part['name']))

    def _program(self, requested, replace, installation=None):
        part = self._checked(requested)
        return dict(part, kind='program', present=self._claim(part['name'], replace, installation),
                    command=requested['command'], detail=self._connection_detail(part['row']),
                    expected=self.mcp.connections.read()['servers'].get(part['name']))

    def _connection_detail(self, row):
        """A connection's pinned identity. A program's names its tree and the program in it.

        The installation record keeps this from the moment the connection is
        registered until the installation releases it, whatever the settings hold
        later: it is how an inspection knows which programs a tree was installed for.
        """
        detail = dict(configuration_sha256=self.mcp.fingerprint(row))
        located = self.mcp.trees.locate(row['command']) if row['transport'] == 'stdio' else None
        if located:
            detail.update(tree=located[0], program=located[1])
        return detail

    def _connection_name(self, requested):
        if self.mcp is None:
            raise Failure('mcp_unavailable', 'Connections are unavailable in this session; nothing was installed.')
        if not CONNECTION_NAME.fullmatch(requested['name']):
            raise Failure('shelf_record', 'Connection names need 1–64 letters, digits, underscores or hyphens.')
        return requested['name']

    def _claim(self, name, replace, installation=None):
        """Whether a connection of this name exists already; a collision is refused before anything is recorded.

        During an update the installation's own entry is its to replace. An entry of
        that name that no longer carries its marker was changed in Settings and is kept.
        """
        owner = self.store.shelf.owner('connection', name)
        if owner is not None and owner != installation:
            raise Failure('shelf_collision', 'Another installed package already provides this connection. '
                          'Remove it first, or keep it and cancel this install.')
        existing = self.mcp.connections.read()['servers'].get(name)
        present = existing is not None
        if present and owner is not None and existing.get('installation') != installation:
            raise Failure('mcp_exists', 'This package’s connection was changed in Settings → Connections. '
                          'Remove the package, or that connection, before updating; nothing was changed.')
        if present and not replace and owner is None:
            raise Failure('mcp_exists', 'A connection with this name already exists. Choose to replace it, keep it, or cancel.')
        if present and (existing.get('secrets') or existing.get('oauth')) and (owner is None or owner != installation):
            # Keep the existing rule for taking over someone else's keyed entry.
            # Updating our own entry uses the credential owner's cleanup instead.
            raise Failure('mcp_credentials', 'A connection with this name has saved keys. Replace or remove it in '
                          'Settings → Connections first; nothing was installed.')
        return present

    @contextmanager
    def _closing(self, key):
        """Never leave an operation applying: an unexpected end closes it as failed.

        Its unsettled steps become uncertain and stay for inspection. Nothing is
        repeated, and a later explicit repair or removal proceeds without a restart.
        """
        try:
            yield
        except BaseException:
            if self.store.shelf.receipt(key)['status'] == 'applying':
                self.store.shelf.finish(key, 'failed')
            raise

    def _entry(self, name, installation):
        """What the settings hold under a connection name, for this installation.

        'ours' (its marked entry), 'other' (an entry added or replaced by hand, or
        another installation's), 'absent', or 'unreadable' when the settings cannot
        be read, which proves nothing either way.
        """
        try:
            row = self.mcp.connections.read()['servers'].get(name)
        except Failure:
            return 'unreadable'
        if row is None:
            return 'absent'
        return 'ours' if row.get('installation') == installation else 'other'

    def _outcome(self, key, ordinal):
        return self.store.shelf.receipt(key)['steps'][ordinal]['outcome']

    @staticmethod
    def _pin(manifest, revision):
        return dict(revision=revision, content_sha256=content_fingerprint(manifest))

    def _supported(self, installation):
        shelf = self.store.shelf
        record = shelf.installation(installation)
        if record is None or record['state'] == 'removed':
            raise Failure('shelf_missing', 'This installation is not present.')
        kinds = {m['kind'] for m in shelf.members(installation)} | {s['component_kind'] for s in shelf.unresolved(installation)}
        if kinds - {'skill', 'connection', 'package_tree', 'plugin_data', 'cache'}:
            raise Failure('shelf_unsupported', 'This installation has parts this version cannot inspect yet.')
        if kinds & {'connection', 'package_tree', 'plugin_data', 'cache'} and self.mcp is None:
            raise Failure('mcp_unavailable', 'Connections are unavailable in this session; nothing was changed.')

    def _inspect(self, installation):
        """Settle earlier steps whose result was never recorded; nothing is repeated.

        A skill step, and a Shelf-only release, commit with their record, so one still
        unsettled never took effect. A connection step is settled from the settings
        file: its entry is ours only while it carries this installation's marker. The
        settings are read only when a connection step needs them, so a package
        without connections never depends on them. Package-tree steps wait for
        ``_inspect_trees``, which has to hash what is on disk.
        """
        shelf = self.store.shelf
        rows = None
        members = {(m['kind'], m['key']): m for m in shelf.members(installation)}
        for step in shelf.unresolved(installation):
            if step['component_kind'] == 'package_tree':
                continue
            settle = lambda outcome, code, **change: shelf.settle(step['mutation_key'], step['ordinal'], outcome, code, **change)
            if step['component_kind'] in ('plugin_data', 'cache') and step['action'] in ('materialize', 'delete'):
                self._inspect_folder(step, settle, members)
                continue
            if step['component_kind'] != 'connection' or step['action'] not in ('register', 'unregister'):
                settle('failed', 'not_committed')
                continue
            if rows is None:
                rows = self.mcp.connections.read()['servers']
            row = rows.get(step['component_key'])
            ours = row is not None and row.get('installation') == installation
            if step['action'] == 'register':
                member = members.get(('connection', step['component_key']))
                if not ours:
                    settle('failed', 'not_registered')
                elif member is None:
                    settle('applied', 'inspection_found',
                           attach=('connection', step['component_key'], self._connection_detail(row)))
                elif member['detail'].get('configuration_sha256') == self.mcp.fingerprint(row):
                    settle('failed', 'not_switched')  # an update's step: the entry is still the one it held before
                else:
                    settle('applied', 'inspection_found', detach=member['id'],
                           attach=('connection', step['component_key'], self._connection_detail(row)))
            elif ours:
                settle('failed', 'still_registered')
            else:
                member = members.get(('connection', step['component_key']))
                settle('applied', 'inspection_removed', **({'detach': member['id']} if member else {}))

    def _inspect_folder(self, step, settle, members):
        """Settle a folder step by whether the folder is there; nothing is created or deleted."""
        kind, installation = step['component_kind'], step['component_key']
        try:
            present = self.mcp.data.state(kind, installation) == 'present'
        except Failure:
            settle('failed', 'folder_path_unsafe')  # a link in its path: not ours to enter, and left alone
            return
        member = members.get((kind, installation))
        if step['action'] == 'materialize':
            if not present:
                settle('failed', 'not_created')
            elif member:
                settle('applied', 'inspection_found')
            else:
                settle('applied', 'inspection_found',
                       attach=(kind, installation, dict(folder=self.mcp.data.relative(kind, installation))))
        elif present:
            settle('failed', 'still_present')
        else:
            settle('applied', 'inspection_removed', **({'detach': member['id']} if member else {}))

    async def _inspect_trees(self, installation):
        """Settle unknown package-tree steps by hashing what is on disk; nothing is changed or repeated.

        A tree's location is its pinned hash, so what is there is either that exact
        tree or not. Hashing runs in the tree owner's disposable process.
        """
        shelf = self.store.shelf
        for step in shelf.unresolved(installation):
            if step['component_kind'] != 'package_tree':
                continue
            settle = lambda outcome, code, **change: shelf.settle(step['mutation_key'], step['ordinal'], outcome, code, **change)
            name = step['component_key']
            if step['action'] not in ('materialize', 'delete'):
                settle('failed', 'not_committed')  # a Shelf-only release commits with its record
                continue
            # A materialize step is confirmed only for the pinned tree holding the
            # programs this installation's connections were installed to run from it.
            commands = self._declared(installation, name) if step['action'] == 'materialize' else []
            state = await self.mcp.trees.verify(name, self._allowance(web=False), commands)
            member = next((m for m in shelf.members(installation) if (m['kind'], m['key']) == ('package_tree', name)), None)
            if step['action'] == 'materialize':
                if state != 'ok':
                    settle('failed', dict(absent='not_materialized', different='tree_differs', incomplete='program_missing')[state])
                elif member:
                    settle('applied', 'inspection_found')
                else:
                    role = 'owner' if shelf.owner('package_tree', name) in (None, installation) else 'shared'
                    settle('applied', 'inspection_found',
                           attach=('package_tree', name, dict(tree_sha256=name.partition('/')[0]), role))
            elif state == 'absent':
                settle('applied', 'inspection_removed', **({'detach': member['id']} if member else {}))
            else:
                settle('failed', 'still_present')

    def _declared(self, installation, name):
        """The programs this installation's connections were installed to run from a tree.

        Read from the installation record, never from the settings: each connection
        it holds keeps the tree and program it was registered with, so replacing or
        editing the entry in Settings changes nothing here. A connection it no
        longer holds (released by a removal, or never registered) adds no program,
        and the package cannot read as installed without it: installed requires
        every declared connection to be held, and then this list is complete.
        """
        return [member['detail']['program'] for member in self.store.shelf.members(installation)
                if member['kind'] == 'connection' and member['detail'].get('tree') == name]
