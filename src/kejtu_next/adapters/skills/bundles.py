"""Read a package folder in a known bundle format, as data.

Four formats are read into one description: an Agent Plugins package (root
``plugin.json``, ``skills/``, ``mcp.json``), a Claude plugin
(``.claude-plugin/plugin.json``), a Codex plugin (``.codex-plugin/plugin.json``),
and an unpacked MCPB bundle (``manifest.json``). Claude and Codex packages use
``skills/`` and ``.mcp.json``. A single skill folder is a package of one skill.

The description lists the parts found, in the shape a Shelf install takes, and
every part that cannot be used, each with its reason. A part the package works
through (a skill, a tool) is essential: one that cannot be used makes the package
not installable. Extras of other products (commands, agents, hooks, app
connectors) are named and left out.

A local tool is never started through a command from outside its package (``npx``,
``node``, a system path): that would resolve and fetch a program when it starts.
It is listed only with a recipe, ``kejtu.recipe.json``, that pins an archive for
it; without one it needs preparation. The recipe carries what a manifest cannot:
the pin, and the program inside the archive when the manifest's own line cannot
be used. A manifest line that starts a file inside the package (``./…``) is used
as written, with its arguments, settings and working folder. Those may name the
package's two places, ``${PLUGIN_ROOT}`` and ``${PLUGIN_DATA}``; any other
placeholder is a setting someone would have to fill in, and is refused.

Nothing in the folder is executed or imported. Only the manifests are opened,
and each skill folder is read by the skill reader an install uses, so a skill
that is listed is one that installs. A link is never followed. Addresses and pins
are passed on as written: the owners an install asks judge them.
"""
import json
import os
from pathlib import Path
import re
import stat

from ...core.mcp import NAME as CONNECTION_NAME
from ...core.skills import path as skill_path
from .packages import local, prepare

RECIPE = 'kejtu.recipe.json'
MAX_MANIFEST = 256 * 1024
MAX_SKILLS, MAX_TOOLS, MAX_COMPONENTS, MAX_LISTED, MAX_UNSUPPORTED = 32, 16, 16, 256, 64
MAX_SUMMARY = 240  # what the connection parser takes as a description
_PACKAGE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._@/-]{0,127}\Z')
_PIN = dict(url=str, bytes=int, sha256=str, directory=str, unpacked_bytes=int, tree_sha256=str)
_NEVER = 'Doesn’t work in Kejtu: '
EXTRAS = (('.cursor', 'cursor', _NEVER + 'Cursor integration needs the app it was written for.'),
          ('commands', 'commands', 'Slash commands are not supported yet.'),
          ('agents', 'agents', _NEVER + 'agents need the app they were written for.'),
          ('hooks', 'hooks', _NEVER + 'hooks need to run code inside the app.'),
          ('hooks.json', 'hooks', _NEVER + 'hooks need to run code inside the app.'),
          ('.app.json', 'apps', _NEVER + 'app connectors need the product they were made for.'))
PREPARATION = 'It needs preparation: the package has no pinned program for this tool.'


def _cut(value, limit):
    """Text within a number of UTF-8 bytes, whole characters only."""
    return str(value).encode('utf-8', 'replace')[:limit].decode('utf-8', 'ignore')


def _text(value, limit, default=''):
    """One line of text within a byte bound, or the default."""
    if not isinstance(value, str) or not value.strip():
        return default
    return _cut(' '.join(value.split()), limit).strip()


class _Folder:
    """A package folder held open; every path is walked through directory handles, never through a link."""

    def __init__(self, source, check):
        if not all(hasattr(os, flag) for flag in ('O_DIRECTORY', 'O_NOFOLLOW')) or os.open not in os.supports_dir_fd:
            raise ValueError('Reading package folders is not available on this platform yet.')
        self.FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        folder = Path(source)
        if not folder.is_absolute() or folder.resolve() != folder or not folder.is_dir() or folder.is_symlink():
            raise ValueError('Choose a real absolute package folder without symbolic links.')
        self.check = check
        self.root = os.open('/', self.FLAGS)
        try:
            for component in folder.parts[1:]:
                child = os.open(component, self.FLAGS, dir_fd=self.root)
                os.close(self.root)
                self.root = child
        except BaseException:
            os.close(self.root)
            raise

    def skill(self, source, path):
        return local(str(Path(source) / path) if path else source, self.check)

    def close(self):
        os.close(self.root)

    @staticmethod
    def _kind(name, handle, relative):
        """'file', 'folder' or None for one entry of an open folder. A link is refused, wherever it points."""
        try:
            info = os.stat(name, dir_fd=handle, follow_symlinks=False)
        except (FileNotFoundError, NotADirectoryError):
            return None
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f'The package holds a link at {_cut(relative, 200)}; links are not read.')
        return 'folder' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else None

    def _parent(self, relative):
        """An open handle of the folder holding ``relative``, and its last name; no handle when a folder on the way is absent."""
        *folders, name = relative.split('/')
        handle = os.dup(self.root)
        try:
            for folder in folders:
                if self._kind(folder, handle, relative) != 'folder':
                    os.close(handle)
                    return None, name
                child = os.open(folder, self.FLAGS, dir_fd=handle)  # still refuses a link swapped in meanwhile
                os.close(handle)
                handle = child
        except BaseException:
            os.close(handle)
            raise
        return handle, name

    def kind(self, relative):
        self.check()
        handle, name = self._parent(relative)
        if handle is None:
            return None
        try:
            return self._kind(name, handle, relative)
        finally:
            os.close(handle)

    def read(self, relative, limit):
        """The bytes of one regular file, or None when it is not there."""
        if self.kind(relative) != 'file':
            return None
        handle, name = self._parent(relative)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=handle)
        finally:
            os.close(handle)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError(f'{relative} is not a regular file.')
            body = stream.read(limit + 1)
        if len(body) > limit:
            raise ValueError(f'{relative} is larger than a package file may be.')
        return body

    def document(self, relative):
        """A JSON object from one file, or None when the file is not there."""
        body = self.read(relative, MAX_MANIFEST)
        if body is None:
            return None
        try:
            value = json.loads(body.decode('utf-8'))
            json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')  # ordinary text and numbers only
        except (ValueError, RecursionError):
            raise ValueError(f'{relative} is not valid JSON.') from None
        if not isinstance(value, dict):
            raise ValueError(f'{relative} must hold one JSON object.')
        return value

    def names(self, relative):
        """The entries of one folder, sorted; empty when it is not there."""
        if self.kind(relative) != 'folder':
            return []
        handle, name = self._parent(relative)
        try:
            folder = os.open(name, self.FLAGS, dir_fd=handle)
        finally:
            os.close(handle)
        try:
            names = sorted(os.listdir(folder))
        finally:
            os.close(folder)
        if len(names) > MAX_LISTED:
            raise ValueError(f'{relative} holds more entries than a package folder may.')
        return names


class _Files(_Folder):
    """The same reader over already verified GitHub blobs; portable and disk-free."""

    def __init__(self, files, origin, check, links=()):
        self.files, self.origin, self.check = files, origin, check
        self.links = set(links)
        self.directories = {p.rsplit('/', 1)[0] for p in files if '/' in p}
        self.directories |= {p[:i] for p in self.directories.copy() for i, c in enumerate(p) if c == '/'}

    def kind(self, relative):
        self.check()
        if any(relative == p or relative.startswith(p + '/') for p in self.links):
            raise ValueError('A package component references a symbolic link; links are not followed.')
        return 'file' if relative in self.files else 'folder' if relative in self.directories else None

    def read(self, relative, limit):
        self.kind(relative)
        body = self.files.get(relative)
        if body is not None and len(body) > limit:
            raise ValueError(f'{relative} is larger than a package file may be.')
        return body

    def names(self, relative):
        prefix = relative + '/' if relative else ''
        names = sorted({p[len(prefix):].split('/')[0] for p in self.files if p.startswith(prefix)})
        if len(names) > MAX_LISTED:
            raise ValueError('The package holds too many directory entries.')
        return names

    def skill(self, source, path):
        prefix = path + '/' if path else ''
        if any(p.startswith(prefix) for p in self.links):
            raise ValueError('Skill folders cannot contain symbolic links.')
        files = {p[len(prefix):]: b for p, b in self.files.items() if p.startswith(prefix)}
        origin = dict(self.origin, path='/'.join(p for p in (self.origin.get('path'), path) if p))
        return prepare(files, origin)

    def close(self):
        pass


def read_files(files, origin, check=lambda: None, *, links=()):
    """Describe downloaded bytes without extracting or executing upstream files."""
    for path in files:
        skill_path(path)
    folder = _Files(files, origin, check, links)
    description = _read('/catalogue/package', folder)
    packages = {c['name']: folder.skill('', c['path']) for c in description['components'] if c['kind'] == 'skill'}
    return dict(description=description, packages=packages)


def _sign_in(entry):
    """'needed' when a tool cannot be reached without a key or a sign-in, 'partly' when only some of it, else None."""
    extensions = entry.get('extensions') if isinstance(entry.get('extensions'), dict) else {}
    declared = [entry.get('auth'), *(e.get('auth') for e in extensions.values() if isinstance(e, dict))]
    kinds = {str(a.get('type', 'none')) if isinstance(a, dict) else str(a) for a in declared if a is not None}
    if entry.get('headers') or entry.get('oauth') or kinds - {'none', 'mixed'}:
        return 'needed'
    return 'partly' if 'mixed' in kinds else None


def _pinned(value):
    """A recipe's entry for one tool, in shape only; the tree owner judges the pin itself."""
    if not isinstance(value, dict) or set(value) - {'artifact', 'command', 'args', 'env', 'cwd', 'inputs', 'protocol_mode', 'network'}:
        return None
    artifact = value.get('artifact')
    if (not isinstance(artifact, dict) or not set(_PIN) <= set(artifact) <= {*_PIN, 'dependencies'}
            or any(type(artifact[key]) is not kind for key, kind in _PIN.items())
            or not isinstance(artifact.get('dependencies', ''), str)):
        return None
    return dict(artifact)


def _launch(source, own):
    """A program's launch line in the shape an install takes, or the reason (text) it cannot be used.

    ``own`` are the spellings this format uses for the package root, read as
    ``${PLUGIN_ROOT}``. The line is passed on as written otherwise: the connection
    owner judges it, and the launch owner fills in the two places.
    """
    command, args, env, cwd = source.get('command'), source.get('args', []), source.get('env', {}), source.get('cwd')
    if (not isinstance(command, str) or not 1 <= len(command.encode()) <= 512
            or not isinstance(args, list) or len(args) > 64 or any(not isinstance(a, str) for a in args)
            or not isinstance(env, dict) or len(env) > 24
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in env.items())
            or cwd is not None and not isinstance(cwd, str)):
        return 'It needs preparation: the way this tool is started is not readable.'
    def place(text):
        for spelling in own:
            text = text.replace(spelling, '${PLUGIN_ROOT}')
        return text
    args, env, cwd = [place(a) for a in args], {k: place(v) for k, v in env.items()}, None if cwd is None else place(cwd)
    inputs = source.get('inputs')
    if inputs is not None:
        from ..mcp.config import input_fields
        try:
            input_fields(inputs)
        except ValueError:
            return 'The package account fields are not valid.'
        # Bind a whole environment value only. No secret interpolation in
        # arguments, paths, URLs, or partial strings, and no process environment lookup.
        for field in inputs:
            if env.get(field['name']) != '${' + field['name'] + '}':
                return 'Each account field must bind its complete environment value.'
            env[field['name']] = ''
    from ..mcp.files import SHARED_ROOT
    # Folder authority comes from Files, not from package input or ambient env.
    # Only whole argument/environment values may request the existing root.
    if (any(SHARED_ROOT in value and value != SHARED_ROOT for value in [*args, *env.values()])
            or cwd is not None and SHARED_ROOT in cwd):
        return 'A shared-folder root must occupy a complete argument or environment value.'
    texts = [*args, *env.values(), *([cwd] if cwd is not None else [])]
    asked = sorted({name for text in texts for name in re.findall(r'\$\{([^}]*)\}', text)} - {'PLUGIN_ROOT', 'PLUGIN_DATA', 'KEJTU_SHARED_ROOT'})
    if asked:
        return f'It needs a setting filled in ({_cut(asked[0], 80)}), which a package cannot carry yet.'
    if (any(len(a.encode()) > 4096 for a in args) or any(len(k.encode()) > 80 or len(v.encode()) > 8192 for k, v in env.items())
            or cwd is not None and len(cwd.encode()) > 600):
        return 'It needs preparation: the way this tool is started is longer than a package may use.'
    line = dict(command=command, args=args)
    if inputs:
        line['inputs'] = inputs
    if 'protocol_mode' in source:
        if source['protocol_mode'] not in ('auto', 'legacy'):
            return 'Unsupported MCP handshake mode.'
        line['protocol_mode'] = source['protocol_mode']
    if 'network' in source:
        if type(source['network']) is not bool:
            return 'Network access must be true or false.'
        line['network'] = source['network']
    if env:
        line['env'] = [dict(name=name, value=value) for name, value in env.items()]
    if cwd is not None:
        line['cwd'] = cwd
    return line


def _tool(name, entry, programs, missing, summary, components, unsupported, form='agent-plugins'):
    """One declared tool: an installable part, or the reason it is not one."""
    def refuse(reason, essential=True):
        unsupported.append(dict(kind='tool', name=_cut(name, 128), essential=essential, reason=reason))
    if not isinstance(name, str) or not CONNECTION_NAME.fullmatch(name):
        return refuse('Its name cannot be used for a connection (1–64 letters, digits, underscores or hyphens).')
    if not isinstance(entry, dict):
        return refuse('Its entry is not readable.')
    kind, description = entry.get('type'), _text(entry.get('description'), MAX_SUMMARY, summary)
    if 'url' in entry or kind in ('streamable-http', 'http', 'sse'):
        url, sign_in = entry.get('url'), _sign_in(entry)
        if kind == 'sse':
            return refuse('It uses the older SSE transport, which is not supported yet.')
        if sign_in == 'needed':
            return refuse('It needs a key or a sign-in, which a package cannot carry yet.')
        if not isinstance(url, str) or not url or len(url.encode()) > 2048:
            return refuse('It gives no usable address.')
        if '${' in url:
            return refuse('Its address needs a setting filled in, which a package cannot carry yet.')
        if sign_in == 'partly':
            refuse('Some of what it offers needs a sign-in, which is not supported yet; the rest works.', essential=False)
        return components.append(dict(kind='connection', name=name, url=url, description=description))
    if 'command' in entry or kind == 'stdio':
        if name not in programs:
            return refuse(missing)
        artifact = _pinned(programs[name])
        if artifact is None:
            return refuse('It needs preparation: the recipe’s entry for this tool is not readable.')
        own = ('${CLAUDE_PLUGIN_ROOT}',) if form == 'claude-plugin' else ()
        if 'command' in programs[name]:
            source = programs[name]  # the recipe names the program inside its archive
        else:
            # The package's own line, and only when it starts a file inside the package:
            # the pinned archive is then that package. Never a command from outside it.
            command = entry.get('command')
            inside = next((command[len(prefix):] for prefix in ('./', *(spelling + '/' for spelling in own))
                           if isinstance(command, str) and command.startswith(prefix)), None)
            if inside is None:
                return refuse('It needs preparation: the recipe names no program for this tool, and the package '
                              'starts it with a command from outside the package.')
            source = dict(entry, command=inside)
        line = _launch(source, own)
        if isinstance(line, str):
            return refuse(line)
        return components.append(dict(kind='program', name=name, description=description, artifact=artifact, **line))
    return refuse('It is not a kind of tool Kejtu knows.')


def _tools(form, manifest, folder, unsupported):
    """Every tool the package declares, by name."""
    if form == 'mcpb':
        # A bundle is one local program. Its own launch line is never used.
        return {re.sub(r'[^A-Za-z0-9_-]', '-', _text(manifest.get('name'), 64, 'tool')): dict(type='stdio')}
    default = 'mcp.json' if form == 'agent-plugins' else '.mcp.json'
    declared = manifest.get('mcpServers')
    if isinstance(declared, dict):
        servers = declared  # listed in the manifest itself
    elif declared is None or (isinstance(declared, str) and declared.removeprefix('./') == default):
        document = folder.document(default)
        if document is None:
            return {}
        servers = document.get('mcpServers', document if form == 'claude-plugin' else None)
    else:
        servers = None
    if not isinstance(servers, dict):
        unsupported.append(dict(kind='package', name='tools', essential=True,
                                reason='It lists its tools in a place or a form Kejtu does not read.'))
        return {}
    return {name: entry for name, entry in servers.items() if not str(name).startswith('$')}


def _skills(form, source, folder, components, unsupported, declared=None):
    """Each skill folder under skills/, or the folder itself: its declared name and where it is."""
    if form == 'skill':
        found = [('', Path(source).name)]
    else:
        roots = ['skills']
        if declared not in (None, 'skills', 'skills/', './skills', './skills/'):
            roots += declared if isinstance(declared, list) else [declared]
        found, seen = [], set()

        def visit(path, depth=0, explicit=False):
            if not isinstance(path, str):
                raise ValueError('Declared skill folders must be relative paths.')
            path = path.removeprefix('./').removesuffix('/')
            skill_path(path)
            if path in seen:
                return
            seen.add(path)
            if depth > 8 or len(seen) > MAX_LISTED:
                raise ValueError('The package nests too many skill folders.')
            if folder.kind(path + '/SKILL.md') == 'file':
                found.append((path, path.rsplit('/', 1)[-1]))
                return
            children = [n for n in folder.names(path) if folder.kind(path + '/' + n) == 'folder']
            if not children and explicit:
                found.append((path, path.rsplit('/', 1)[-1]))
                return
            for child in children:
                visit(path + '/' + child, depth + 1, True)

        try:
            for index, root in enumerate(roots):
                visit(root, explicit=index > 0)
        except ValueError as error:
            if 'more entries than' in str(error) or 'too many directory entries' in str(error):
                raise
            unsupported.append(dict(kind='package', name='skills', essential=True, reason=_cut(error, 512)))
        if len(found) > MAX_SKILLS:
            raise ValueError('The package holds more skills than one package may.')
    described = None
    for path, name in found:
        try:
            if path:
                skill_path(path)
            if folder.kind((path + '/' if path else '') + 'SKILL.md') != 'file':
                raise ValueError('It has no SKILL.md.')
            # The whole skill folder, through the reader an install uses: its bounds and its refusals.
            meta = folder.skill(source, path)['manifest']['metadata']
            if len(meta['name'].encode()) > 64:
                raise ValueError('Its name is longer than a package may use.')
        except UnicodeError:
            unsupported.append(dict(kind='skill', name=_cut(name, 128), essential=True, reason='Its SKILL.md is not UTF-8 text.'))
            continue
        except (ValueError, OSError) as error:
            unsupported.append(dict(kind='skill', name=_cut(name, 128), essential=True, reason=_cut(error, 512)))
            continue
        components.append(dict(kind='skill', name=meta['name'], path=path))
        described = meta
    return described


def _format(folder):
    """Which format the folder is, and its manifest."""
    plugin = folder.document('plugin.json')
    if plugin is not None and str(plugin.get('$schema', '')).startswith('https://agent-plugins.org/schemas/'):
        return 'agent-plugins', plugin
    claude = folder.document('.claude-plugin/plugin.json')
    if claude is not None:
        return 'claude-plugin', claude
    # Codex manifests describe the same skill folders and wrapped MCP settings
    # read below. App registrations and agent metadata stay unsupported extras;
    # recognizing this manifest never grants access or imports another app's login.
    codex = folder.document('.codex-plugin/plugin.json')
    if codex is not None:
        return 'codex-plugin', codex
    bundle = folder.document('manifest.json')
    if bundle is not None and ('manifest_version' in bundle or 'dxt_version' in bundle) and 'server' in bundle:
        return 'mcpb', bundle
    if folder.kind('SKILL.md') == 'file':
        return 'skill', {}
    raise ValueError('This folder is not a package Kejtu can read: it has no Agent Plugins, Claude/Codex plugin or MCPB '
                     'manifest and no SKILL.md at its top.')


def read(source, check=lambda: None):
    """Describe one package folder: its format, the parts found, and what cannot be used, with reasons."""
    return _read(source, _Folder(source, check))


def _read(source, folder):
    check = folder.check
    try:
        form, manifest = _format(folder)
        components, unsupported = [], []
        skill = _skills(form, source, folder, components, unsupported, manifest.get('skills'))
        if form == 'skill':
            if skill is None:
                raise ValueError(unsupported[-1]['reason'])  # the folder is that one skill, and it cannot be read
            manifest = dict(name=skill['name'], description=skill['description'])
        name = _text(manifest.get('name'), 128)
        if not _PACKAGE.fullmatch(name):
            raise ValueError('The package does not give a usable name.')
        description = _text(manifest.get('description'), 960, name)
        if form != 'skill':
            # Tools, with the recipe that pins a program for each local one.
            programs, missing, recipe = {}, PREPARATION, folder.document(RECIPE)
            if recipe is not None and (recipe.get('recipe') != 1 or not isinstance(recipe.get('programs'), dict)):
                missing = 'It needs preparation: the package’s recipe is not one this version reads.'
            elif recipe is not None:
                programs = recipe['programs']
            tools = _tools(form, manifest, folder, unsupported)
            if len(tools) > MAX_TOOLS:
                raise ValueError('The package holds more tools than one package may.')
            for tool, entry in tools.items():
                check()
                _tool(tool, entry, programs, missing, description[:MAX_SUMMARY], components, unsupported, form)
        # What other products add, named and left out.
        settings = manifest.get('user_config') if form == 'mcpb' else None
        if isinstance(settings, dict) and settings:
            unsupported.append(dict(kind='settings', name='settings',
                                    essential=any(isinstance(v, dict) and v.get('required') for v in settings.values()),
                                    reason='It asks for settings to be filled in, which a package cannot carry yet.'))
        named = set()
        for entry, kind, reason in () if form in ('mcpb', 'skill') else EXTRAS:
            if kind not in named and folder.kind(entry) is not None:
                named.add(kind)
                unsupported.append(dict(kind=kind, name=kind, essential=False, reason=reason))
        if not components and not any(u['essential'] for u in unsupported):
            unsupported.append(dict(kind='package', name=name, essential=True, reason='It holds nothing Kejtu can install.'))
        if len(components) > MAX_COMPONENTS:
            unsupported.append(dict(kind='package', name=name, essential=True, reason='It holds more parts than one package may.'))
        keys = [(c['kind'] == 'skill', c['name']) for c in components]
        if len(set(keys)) != len(keys):
            unsupported.append(dict(kind='package', name=name, essential=True, reason='Two of its parts have the same name.'))
        return dict(format=form, name=name, version=_text(manifest.get('version'), 128, 'unversioned'),
                    description=description, directory=str(Path(source)), components=components[:MAX_COMPONENTS],
                    unsupported=unsupported[:MAX_UNSUPPORTED],
                    installable=bool(components) and not any(u['essential'] for u in unsupported))
    finally:
        folder.close()
