"""Operator-owned shared folders; one atomic connection/settings authority.

The model cannot change this configuration. User data stays at its original path;
removing a grant never removes files. Shared writes replace legacy per-connection
write mounts on the first explicit save, rather than leaving hidden write access.
"""
from pathlib import Path
import os
import sys
import shutil

from ...core.records import Failure

FILES_SERVER = 'kejtu-files'
SHARED_ROOT = '${KEJTU_SHARED_ROOT}'
VERSION = '1.0.0'
WINDOWS_VERSION = '2.0.1'
READ_TOOLS = ['read_text_file','read_media_file','read_multiple_files','list_directory',
    'list_directory_with_sizes','directory_tree','search_files','get_file_info','list_allowed_directories']
WRITE_TOOLS = ['write_file','edit_file','create_directory','move_file']


def validate(value):
    if (not isinstance(value,dict) or set(value)!={'enabled','workspace','folders','servers'}
            or type(value['enabled']) is not bool or value['workspace'] is not None and not isinstance(value['workspace'],str)
            or not isinstance(value['folders'],list) or len(value['folders'])>32
            or not isinstance(value['servers'],list) or len(value['servers'])>16
            or any(not isinstance(v,str) or not v or len(v)>80 for v in value['servers'])
            or len(set(value['servers']))!=len(value['servers'])):
        raise ValueError('Invalid shared folder settings.')
    paths=[]
    if value['workspace'] is not None:paths.append(value['workspace'])
    for row in value['folders']:
        if not isinstance(row,dict) or set(row)!={'path','access'} or row['access'] not in ('read','write'):
            raise ValueError('Folders need a path and read or write access.')
        paths.append(row['path'])
    if any(not isinstance(p,str) or not p or len(p.encode())>4096 or '\0' in p or not Path(p).is_absolute() for p in paths):
        raise ValueError('Use complete absolute folder paths.')
    if len(set(paths))!=len(paths):raise ValueError('List each folder only once, including the working folder.')
    return value


def grants(value):
    return ([dict(path=value['workspace'],access='write')] if value['workspace'] else [])+value['folders']


def bind_shared_root(row, shared):
    """Resolve a server's root argument from Files, without granting its parent.

    Some servers accept only one confinement root. Its common ancestor covers
    every selected folder; the worker still mounts ONLY the exact shares with
    their read/write overrides. This never adds a parent filesystem grant.
    """
    if row['transport'] != 'stdio' or SHARED_ROOT not in row['args']:
        return
    if not shared:
        # Keep the saved connection permission, but withhold an unusable server.
        # Do not fall back to its old root or a writable temporary directory.
        row['enabled'] = False
        return
    root = os.path.commonpath([item['path'] for item in shared])
    row['args'] = [root if arg == SHARED_ROOT else arg for arg in row['args']]
    row['shared_root'] = root


class Files:
    def __init__(self, host):
        self.host=host

    @property
    def private(self):return self.host.connections.path.parent.resolve()

    @property
    def private_roots(self):
        # Installation state includes backups, logs, launch inputs and runtimes.
        # The archive may also be explicitly located outside that installation.
        roots={(self.host.root/'.state').resolve(),self.private}
        if sys.platform == 'linux':
            from .desktop_paths import linux_desktop_paths
            roots.update(path.resolve() for path in linux_desktop_paths())
        if sys.platform == 'win32':
            # A broad Home share must not permit edits to the code that owns
            # the next file request. Packaged state also has an adjacent pin.
            roots.add(self.host.root.resolve())
            if self.private.name == 'runtime' and self.private.parent.name == 'Kejtu-Test':
                roots.add(self.private.parent)
        return tuple(sorted(roots,key=lambda p:(len(p.parts),str(p))))

    def is_private(self,path):
        if sys.platform == 'linux':path=Path(path).resolve()
        return any(path==root or root in path.parents for root in self.private_roots)

    def code_dependency(self,path):
        # Only an explicitly selected descendant of these installation areas may
        # reappear read-only after masking state. Never re-expose the archive,
        # even when an operator located it inside a program installation area.
        if sys.platform == 'linux':path=path.resolve()
        if path==self.private or path in self.private.parents or self.private in path.parents:
            return False
        if sys.platform == 'linux':
            from .desktop_paths import linux_desktop_paths
            # A custom desktop location inside a runtime must not be restored
            # with that runtime's otherwise legitimate read-only exception.
            for desktop in linux_desktop_paths():
                desktop=desktop.resolve()
                if path==desktop or path in desktop.parents or desktop in path.parents:
                    return False
        state=(self.host.root/'.state').resolve()
        return any(root in path.parents for root in
            (state/'apps',state/'mcp-runtime',state/'playwright'))

    def protect(self,row):
        if row['transport']!='stdio':return
        row['private_paths']=[str(p) for p in self.private_roots]
        if sys.platform == 'linux':
            from .desktop_paths import linux_desktop_paths
            row['desktop_private_paths']={str(p):str(p.resolve()) for p in linux_desktop_paths()}
        row['private_read_paths']=[p for p in row['read_paths']
            if self.is_private(Path(p)) and self.code_dependency(Path(p))]

    def validate_connection_paths(self,reads,writes):
        if any(self.is_private(Path(p)) and not self.code_dependency(Path(p)) for p in reads):
            raise Failure('files_private','Kejtu’s private app data is not a connection folder. Select only the required installed program directory.')
        if any(self.is_private(Path(p)) for p in writes):
            raise Failure('files_private','Kejtu’s private app data is not a writable connection folder.')
        if sys.platform == 'linux':
            from .runtime_protection import validate_write_roots
            validate_write_roots(self.host.root, writes)

    def runtime(self):
        if sys.platform == 'win32':
            root=(self.host.root/'package-runtime/mcp/filesystem').resolve()
            node=self.host.root/'.venv/Scripts/site-packages/playwright/driver/node.exe'
            if not node.is_file() or not (self.host.root/'package-runtime/mcp/python/mcp').is_dir():
                raise Failure('files_runtime','The built-in Files component is missing. Repair the Kejtu installation.')
        else:
            root=(self.host.root/'.state/mcp-runtime/filesystem').resolve()
            node=shutil.which('node',path='/usr/bin:/bin')
        entry=root/'node_modules/@modelcontextprotocol/server-filesystem/dist/index.js'
        if not node or not entry.is_file():
            raise Failure('files_runtime','The built-in Files component is missing. Repair the Kejtu installation.')
        import json
        if json.loads((entry.parent.parent/'package.json').read_text()).get('version')!=(WINDOWS_VERSION if sys.platform=='win32' else VERSION):
            raise Failure('files_runtime','The Files component does not match this Kejtu release.')
        return root,entry,str(Path(node).resolve())

    def shareable(self, row):
        if row['transport'] != 'stdio': return False
        from .windows_connections import known
        return not (sys.platform == 'win32' and known(row.get('command')))

    def initial(self, value):
        # Existing document-tool writes are visible before opting into Files.
        folders=sorted({p for row in value['servers'].values() for p in row.get('write_paths',[])})
        return dict(enabled=False,workspace=None,folders=[dict(path=p,access='write') for p in folders],
            servers=[name for name,row in value['servers'].items() if self.shareable(row)])

    def status(self):
        value=self.host.connections.read();config=value.get('files') or self.initial(value)
        config=dict(config, servers=[name for name in config['servers']
            if name in value['servers'] and self.shareable(value['servers'][name])])
        try:self.runtime();available=True
        except (Failure,OSError,ValueError):available=False
        rows=[]
        for row in grants(config):
            p=Path(row['path'])
            rows.append(dict(**row,exists=p.is_dir(),available=p.is_dir() and str(p.resolve())==str(p)))
        from .windows_connections import known
        tools=[dict(id=name,description=row['description'],enabled=row['enabled'],shared=name in config['servers'])
            for name,row in value['servers'].items() if row['transport']=='stdio'
                and not (sys.platform == 'win32' and known(row.get('command')))]
        # Dependencies remain explicit, read-only and separate from user folders.
        dependencies=[dict(server=name,path=p) for name,row in value['servers'].items() for p in row.get('read_paths',[])]
        return dict(revision=value['revision'],configured='files' in value,enabled=config['enabled'],
            workspace=config['workspace'],folders=config['folders'],servers=config['servers'],
            suggested_workspace=str(Path.home()/'Kejtu'),home=str(Path.home()),documents=str(Path.home()/'Documents'),
            runtime_available=available,locations=rows,tools=tools,connection_read_folders=dependencies)

    def _path(self, raw, *, create=False):
        if sys.platform == 'win32':
            from .windows_runtime import folder_input
            folder_input(raw)
        p=Path(raw).expanduser()
        if not p.is_absolute() or '\0' in str(p) or len(str(p).encode())>4096:
            raise Failure('files_path','Choose a complete folder path.')
        # Resolve selected symlink aliases once, display/save their actual target.
        real=p.resolve()
        if real==Path(real.anchor) or real in (Path('/proc'),Path('/sys'),Path('/dev')):
            raise Failure('files_path','Choose Home, Documents or another folder, rather than a system root.')
        if self.is_private(real):
            raise Failure('files_private','Kejtu’s private app data is not a working or shared folder.')
        if create:
            if not real.exists() and not real.parent.is_dir():
                raise Failure('files_path','Choose an existing parent folder for the new Kejtu folder.')
            if real.exists() and not real.is_dir():raise Failure('files_path','A file already occupies this location.')
        elif not real.is_dir():raise Failure('files_path','This folder is unavailable. Choose an existing folder.')
        return str(real)

    def save(self, args):
        value=self.host.connections.read()
        if args['expected_revision']!=value['revision']:
            raise Failure('mcp_conflict','File access or connections changed. Refresh before saving again.')
        config={k:args[k] for k in ('enabled','workspace','folders','servers')}
        try:validate(config)
        except (ValueError,TypeError):raise Failure('files_settings','Use unique folders with read-only or read/write access.') from None
        if any(name not in value['servers'] or value['servers'][name]['transport']!='stdio' for name in config['servers']):
            raise Failure('files_tools','Choose current local tools; remote tools cannot mount folders.')
        if sys.platform == 'win32':
            from .windows_connections import known
            if any(known(value['servers'][name].get('command')) for name in config['servers']):
                raise Failure('files_tools', 'Packaged document apps use normal Windows account access. These folder permissions apply to built-in Files.')
        if args['create_workspace'] and not config['workspace']:
            raise Failure('files_path','Choose where to create the working folder.')
        if config['enabled']:self.runtime()
        config=dict(config,workspace=self._path(config['workspace'],create=args['create_workspace']) if config['workspace'] else None,
            folders=[dict(path=self._path(row['path']),access=row['access']) for row in config['folders']])
        try:validate(config)
        except ValueError:raise Failure('files_path','These choices point to the same folder. List it only once.') from None
        if sys.platform == 'linux' and config['enabled']:
            from .runtime_protection import validate_write_roots
            validate_write_roots(self.host.root, [row['path'] for row in grants(config)
                                                if row['access'] == 'write'])
        if config['enabled'] and not grants(config):raise Failure('files_path','Choose a working folder or at least one shared folder.')
        # This is an explicit operator save. No writes occur just by opening the UI.
        if args['create_workspace']:
            try:Path(config['workspace']).mkdir(mode=0o700,exist_ok=True)
            except OSError:raise Failure('files_create','The folder could not be created here. Choose a writable location.') from None
        if config['enabled']:
            for row in grants(config):
                flags=os.R_OK|os.X_OK|(os.W_OK if row['access']=='write' else 0)
                if not os.access(row['path'],flags):raise Failure('files_access','The selected access is unavailable for '+row['path'])
        # No duplicate legacy write authority survives the visible shared choices.
        for row in value['servers'].values():
            if row['transport']=='stdio':row['write_paths']=[]
        value['files']=config
        self.host.connections.save(value,args['expected_revision']);self.host.inspections.clear()
        return self.status()

    def disable(self, revision):
        value=self.host.connections.read()
        if 'files' not in value:return self.status()
        value['files']['enabled']=False
        self.host.connections.save(value,revision);self.host.inspections.clear()
        return self.status()

    def settings(self):
        value=self.host.connections.read();config=value.get('files')
        if not config:
            for row in value['servers'].values():
                bind_shared_root(row, [])
                self.protect(row)
                if sys.platform == 'win32':
                    from .windows_runtime import is_builtin
                    if not is_builtin(row): row['enabled'] = False
                    if row.get('command') == '@kejtu/browser': row['requires_web'] = True
            return value
        shared=grants(config) if config['enabled'] else []
        for name,row in value['servers'].items():
            if row['transport']!='stdio':continue
            if sys.platform == 'win32':
                from .windows_connections import known
                if known(row.get('command')):continue
            row['shared_folders']=shared if name in config['servers'] else []
            self.protect(row)
            bind_shared_root(row, row['shared_folders'])
        if shared:
            try:root,entry,node=self.runtime()
            except (Failure,OSError,ValueError):return value
            value['servers'][FILES_SERVER]=dict(description='Read, write and organise your shared files.',transport='stdio',
                enabled=True,allowed_tools=READ_TOOLS+(WRITE_TOOLS if any(r['access']=='write' for r in shared) else []),
                repeatable_tools=READ_TOOLS,requires_web=False,command=node,args=[str(entry),*[r['path'] for r in shared]],env={},
                read_paths=[str(root)],write_paths=[],network=False,shared_folders=shared)
            self.protect(value['servers'][FILES_SERVER])
            if sys.platform == 'win32':
                # Only this virtual, non-importable server gets the trusted
                # Windows launcher; arbitrary connections retain their refusal.
                value['servers'][FILES_SERVER]['builtin_files'] = True
        if sys.platform == 'win32':
            for name,row in value['servers'].items():
                from .windows_runtime import is_builtin
                if not is_builtin(row): row['enabled'] = False
                if row.get('command') == '@kejtu/browser': row['requires_web'] = True
        return value
