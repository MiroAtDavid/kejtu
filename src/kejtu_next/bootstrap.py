"""Explicit CLI construction; importing this module performs no IO."""
import asyncio
from contextlib import closing
import getpass
import hashlib
import json
import os
import sqlite3
import sys
import warnings
import uuid
from pathlib import Path

from .adapters.models.deepseek import DeepSeek, validate_key
from .adapters.models.embedding import LocalEmbedding
from .adapters.models.ollama import Ollama
from .adapters.storage.sqlite import SqliteStore
from .adapters.storage.files import open_regular
from .adapters.storage.model_settings import SavedModels
from .adapters.models.credentials import DesktopKeyring
from .adapters.models.session import ModelSession
from .application.turn import TurnService
from .core.providers import provider
from .core.search_providers import PROVIDERS as SEARCH_PROVIDERS
from .adapters.models.chat_completions import ChatCompletions
from .core.records import Failure
from .interfaces.cli import execute, parse_args


def initialize_desktop_web(store, host):
    """First desktop initialization only; reopening never restores revoked grants."""
    current = store.web.settings()
    store.web.settings_operation(dict(action='set', enabled=True, provider='tavily',
        expected_revision=current['revision'], mutation_key=uuid.uuid4().hex))
    marker = 'browser-runtime.json' if sys.platform == 'win32' else '.state/mcp-runtime/browser.json'
    if (host.root / marker).is_file():
        host.add_browser(host.connections.read()['revision'])


def initialize_desktop_terminals(host):
    from .adapters.mcp.terminals import DIRECTORY, NAMES
    if sys.platform == 'linux' and (host.root / '.state/mcp-runtime' / DIRECTORY).is_dir():
        for name in NAMES:
            host.setup(name, host.connections.read()['revision'])


def initialize_desktop_workspace(store):
    """Fresh desktop choices; never replace an existing saved workspace policy."""
    from .application.clock import ClockContext

    current = store.planner.settings()
    if current['revision'] != 0:
        return
    settings = current['settings']
    settings.update(university_enabled=True, model_read=True, model_write=True)
    # Reuse the application clock's portable named-zone lookup. Persist this
    # once; later travel or startup must not overwrite the saved calendar zone.
    zone = ClockContext(0, lambda: 0).zone_name
    if zone:
        settings['availability']['timezone'] = zone
    store.planner.save_settings(settings, current['revision'], uuid.uuid4().hex,
                                layout_revision=current['layout_revision'])


def initialize_desktop_reflection(store, profile, *, notes_write):
    """Seed the first real desktop only; saved jobs keep their own authority."""
    from datetime import datetime, timezone
    from uuid import uuid4
    from .application.clock import ClockContext
    from .core.jobs import JobDefinition, JobPermissions
    from .core.schedules import Schedule

    clock = ClockContext(0, lambda: 0)
    zone = clock.zone_name or 'UTC'
    now = datetime.now(timezone.utc)
    start = now.astimezone(clock.zone if clock.zone_name else timezone.utc)
    definition = JobDefinition('idle_review', 'Reflection', None,
        Schedule(start.isoformat(), zone, repeat='daily'), profile,
        permissions=JobPermissions(read_memory=True, write_notes=notes_write),
        enabled=True)
    receipt = store.jobs.save(definition, mutation_key=uuid4().hex, now=now.isoformat())
    care = JobDefinition('notebook_care', 'Keep notebook tidy', None,
        Schedule(start.isoformat(), zone, repeat='daily'), profile,
        permissions=JobPermissions(read_memory=True, write_notes=notes_write),
        enabled=True)
    store.jobs.save(care, mutation_key=uuid4().hex, now=now.isoformat())
    review = JobDefinition('reminder_review','Review leftover reminders',None,
        Schedule(start.isoformat(),zone,repeat='daily'),profile,
        permissions=JobPermissions(read_memory=True),enabled=True)
    store.jobs.save(review,mutation_key=uuid4().hex,now=now.isoformat())
    return receipt


def native_profile(path: Path, expected_sha256: str | None = None):
    """Read one explicit, bounded file; configuration failure never selects a fake."""
    from dataclasses import asdict
    from .core.records import Profile
    from .interfaces.native_wire import matches

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate profile field')
            result[key] = value
        return result

    try:
        with open_regular(path) as source:
            raw = source.read(16385)
        if len(raw) > 16384 or (expected_sha256 is not None
                and hashlib.sha256(raw).hexdigest() != expected_sha256):
            raise ValueError('profile size or identity')
        profile = Profile(**json.loads(raw, object_pairs_hook=unique))
        if not matches(asdict(profile), 'profile'):
            raise ValueError('native profile is not supported')
        return profile
    except (OSError, ValueError, TypeError, UnicodeError):
        raise Failure('native_profile',
            'Select a valid bounded provider profile file; no provider fallback was used.') from None


def native_standing_profile(path: Path, expected_sha256: str | None = None) -> str:
    """Exact private text, explicitly selected; never discover another archive."""
    try:
        with open_regular(path) as source:
            raw = source.read(16385)
        if len(raw) > 16384 or (expected_sha256 is not None
                and hashlib.sha256(raw).hexdigest() != expected_sha256):
            raise ValueError('standing profile size or identity')
        return raw.decode('utf-8')  # No stripping or newline normalization.
    except (OSError, ValueError, UnicodeError):
        raise Failure('native_standing_profile',
            'Select a valid bounded UTF-8 standing profile; no conversation was created.') from None


def wire_chatgpt(session, saved):
    """Compose credential storage and subscription transport without family imports."""
    from .adapters.storage.private_records import PrivateRecordFile
    from .adapters.models.chatgpt_accounts import ChatGPTAccounts
    from .adapters.models.chatgpt import ChatGPTModel
    session.chatgpt = ChatGPTAccounts(PrivateRecordFile(saved.path.parent / 'chatgpt'), saved.state_id)
    session.models['chatgpt'] = ChatGPTModel(session.chatgpt)


def workspace_controls(app):
    """Composition root: application owners receive current concrete adapters."""
    from .adapters.mcp.course_material import CourseMaterials
    from .adapters.web.media.runtime import CalendarRuntime
    from .application.media import CalendarControls
    from .application.workspace import WorkspaceControls
    return WorkspaceControls(app,materials=CourseMaterials(app.store,getattr(app.mcp_host,'files',None)),
        media=CalendarControls(app,CalendarRuntime()))


def native_main(argv=None) -> int:
    import argparse
    from .adapters.models.development import DevelopmentModel
    from .application.native import NativeApplication
    from .core.records import Profile
    from .interfaces.native_server import serve, build_fingerprint
    from .interfaces.native_wire import BOUNDS, ID, PROTOCOL, SCHEMA_SHA256

    parser = argparse.ArgumentParser(prog='kejtu-next native-server')
    parser.add_argument('--state-dir', required=True, type=Path)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--initialize', action='store_true')
    selection.add_argument('--expected-state-id')
    notes = parser.add_mutually_exclusive_group()
    notes.add_argument('--synthetic-notes', action='store_true')
    notes.add_argument('--notes-write', action='store_true',
                       help='Enable note writes for new real-provider conversations only.')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--development-fake', action='store_true')
    mode.add_argument('--profile', type=Path)
    parser.add_argument('--profile-sha256')
    parser.add_argument('--standing-profile', type=Path)
    parser.add_argument('--standing-profile-sha256')
    parser.add_argument('--deepseek-key-stdin', action='store_true')  # Legacy explicit operator path.
    parser.add_argument('--model-key-stdin', action='store_true')
    parser.add_argument('--lifecycle-event-handle', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.profile_sha256 is not None and args.profile is None:
        parser.error('--profile-sha256 requires --profile')
    if args.notes_write and args.profile is None:
        parser.error('--notes-write requires --profile; fake tests use --synthetic-notes')
    if args.standing_profile_sha256 is not None and args.standing_profile is None:
        parser.error('--standing-profile-sha256 requires --standing-profile')
    store = None
    try:
        standing = native_standing_profile(args.standing_profile, args.standing_profile_sha256) if args.standing_profile else ''
        if args.profile is not None:
            profile = native_profile(args.profile, args.profile_sha256)
            if args.synthetic_notes:
                raise Failure('native_profile', 'Synthetic note mode requires the development fake.')
            key = None
            if args.deepseek_key_stdin and profile.provider != 'deepseek':
                raise Failure('native_profile', 'The legacy key flag requires DeepSeek.')
            if provider(profile.provider).requires_key and (args.model_key_stdin or args.deepseek_key_stdin):
                # Rust supplies only the bounded key and closes this anonymous
                # pipe. It is not a wire request and is never stored or logged.
                try:
                    key = sys.stdin.buffer.read(4097).decode('ascii')
                except (OSError, UnicodeError):
                    raise Failure('credential_required', 'Provider startup key unavailable.') from None
                validate_key(key)
            elif args.deepseek_key_stdin or args.model_key_stdin:
                raise Failure('native_profile', 'This provider does not accept a startup key.')
            # Startup does not depend on a console or a credential. Restoring
            # the desktop keyring happens after the native handshake is ready.
            model, provider_mode = ModelSession(DesktopKeyring(), key=key, key_provider=profile.provider), profile.provider
            del key
        else:
            if args.deepseek_key_stdin or args.model_key_stdin:
                raise Failure('native_profile', 'The development fake does not accept a key.')
            profile = Profile('r013-development-fake', 'http://127.0.0.1:1', 16384, 4096,
                              thinking_level=False, timeout_seconds=30, safety_margin=512,
                              keep_loaded=False, provider='ollama')
            model, provider_mode = DevelopmentModel(), 'development_fake'
        store = SqliteStore(args.state_dir, create=args.initialize,
                            expected_state_id=args.expected_state_id)
        from .adapters.skills.included import install_included
        install_included(store)
        saved_models = None
        if args.profile is not None:
            store.recall.embedding = LocalEmbedding()
            saved_models = SavedModels(args.state_dir, store.state_id, profile)
            profile, provider_mode = saved_models.profile, saved_models.active
            wire_chatgpt(model, saved_models)
        from .adapters.mcp.host import McpHost
        from .adapters.skills.packages import PackageImporter
        from .adapters.web.providers import WebBackends
        from .adapters.mcp.secrets import ConnectionSecrets, MAX_VALUE_BYTES, credential_value
        from .adapters.models.credentials import SecretItem
        # Connection keys use the same desktop keyring owner as provider keys, one
        # item per saved value, with the connection's own value rule.
        mcp_host = McpHost(args.state_dir, secrets=ConnectionSecrets(
            lambda identity, label: DesktopKeyring(spec=SecretItem('mcp-connection', identity, label),
                                                   validate=credential_value, encoding='utf-8', limit=MAX_VALUE_BYTES)))
        if args.initialize and args.profile is not None:
            from .adapters.skills.starter import install_starter
            install_starter(store)
            initialize_desktop_web(store, mcp_host)
            initialize_desktop_terminals(mcp_host)
            initialize_desktop_workspace(store)
            store.onboarding.offer()
            initialize_desktop_reflection(store, profile, notes_write=args.notes_write)
        application = NativeApplication(store, model, launch_profile=profile, provider_mode=provider_mode,
                                        protocol=PROTOCOL, schema_sha256=SCHEMA_SHA256,
                                        bounds=BOUNDS, id_pattern=ID.pattern,
                                        notes_write=args.notes_write or args.synthetic_notes,
                                        standing_profile=standing,
                                        hybrid_memory=True,
                                        task_tools=True, saved_models=saved_models, mcp_host=mcp_host,
                                        skill_importer=PackageImporter(),
                                        web_backends=WebBackends(Path(sys.prefix).parent,
                                            keyrings={name: DesktopKeyring(spec=spec) for name, spec in SEARCH_PROVIDERS.items()
                                                if spec.requires_key or spec.optional_key} if args.profile is not None else None),
                                        build_fingerprint=build_fingerprint(),workspace_factory=workspace_controls)
        return asyncio.run(serve(application, lifecycle_event_handle=args.lifecycle_event_handle))
    except Failure as error:
        # The private readiness pipe carries only a bounded failure code, never
        # an unverified session or raw storage error. Rust still reaps the child.
        print(json.dumps({'kind': 'failed', 'code': error.code}), flush=True)
        print(json.dumps({'status': 'failed', 'reason': error.code, 'detail': error.detail}), file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print(json.dumps({'status': 'failed', 'reason': 'native_startup',
                          'detail': 'Candidate native backend could not start.'}), file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()


def deepseek_key() -> str:
    # No argv/env/profile key paths and no getpass fallback that could echo a key.
    if not sys.stdin.isatty():
        raise Failure('credential_required', 'DeepSeek send requires a hidden interactive terminal key prompt.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            return getpass.getpass('DeepSeek session-only API key (hidden): ')
    except (getpass.GetPassWarning, EOFError, OSError):
        raise Failure('credential_required', 'A hidden terminal key prompt is unavailable; no request was sent.') from None


def provider_key(provider_id):
    """Explicit CLI-only hidden entry; the desktop uses its provider keyring."""
    if provider_id == 'deepseek':
        return deepseek_key()
    if not sys.stdin.isatty():
        raise Failure('credential_required', 'A terminal is required for hidden API key entry.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            return validate_key(getpass.getpass(provider(provider_id).label + ' API key: '))
    except (EOFError, OSError, KeyboardInterrupt, getpass.GetPassWarning):
        raise Failure('credential_required', 'API key entry cancelled or unavailable.') from None


def native_key_main(argv=None) -> int:
    """Hidden operator prompt; stdout belongs solely to the owning Rust pipe."""
    import argparse
    parser = argparse.ArgumentParser(prog='kejtu-next native-key')
    parser.add_argument('--profile', required=True, type=Path)
    parser.add_argument('--profile-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        profile = native_profile(args.profile, args.profile_sha256)
        if not provider(profile.provider).requires_key or sys.stdout.isatty():
            raise Failure('credential_required', 'A remote provider profile and private output pipe are required.')
        key = validate_key(provider_key(profile.provider))
        sys.stdout.write(key)
        sys.stdout.flush()
        return 0
    except (Failure, OSError, KeyboardInterrupt):
        # Never interpolate a credential or a raw exception into diagnostic IO.
        print('API key entry cancelled or unavailable; no request was sent.', file=sys.stderr)
        return 1


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.command is None:
        return 0
    store = None
    try:
        if args.command == 'import-conversations':
            from .adapters.storage.conversation_import import import_conversations
            from .adapters.storage.model_settings import SavedModels
            store = SqliteStore(args.state_dir, expected_state_id=args.expected_state_id)
            profiles = SavedModels(args.state_dir, store.state_id, native_profile(args.profile))
            if not store.db.execute('SELECT 1 FROM conversation_imports WHERE id=?',(args.source_id,)).fetchone():
                args.backup_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
                with closing(sqlite3.connect(args.backup_dir / 'before.sqlite3')) as backup:
                    store.db.backup(backup)
                os.chmod(args.backup_dir / 'before.sqlite3',0o600)
            result = import_conversations(store,args.snapshot,source_id=args.source_id,profile=profiles.profile)
            print(json.dumps(result))
            return 0
        if args.command in ('upgrade-store', 'restore-upgrade-backup'):
            from .adapters.storage.upgrade import upgrade_store, restore_upgrade_backup
            result = (upgrade_store(args.state_dir, args.expected_state_id, args.backup_dir)
                      if args.command == 'upgrade-store' else
                      restore_upgrade_backup(args.backup_dir, args.state_dir, args.expected_state_id))
            # Full table fingerprints live in private recovery receipts, not the
            # terminal. Neither operation initializes a provider or a turn owner.
            print(json.dumps({k: v for k, v in result.items() if k not in ('before', 'after')}))
            return 0
        store = SqliteStore(args.state_dir, create=args.command == 'init')
        store.recall.embedding = LocalEmbedding()
        model = Ollama()
        if args.command == 'send':
            profile = store.conversation(args.conversation).profile
            if provider(profile.provider).requires_key:
                model = DeepSeek(deepseek_key()) if profile.provider == 'deepseek' else ChatCompletions(profile.provider, provider_key(profile.provider))
        return asyncio.run(execute(args, TurnService(store, model, memory=store.memory, notes=store.notes)))
    except Failure as error:
        print(json.dumps({'status': 'failed', 'reason': error.code, 'detail': error.detail}), file=sys.stderr)
        return 1
    except (ValueError, TypeError, OSError, sqlite3.Error):
        print(json.dumps({'status': 'failed', 'reason': 'configuration_or_store_error',
            'detail': 'Invalid explicit configuration or inaccessible candidate store; no automatic retry was sent.'}), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(json.dumps({'status': 'cancelled', 'detail': 'Interrupted. Reopen the candidate store to inspect the durable turn outcome.'}), file=sys.stderr)
        return 130
    finally:
        if store is not None:
            store.close()


def package_tree_request(args):
    """One request of the disposable package-tree process; data only, nothing is executed.

    ``materialize`` downloads a pinned archive, unpacks it beside its destination,
    checks the unpacked tree against its pinned hash and publishes it with one
    rename; an existing tree is never replaced. ``verify`` hashes what is there.
    """
    import shutil
    import tarfile
    from .adapters import artifacts
    from .adapters.mcp.trees import artifact, clear_leftovers, missing_programs, tree_path
    root, commands = Path(args['root']), args.get('commands', [])
    if args['action'] == 'verify':
        path = tree_path(root, args['key'])
        if any(item.is_symlink() for item in (path, path.parent, root)):
            return dict(ok=True, state='different')
        if not path.exists():
            return dict(ok=True, state='absent')
        try:
            same = path.is_dir() and artifacts.tree_hash(path) == args['key'].partition('/')[0]
        except artifacts.ArtifactError:
            same = False
        if not same:
            return dict(ok=True, state='different')
        return dict(ok=True, state='incomplete' if missing_programs(path, commands) else 'ok')
    pin = artifact(args['artifact'])
    try:
        parent = artifacts.real_directory(root / pin['tree_sha256'])
        cache = artifacts.real_directory(root / '.downloads')
        # Leftovers of a run that was ended: only one tree is worked on at a time.
        # Only real folders of this layout are entered; a link is never followed.
        clear_leftovers(root)
        for stale in cache.glob('.download-*'):
            if stale.is_symlink() or stale.is_file():
                stale.unlink(missing_ok=True)
        destination = parent / pin['directory']
        if destination.is_symlink() or destination.exists():
            if destination.is_symlink() or not destination.is_dir() or artifacts.tree_hash(destination) != pin['tree_sha256']:
                return dict(ok=False, code='shelf_tree_differs',
                            error='A different folder already occupies this package tree; it was left untouched.')
            absent = missing_programs(destination, commands)
            if absent:
                # The pinned tree is in place; it simply does not hold what this package declares.
                return dict(ok=False, code='shelf_program_missing',
                            error=f'The package tree does not contain the declared program {absent[0]}; nothing was changed.')
            return dict(ok=True, state='present')
    except artifacts.ArtifactError as error:
        return dict(ok=False, code='shelf_tree', error=str(error))
    item = dict(pin, id=pin['directory'], archive=pin['sha256'] + '.tar.gz')
    try:
        archive = artifacts.download(cache, item, user_agent='Kejtu-shelf')
    except artifacts.ArtifactError as error:
        return dict(ok=False, code='shelf_download', error=str(error))
    except OSError:
        return dict(ok=False, code='shelf_download', error='The package archive could not be downloaded; nothing was installed.')

    def prepare(temporary):
        extracted = artifacts.extract_archive(archive, temporary, item)
        if pin.get('dependencies') == 'npm':
            # The one step that runs a program: npm, in its own sandbox profile. The
            # pinned tree is the result, so it is hashed after this step.
            npm_dependencies(extracted, args.get('registry'))
        elif pin.get('dependencies') == 'wheels':
            # Python wheels are downloaded at their pinned size and hash and copied in
            # as plain files. Nothing is built or run, so this step needs no sandbox.
            used = sum(path.stat().st_size for path in extracted.rglob('*') if path.is_file() and not path.is_symlink())
            wheel_dependencies(extracted, cache, pin['unpacked_bytes'] - used)
        if pin.get('dependencies'):
            size = sum(path.stat().st_size for path in extracted.rglob('*') if path.is_file() and not path.is_symlink())
            if size > pin['unpacked_bytes']:
                raise artifacts.ArtifactError('The installed package is larger than its pin allows; nothing was installed.')
        if artifacts.tree_hash(extracted) != pin['tree_sha256']:
            raise artifacts.ArtifactError('The unpacked package does not match its pinned contents; nothing was installed.')
        absent = missing_programs(extracted, commands)
        if absent:
            raise artifacts.ArtifactError(f'The package does not contain its declared program {absent[0]}; nothing was installed.')
        return extracted
    try:
        artifacts.publish(destination, prepare)
    except Failure as error:  # the dependency step refused, saying why; nothing was published
        return dict(ok=False, code=error.code, error=error.detail[:500])
    except (artifacts.ArtifactError, tarfile.TarError, EOFError) as error:
        message = str(error) if isinstance(error, artifacts.ArtifactError) else 'The package archive could not be unpacked; nothing was installed.'
        return dict(ok=False, code='shelf_tree', error=message)
    archive.unlink(missing_ok=True)  # verified and published; it can be downloaded again
    return dict(ok=True, state='published')


def npm_dependencies(tree, registry=None):
    """Install a package's npm dependencies from its lock file, inside the staging sandbox.

    npm runs with the read-only system, network, and this one folder; nothing of the
    user or of Kejtu is mounted. No package script runs (``--ignore-scripts``) and no
    command link is made (``--no-bin-links``), so the result holds only regular files
    and can be hashed strictly. ``registry`` is for tests; the public registry is used
    otherwise, and the lock's integrity values are what npm verifies either way.
    """
    import subprocess
    from .adapters.linux_sandbox import staging
    from .adapters.mcp.trees import NODE, NPM, REGISTRY, npm_lock
    npm_lock(tree)
    if not (Path(NPM).is_file() and Path(NODE).is_file()):
        raise Failure('shelf_dependency', 'This package needs Node.js and npm installed on this computer; nothing was installed.')
    try:
        command = staging(tree) + [
            '--setenv', 'npm_config_cache', '/tmp/npm-cache', '--setenv', 'npm_config_update_notifier', 'false',
            # One quick retry for a dropped connection; a download that does not match its lock is not worth waiting on.
            '--setenv', 'npm_config_fetch_retries', '1', '--setenv', 'npm_config_fetch_retry_mintimeout', '1000',
            '--setenv', 'npm_config_fetch_retry_maxtimeout', '5000',
            '--setenv', 'npm_config_registry', registry or REGISTRY, '--',
            NPM, 'ci', '--omit=dev', '--ignore-scripts', '--no-bin-links', '--no-audit', '--no-fund']
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=200)
    except subprocess.TimeoutExpired:
        raise Failure('shelf_dependency', 'Installing the package’s dependencies took too long and was stopped; nothing was installed.') from None
    except (OSError, ValueError, KeyError, StopIteration):
        raise Failure('shelf_dependency', 'The Linux tool sandbox is incomplete or changed; the package’s dependencies were not installed.') from None
    if result.returncode != 0:
        raise Failure('shelf_dependency', 'npm could not install the package’s dependencies exactly as locked; nothing was installed.')
    # npm's own copy of the lock inside node_modules varies with the npm version and is not part of the package.
    hidden = Path(tree) / 'node_modules/.package-lock.json'
    if hidden.is_file() and not hidden.is_symlink():
        hidden.unlink()


def wheel_dependencies(tree, cache, allowance):
    """Add a package's pinned Python wheels to its tree, as data.

    Every wheel is fetched through the shared pinned-download primitive (exact size
    and sha256, HTTPS only) and unpacked into ``site-packages`` as plain files. pip
    is not used: nothing is built, no command script is generated and nothing is
    imported or run. The downloaded wheels are discarded once unpacked.
    """
    from .adapters import artifacts
    from .adapters.mcp.trees import WHEEL_FOLDER, wheel_lock
    items = wheel_lock(tree)
    target = Path(tree) / WHEEL_FOLDER
    target.mkdir()
    remaining = allowance
    for item in items:
        try:
            wheel = artifacts.download(cache, item, user_agent='Kejtu-shelf')
        except artifacts.ArtifactError as error:
            raise Failure('shelf_dependency', 'A Python package could not be downloaded exactly as pinned; nothing was installed.') from error
        except OSError:
            raise Failure('shelf_dependency', 'A Python package could not be downloaded; nothing was installed.') from None
        try:
            remaining -= artifacts.extract_wheel(wheel, target, remaining)
        except artifacts.ArtifactError as error:
            raise Failure('shelf_dependency', str(error) + ' Nothing was installed.') from None
        finally:
            wheel.unlink(missing_ok=True)


def package_tree_main():
    """Private one-request composition for package trees; opens no archive and runs nothing it handles."""
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError('Package tree request exceeds its allowance.')
        result = package_tree_request(json.loads(raw))
    except Failure as error:
        result = dict(ok=False, code='shelf_tree', error=error.detail[:500])
    except Exception:
        result = dict(ok=False)  # unknown: the caller inspects the tree
    sys.stdout.write(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


def skill_import_main():
    """Private one-request package-reader composition; opens no archive."""
    import base64
    import time
    from .adapters.skills.packages import load_source
    from .adapters.web.skill_github import download
    try:
        raw=sys.stdin.buffer.read(65537)
        if len(raw)>65536:raise ValueError('Skill import request exceeds its allowance.')
        args=json.loads(raw);deadline=time.monotonic()+(155 if args.get('kind')=='catalogue' else 32)
        def check():
            if time.monotonic()>deadline:raise ValueError('Skill import reached its time limit.')
        if args['kind']=='catalogue':
            from .adapters.skills.catalogue import selected, download as download_package
            from .adapters.web.skill_github import fetch
            result = download_package(selected(args['identity'], args['digest']), check, get=fetch)
            for package in result['packages'].values():
                package['files'] = {p: base64.b64encode(b).decode('ascii') for p, b in package['files'].items()}
            result['ok'] = True
        elif args['kind']=='describe':
            # What a package folder holds, read as data; nothing is loaded from it.
            from .adapters.skills.bundles import read
            result=dict(ok=True,description=read(args['source'],check))
        else:
            package=load_source(args['kind'],args['source'],check,download,
                                expected_commit=args.get('expected_commit'),origin=args.get('origin'))
            package['files']={p:base64.b64encode(b).decode('ascii') for p,b in package['files'].items()}
            result=dict(ok=True,package=package)
    except Exception as error:
        result=dict(ok=False,error=str(error)[:500])
    sys.stdout.write(json.dumps(result,ensure_ascii=False,allow_nan=False))
    return 0
