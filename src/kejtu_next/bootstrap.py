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
    return store.jobs.save(definition, mutation_key=uuid4().hex, now=now.isoformat())


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
    from .adapters.web.media.runtime import MediaRuntime
    from .application.media import MediaControls
    from .application.workspace import WorkspaceControls
    return WorkspaceControls(app,materials=CourseMaterials(app.store,getattr(app.mcp_host,'files',None)),
        media=MediaControls(app,MediaRuntime(app.store)))


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
        saved_models = None
        if args.profile is not None:
            store.recall.embedding = LocalEmbedding()
            saved_models = SavedModels(args.state_dir, store.state_id, profile)
            profile, provider_mode = saved_models.profile, saved_models.active
            wire_chatgpt(model, saved_models)
        from .adapters.mcp.host import McpHost
        from .adapters.skills.packages import PackageImporter
        from .adapters.web.providers import WebBackends
        mcp_host = McpHost(args.state_dir)
        if args.initialize and args.profile is not None:
            from .adapters.skills.starter import install_starter
            install_starter(store)
            initialize_desktop_web(store, mcp_host)
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


def skill_import_main():
    """Private one-request package-reader composition; opens no archive."""
    import base64
    import time
    from .adapters.skills.packages import load_source
    from .adapters.web.skill_github import download
    try:
        raw=sys.stdin.buffer.read(65537)
        if len(raw)>65536:raise ValueError('Skill import request exceeds its allowance.')
        args=json.loads(raw);deadline=time.monotonic()+32
        def check():
            if time.monotonic()>deadline:raise ValueError('Skill import reached its time limit.')
        package=load_source(args['kind'],args['source'],check,download)
        package['files']={p:base64.b64encode(b).decode('ascii') for p,b in package['files'].items()}
        result=dict(ok=True,package=package)
    except Exception as error:
        result=dict(ok=False,error=str(error)[:500])
    sys.stdout.write(json.dumps(result,ensure_ascii=False,allow_nan=False))
    return 0
