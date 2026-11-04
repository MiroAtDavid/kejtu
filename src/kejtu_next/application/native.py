"""Native-session application facade over the existing turn and store owners."""
from __future__ import annotations

from ..core.providers import PROVIDERS
from dataclasses import asdict
import asyncio
import base64
import hashlib
import json
import re

from ..core.execution import HistoryPolicy
from ..core.prompt import system_prompt
from ..core.reply_format import GUIDE as REPLY_GUIDE
from ..core.records import Failure, Profile, message_byte_limit
from .turn import TurnService
from .turn_failure import record_failure
from .background import BackgroundRunner
from .job_controls import JobControls
from .reminder_controls import ReminderControls
from .delivery_controls import DeliveryControls
from .model_controls import ModelControls
from ..core.search_providers import PROVIDERS as SEARCH_PROVIDERS
from .conversation_titles import TitleRunner
from .streaming import ForegroundStream
from .skills import SkillControls


def canonical_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()


class NativeApplication:
    def __init__(self, store, model, *, launch_profile: Profile, history=True,
                 memory=True, notes_write=False, protocol: str, schema_sha256: str,
                 bounds: dict, id_pattern: str, build_fingerprint: str = '0' * 64,
                 provider_mode: str = 'development_fake', standing_profile: str = '', hybrid_memory=False, task_tools=False,
                 saved_models=None, web_backends=None, mcp_host=None, skill_importer=None,workspace_factory=None):
        if (provider_mode not in ('development_fake', *PROVIDERS)
                or launch_profile.provider != ('ollama' if provider_mode == 'development_fake' else provider_mode)):
            raise Failure('native_profile', 'Unsupported native provider selection.')
        self.store = store
        self.web_backends = web_backends
        self.mcp_host = mcp_host
        self.skill_controls = SkillControls(store,skill_importer)
        self.hybrid_memory = hybrid_memory
        self.maintenance_loop = None
        self.maintenance_active = None
        self.model_controls = ModelControls(self, saved_models, model) if saved_models is not None else None
        self.model_restore = None
        self.web_restore = None
        self.streams = ForegroundStream()
        self.research_changes = asyncio.Lock()
        self.service = TurnService(store, model, memory=store.memory, notes=store.notes, tasks=store.personal_tasks,
                                   reminders=store.reminders.model, people=store.people, progress=self.streams, web_backends=web_backends,
                                   reply_guide=REPLY_GUIDE, mcp_host=mcp_host)
        self.background = BackgroundRunner(store.jobs, model, provider=launch_profile.provider,
            reminders=store.reminders, memory=store.memory, notes=store.notes, research=store.research, web_backends=web_backends, mcp_host=mcp_host, before_run=self._before_background,
            blocked=lambda: self.research_changes.locked() or self.model_controls is not None and self.model_controls.lock.locked())
        self.titles = TitleRunner(self, model)
        self.job_controls = JobControls(self)
        self.reminder_controls = ReminderControls(self)
        self.delivery_controls = DeliveryControls(self)
        self.task_tools = task_tools
        self.profile = launch_profile
        self.provider_mode = provider_mode
        if not isinstance(standing_profile, str) or len(standing_profile.encode('utf-8')) > 16384:
            raise Failure('native_standing_profile', 'Standing profile exceeds the supported text bound.')
        # Freeze at launch, then embed exact text in each NEW saved conversation.
        # Reopening never replaces an existing conversation's saved instructions.
        self.standing_profile = standing_profile
        self.history, self.memory, self.notes_write = history, memory, notes_write
        self.protocol, self.schema_sha256, self.bounds = protocol, schema_sha256, bounds
        self.id_pattern = re.compile(id_pattern)
        self.dispatching = 0
        self.tasks: dict[str, asyncio.Task] = {}
        self.build_fingerprint = build_fingerprint
        self.workspace_controls = workspace_factory(self) if workspace_factory is not None else None
        self.service.workspace = self.workspace_controls

    def creation_intent(self):
        value = {'profile': asdict(self.profile), 'history': self.history,
                 'memory': self.memory, 'notes_write': self.notes_write, 'hybrid_memory':self.hybrid_memory and self.memory}
        if self.task_tools:
            value['tasks_enabled'] = self.memory
        if self.standing_profile:
            value['standing_profile_sha256'] = hashlib.sha256(self.standing_profile.encode('utf-8')).hexdigest()
        # Bind the selected prompt in every mode: a pending create from an older
        # default must not resolve to different instructions after an update.
        value['system_prompt_sha256'] = hashlib.sha256(self._system_prompt().encode('utf-8')).hexdigest()
        return canonical_digest(value)

    @property
    def ingress_context_limit(self):
        # Transport only: include older conversations' immutable profiles when
        # the new-conversation default is smaller. Each turn still uses its own
        # saved Profile and the existing Ledger for admission and budgeting.
        return max(self.profile.context_limit, self.store.maximum_profile_context())

    def start_maintenance(self):
        if self.workspace_controls is not None:self.workspace_controls.media.start()
        if self.web_backends is not None:
            self.web_restore = asyncio.create_task(self.web_backends.restore(), name="restore-search-credential")
        if self.model_controls is None:
            self.background.start()
        else:
            async def restore_models():
                await self.model_controls.session.restore()
                self.background.start()
            self.model_restore = asyncio.create_task(restore_models(), name='restore-model-credential')
        if not self.hybrid_memory or self.maintenance_loop is not None:
            return
        async def run():
            while True:
                await asyncio.sleep(5)
                if self.tasks or self.store.active_turn_id() is not None or self.background.active is not None or self.titles.active is not None:
                    continue
                self.maintenance_active = asyncio.create_task(self.store.maintain_memory())
                try:
                    await asyncio.gather(self.maintenance_active,return_exceptions=True)
                finally:
                    self.maintenance_active = None
        self.maintenance_loop = asyncio.create_task(run(),name='incremental-memory-index')

    async def _before_background(self):
        await self.titles.cancel()
        await self._stop_maintenance_batch()

    async def _stop_maintenance_batch(self):
        if self.maintenance_active is not None:
            self.maintenance_active.cancel()
            await asyncio.gather(self.maintenance_active,return_exceptions=True)

    def _system_prompt(self):
        return system_prompt(self.standing_profile, with_history=self.history,
            history_policy=HistoryPolicy.STANDARD if self.history else None,
            with_memory=self.memory, with_notes=self.notes_write)

    def _cursor(self, scope: str, value) -> str:
        payload = json.dumps([scope, value], separators=(',', ':')).encode()
        tag = hashlib.sha256(self.store.state_id.encode() + payload).digest()[:12]
        return base64.urlsafe_b64encode(payload + tag).decode().rstrip('=')

    def _uncursor(self, scope: str, token: str):
        if not isinstance(token, str) or len(token.encode()) > self.bounds['cursor_bytes']:
            raise Failure('invalid_cursor', 'Cursor is invalid for this page.')
        try:
            raw = base64.urlsafe_b64decode(token + '=' * (-len(token) % 4))
            payload, tag = raw[:-12], raw[-12:]
            if hashlib.sha256(self.store.state_id.encode() + payload).digest()[:12] != tag:
                raise ValueError
            saved_scope, value = json.loads(payload)
            if saved_scope != scope:
                raise ValueError
            return value
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            raise Failure('invalid_cursor', 'Cursor is invalid for this page.') from None

    def _id(self, value, *, nullable=False):
        if nullable and value is None:
            return None
        if not isinstance(value, str) or self.id_pattern.fullmatch(value) is None:
            raise Failure('invalid_request', 'A required identity is invalid.')
        return value

    def _page_size(self, value):
        value = self.bounds['default_page_items'] if value is None else value
        if type(value) is not int or not 1 <= value <= self.bounds['maximum_page_items']:
            raise Failure('invalid_request', 'Page size is outside the native bound.')
        return value

    def _turn_value(self, row: dict) -> dict:
        bodies = []
        for role, key in (('user', 'user_id'), ('answer', 'answer_id'), ('partial', 'partial_id')):
            if row.get(key):
                bodies.append({'role': role, 'body_id': row[key],
                    'format': 'text' if role == 'user' else 'markdown',
                    'state': 'partial' if role == 'partial' else 'ready'})
        flags = [r[0] for r in self.store.db.execute(
            'SELECT excluded FROM imported_messages WHERE artifact_id IN (?,?,?)',
            (row.get('user_id'), row.get('answer_id'), row.get('partial_id')))]
        return {key: row.get(key) for key in ('id', 'conversation_id', 'sequence', 'status', 'reason',
            'detail', 'calls', 'input_estimate', 'input_tokens', 'output_tokens', 'finish_reason',
            'created_at', 'finished_at')} | {'created_at': row.get('created_at') or None, 'imported': self.store.imported_conversation(row['conversation_id']), 'sequence': str(row['sequence']), 'bodies': bodies,
                'recall_excluded': bool(flags and all(flags)) or self.store.db.execute('SELECT 1 FROM exchange_exclusions WHERE turn_id=?', (row['id'],)).fetchone() is not None,
                'partial_recall_excluded': bool(flags and any(flags) and not all(flags)),
                'context_usage': self.store.context_usage(row['id']) if row['status'] != 'running' else None}

    def _drop_task(self, turn_id, task):
        self.tasks.pop(turn_id, None)
        try:
            try:
                task.result()
                if self.store.turn_metadata(turn_id)['status'] == 'running':
                    raise RuntimeError('Execution returned without a terminal outcome.')
            except asyncio.CancelledError:
                self._finish_unstarted_cancellation(turn_id)
            except Exception as error:
                record_failure(self.store, turn_id, error, 'native_task')
                # The execution owner can fail before entering its handler, or
                # during cleanup. A durable terminal outcome always wins; never
                # replay work or replace an answer already committed.
                if self.store.turn_metadata(turn_id)['status'] == 'running':
                    self.store.finish(turn_id, 'failed',
                        reason=error.code if isinstance(error, Failure) else 'internal_error',
                        detail=error.detail if isinstance(error, Failure) else
                            'Local reply execution stopped unexpectedly. No automatic retry was sent.')
            self.streams.terminal(turn_id, self.store.turn_metadata(turn_id)['status'])
        except Exception as error:
            # Storage/display faults must remain diagnosable even when the
            # archive itself cannot accept the terminal update.
            record_failure(self.store, turn_id, error, 'native_finalization')
            self.streams.terminal(turn_id, 'failed')

    async def dispatch(self, operation: str, args: dict) -> dict:
        if operation == 'conversation.title':
            return await self._dispatch(operation, args)
        self.dispatching += 1
        try:
            return await self._dispatch(operation, args)
        finally:
            self.dispatching -= 1

    async def _dispatch(self, operation: str, args: dict) -> dict:
        if operation == 'workspace':
            if self.workspace_controls is None:raise Failure('workspace_unavailable','Workspace adapters are not configured.')
            return await self.workspace_controls.dispatch(args['action'],args['payload_json'])
        if operation == 'conversation.title':
            return await self.titles.read(self._id(args['conversation_id']))
        if operation.startswith('research.'):
            action=operation.split('.',1)[1]
            if action in ('pause','resume','seen','disposition','review','delete'):
                async with self.research_changes:
                    change = dict(action=action, **args)
                    if action == 'delete':
                        previous = self.store.research.delete_check(change)
                        if previous is not None:
                            return previous
                    if self.tasks or self.store.active_turn_id() is not None:
                        raise Failure('native_busy','Finish the current reply before changing an assignment.')
                    if action == 'delete':
                        self.background.activity.touch()
                        if self.background.run_id is not None and self.store.jobs.run(self.background.run_id)['job_id'] == args['job_id']:
                            await self.background.interrupt('job_deleted', cancel_deliveries=False)
                    elif action in ('pause','resume'):
                        await self.background.interrupt('job_definition_changed')
                    return self.store.research.local(change)
            return self.store.research.local(dict(action=action,**args))
        if operation.startswith('skills.'):
            library=self.store.skills
            if operation=='skills.status':return library.status(enabled=args.get('enabled_only',False),offset=args.get('offset',0),limit=20)
            if operation=='skills.read':
                from ..core.reference import exact_page
                if args['path']=='':
                    body=json.dumps(library.manifest(args['name'],args['revision']),ensure_ascii=False,sort_keys=True,indent=2)
                else:
                    _,raw=library.file(args['name'],args['revision'],args['path'])
                    try:body=raw.decode('utf-8')
                    except UnicodeError:raise Failure('skill_binary','This installed resource is binary; this viewer displays UTF-8 text.') from None
                page=exact_page(args['revision'],body,args['offset'],min(args['max_bytes'],32768)).envelope()
                if page['reason']:raise Failure('skill_page',page['reason'])
                return dict({k:page[k] for k in ('content','offset','end','total_bytes','next_offset','sha256')},complete=page['status']=='complete')
            busy=self.tasks or self.store.active_turn_id() is not None or self.skill_controls.lock.locked()
            if busy and not (operation=='skills.enable' and not args['enabled']):
                raise Failure('native_busy','Wait for current work before importing or enabling skills. Disable remains available.')
            if operation=='skills.import':return await self.skill_controls.install(args)
            if operation=='skills.enable':return library.enable(args['name'],args['revision'],args['enabled'],args['expected_revision'])
        if operation.startswith('files.'):
            if self.mcp_host is None:
                raise Failure('files_unavailable','File access is unavailable in this session.')
            if operation=='files.status':return self.mcp_host.files.status()
            if operation=='files.disable':
                value=self.mcp_host.files.disable(args['expected_revision'])
                await self.background.interrupt('job_files_changed')
                return value
            if self.tasks or self.store.active_turn_id() is not None or self.mcp_host.lock.locked():
                raise Failure('native_busy','Finish or stop the current reply before changing shared folders. Disable remains available.')
            await self.background.interrupt('job_files_changed')
            if operation=='files.save':return self.mcp_host.files.save(args)
        if operation.startswith('mcp.'):
            if self.mcp_host is None:
                raise Failure('mcp_unavailable', 'MCP connections are not available in this session.')
            if operation == 'mcp.status':
                return self.mcp_host.connections.status()
            if operation == 'mcp.results':
                return dict(items=self.store.mcp.list(args['turn_id']))
            if operation == 'mcp.result':
                from ..core.reference import exact_page
                value = self.store.mcp.load(args['result_id'], args['turn_id'])
                page = exact_page(args['result_id'], value['body'], args['offset'], args['maximum_bytes']).envelope()
                return dict(content=page['content'], next_offset=page['next_offset'], total_bytes=page['total_bytes'])
            busy = self.tasks or self.store.active_turn_id() is not None or self.mcp_host.lock.locked()
            if busy and not (operation == 'mcp.update' and args['action'] == 'disable'):
                raise Failure('busy', 'Finish or stop current work before configuring or testing a connection.')
            if operation == 'mcp.inspect':
                return await self.mcp_host.inspect(args['server'], args['offset'])
            if operation == 'mcp.browser':
                return self.mcp_host.add_browser(args['expected_revision'])
            if operation == 'mcp.setup':
                return self.mcp_host.setup(args['server'], args['expected_revision'], use_shared_folders=args['use_shared_folders'])
            if operation == 'mcp.import':
                return self.mcp_host.import_config(args['configuration'], args['expected_revision'])
            if operation == 'mcp.update':
                return self.mcp_host.update(args)
        if operation in ('web.status', 'web.key'):
            if self.web_backends is None:
                raise Failure('web_provider_unavailable', 'Web adapters are unavailable in this session.')
            if operation == 'web.key':
                if (self.tasks or self.store.active_turn_id() is not None
                        or self.web_backends.lock.locked()):
                    raise Failure('busy', 'Wait for current work to finish before changing the search key.')
                await self.web_backends.credential(args['action'], args.get('api_key'), provider=args.get('provider', 'brave'))
            return dict(providers=[dict(id=spec.id,label=spec.label,requires_key=spec.requires_key,
                key_status=self.web_backends.key_statuses.get(spec.id, 'unavailable') if spec.requires_key or spec.optional_key else 'not_required',
                usage=self.store.web.usage.status(spec.id)) for spec in SEARCH_PROVIDERS.values()
                if spec.id in self.web_backends.providers])
        if operation == 'turn.submit' and self.research_changes.locked():
            raise Failure('native_busy', 'Wait for the background task change to finish.')
        if operation == 'turn.submit' and self.skill_controls.lock.locked():
            raise Failure('native_busy','Wait for the current skill import before sending.')
        if operation == 'turn.submit' and self.mcp_host is not None and self.mcp_host.lock.locked():
            raise Failure('busy', 'Wait for the connection test to finish.')
        if (self.web_backends is not None and self.web_backends.lock.locked()
                and (operation == 'turn.submit' or operation == 'web.settings' and args.get('action') == 'set')):
            raise Failure('busy', 'Wait for the search key operation to finish.')
        if operation.startswith('models.'):
            if self.model_controls is None:
                raise Failure('models_unavailable', 'Model settings are unavailable in this test session.')
            return await self.model_controls.dispatch(operation, args)
        if operation == 'execution.read':
            return self.store.execution.settings()
        if operation == 'execution.save':
            if self.tasks or self.store.active_turn_id() is not None:
                raise Failure('busy', 'Finish or stop the current reply before changing execution settings.')
            return self.store.execution.save(**args)
        if operation == 'access.read':
            return self.store.access_settings.read()
        if operation == 'access.save':
            if self.tasks or self.store.active_turn_id() is not None:
                raise Failure('busy', 'Finish or stop the current reply before changing access.')
            async with self.research_changes:
                await self.titles.cancel()
                await self.background.foreground()
                try:
                    return self.store.access_settings.save(**args)
                finally:
                    # An uncertain durability acknowledgement may follow a
                    # committed rename. No previous working view may survive it.
                    self.service.working_context.clear()
        if operation == 'onboarding.read':
            return self.store.onboarding.read()
        if operation == 'onboarding.change':
            if self.tasks or self.store.active_turn_id() is not None:
                raise Failure('busy', 'Finish the current reply before changing getting-started guidance.')
            try:
                return self.store.onboarding.change(**args)
            finally:
                self.service.working_context.clear()
        if operation == 'preferences.read':
            return self.store.memory.preference_settings.read()
        if operation == 'preferences.save':
            if self.tasks or self.store.active_turn_id() is not None:
                raise Failure('busy', 'Finish or stop the current reply before changing preference recall.')
            try:
                return self.store.memory.preference_settings.save(**args)
            finally:
                # A rename can succeed before its durability acknowledgement
                # fails. Never retain a pre-change working view in that case.
                self.service.working_context.clear()
        if (self.model_controls is not None and self.model_controls.lock.locked()
                and operation in ('conversation.create', 'turn.submit')):
            raise Failure('busy', 'Wait for the model settings operation to finish.')
        if operation.startswith('deliveries.'):
            return await self.delivery_controls.dispatch(operation, args)
        if operation.startswith('reminders.'):
            return await self.reminder_controls.dispatch(operation, args)
        if operation.startswith(('jobs.', 'background.', 'care.')):
            return await self.job_controls.dispatch(operation, args)
        if (operation in ('conversation.create', 'turn.submit', 'note.remove', 'exchange.action')
                or operation == 'tasks' and args.get('action') in ('save', 'transition')
                or operation == 'task.access' and args.get('action') == 'set'
                or operation == 'people.access' and args.get('action') == 'set'
                or operation == 'web.settings' and args.get('action') == 'set'):
            # Foreground intent wins even when subsequent validation refuses it.
            # Inspection/polling is not interaction and must not reset idle time.
            await self.titles.cancel()
            await self.background.foreground()
        if operation == 'hello':
            return {'protocol': self.protocol, 'schema_sha256': self.schema_sha256,
                'build_fingerprint': self.build_fingerprint,
                'process_instance_id': self.process_instance_id, 'state_id': self.store.state_id,
                'store_schema_version': self.store.schema_version, 'provider_mode': self.provider_mode,
                'profile_id': canonical_digest(asdict(self.profile)), 'profile': asdict(self.profile),
                'ingress_context_limit': self.ingress_context_limit,
                'maximum_message_bytes': message_byte_limit(self.profile.context_limit),
                'capabilities': {'history': self.history, 'memory': self.memory,
                                 'notes_write': self.notes_write,
                                 'streaming': getattr(self.service.model, 'supports_streaming', False) is True},
                'bounds': self.bounds, 'active_turn_id': self.store.active_turn_id(),
                'creation_intent_sha256': self.creation_intent()}
        if operation == 'conversation.create':
            key = self._id(args['mutation_key'])
            intent = self.creation_intent()
            conversation_id = await self.service.create_conversation(self.profile,
                self._system_prompt(),
                history_enabled=self.history, history_policy=HistoryPolicy.STANDARD if self.history else None,
                tasks_enabled=self.task_tools and self.memory,
                memory_enabled=self.memory, notes_enabled=self.notes_write, hybrid_memory=self.hybrid_memory and self.memory,
                mutation_key=key, intent_sha256=intent)
            config_id = self.store.configuration_id(conversation_id)
            return {'conversation_id': conversation_id, 'configuration_id': config_id,
                    'mutation_key': key, 'intent_sha256': intent}
        if operation == 'conversations.list':
            cursor = args.get('cursor')
            anchor = self._uncursor('conversations', cursor) if cursor is not None else None
            size = self._page_size(args.get('page_size'))
            rows = self.store.conversation_summaries(before_created=anchor[0] if anchor else None,
                                                      before_id=anchor[1] if anchor else None, limit=size)
            items = [{'conversation_id': row['id'], 'created_at': row['created_at'],
                      'configuration_id': row['config_id'], 'imported': self.store.imported_conversation(row['id']), 'title': self.store.titles.read(row['id'])['title']} for row in rows]
            next_cursor = self._cursor('conversations', [rows[-1]['created_at'], rows[-1]['id']]) if len(rows) == size else None
            return {'items': items, 'next_cursor': next_cursor}
        if operation == 'turns.page':
            conversation_id = self._id(args['conversation_id'])
            if self.titles.cid != conversation_id:
                await self.titles.cancel()
            before = args.get('before')
            sequence = self._uncursor('turns:' + conversation_id, before) if before is not None else None
            rows = self.store.turn_metadata_page(conversation_id, before_sequence=sequence,
                                                  limit=self._page_size(args.get('page_size')))
            items = [self._turn_value(row) for row in rows]
            # A cursor promises retained earlier entries, including at an exact
            # page boundary or after deletion. Probe metadata only; never bodies.
            has_earlier = bool(rows and self.store.turn_metadata_page(conversation_id,
                before_sequence=rows[0]['sequence'], limit=1))
            next_cursor = self._cursor('turns:' + conversation_id, rows[0]['sequence']) if has_earlier else None
            return {'items': items, 'next_cursor': next_cursor,
                    'maximum_message_bytes': self.store.conversation_message_byte_limit(conversation_id)}
        if operation == 'turn.submit':
            await self._stop_maintenance_batch()
            key = self._id(args['mutation_key'])
            conversation_id = self._id(args['conversation_id'])
            # The launch selection authorizes this adapter/profile only. Saved
            # conversations remain readable but never acquire new settings by
            # reopening the same state with another launch selection.
            self.store.require_writable_conversation(conversation_id)
            conversation_profile = self.store.conversation(conversation_id).profile
            if self.model_controls is None and conversation_profile != self.profile:
                raise Failure('native_profile',
                    'Conversation profile differs from this launch. Reopen with its saved profile or create a new conversation.')
            if self.model_controls is not None:
                # Older conversations keep their exact saved profile. Routing
                # by that profile permits reopening them after a default change.
                self.model_controls.session.require(conversation_profile)
            previous = self._id(args['expected_previous_turn_id'], nullable=True)
            text = args['text']
            if not isinstance(text, str):
                raise Failure('invalid_request', 'Turn text must be exact Unicode text.')
            intent_data={'conversation_id':conversation_id,'expected_previous_turn_id':previous,'text':text}
            selected_skill=args.get('skill')
            if selected_skill:intent_data['skill']=selected_skill
            intent=canonical_digest(intent_data)
            admitted = self.service.admit(conversation_id, text, mutation_key=key,
                                          intent_sha256=intent, expected_previous_turn_id=previous, selected_skill=selected_skill)
            if admitted.status == 'running' and admitted.id not in self.tasks:
                self.streams.start(admitted.id, conversation_id)
                task = asyncio.create_task(self.service.run(admitted.id), name='native-turn-' + admitted.id)
                self.tasks[admitted.id] = task
                task.add_done_callback(lambda done, ident=admitted.id: self._drop_task(ident, done))
            return {'turn_id': admitted.id, 'conversation_id': conversation_id,
                    'status': admitted.status, 'mutation_key': key, 'intent_sha256': intent}
        if operation == 'mutation.lookup':
            kind = args['kind']
            if kind == 'exchange.action':
                return self.store.exchanges.lookup(self._id(args['mutation_key']))
            if kind == 'note.remove':
                conversation_id = self._id(args.get('conversation_id'))
                result = self.store.note_removal_status(conversation_id, self._id(args['mutation_key']), include_intent=True)
                return self._removal_receipt(result, result) if result['status'] == 'removed' else {'status': 'absent'}
            if kind not in ('conversation.create', 'turn.submit'):
                raise Failure('invalid_request', 'Mutation lookup kind is invalid.')
            return self.store.lookup_mutation(kind, self._id(args['mutation_key']))
        if operation == 'exchange.action':
            await self._stop_maintenance_batch()
            self.service.working_context.clear()
            if self.store.active_turn_id() is None and args['action'] in ('exclude', 'delete'):
                self.streams = ForegroundStream()
                self.service.progress = self.streams
            return self.store.exchanges.apply(args)
        if operation == 'notes.page':
            return self.store.notebook_page(self._id(args['conversation_id']), args.get('cursor'), self._page_size(args.get('page_size')), args.get('collection','current'))
        if operation == 'tasks':
            if args['action'] in ('save','transition'):
                self.service.working_context.clear()
            return self.store.personal_tasks.execute(args)
        if operation == 'task.lookup':
            return self.store.personal_tasks.lookup(self._id(args['mutation_key']))
        if operation == 'web.settings':
            if args['action']=='set':
                if self.web_backends is None or args['provider'] not in self.web_backends.providers:
                    raise Failure('web_provider_unavailable','That search provider is not installed.')
                self.service.working_context.clear()
            return self.store.web.settings_operation(args)
        if operation == 'web.sources':
            return dict(items=self.store.web.sources(self._id(args['turn_id'])))
        if operation == 'web.source':
            return self.store.web.inspect(self._id(args['source_id']),args['offset'],args['maximum_bytes'])
        if operation == 'people.access':
            result = self.store.people.access(args)
            if args['action'] == 'set':
                self.service.working_context.clear()
            return result
        if operation == 'task.access':
            result = self.store.personal_tasks.access(args)
            if args['action'] == 'set':
                self.service.working_context.clear()
            return result
        if operation == 'note.read':
            return self.store.notebook_read(self._id(args['conversation_id']), self._id(args['note_id']),
                self._id(args['revision_id']), args['view_token'], args['offset'], args['maximum_bytes'])
        if operation == 'note.removal.preview':
            conversation_id, note_id = self._id(args['conversation_id']), self._id(args['note_id'])
            scope = 'note-removal:' + conversation_id + ':' + note_id
            previous = self._uncursor(scope, args['cursor']) if args.get('cursor') else None
            if previous is not None and (not isinstance(previous, list) or len(previous) != 2
                    or not isinstance(previous[0], str) or re.fullmatch('[0-9a-f]{64}', previous[0]) is None
                    or type(previous[1]) is not int):
                raise Failure('invalid_cursor', 'Removal preview cursor is invalid.')
            result = self.store.note_removal_preview_page(conversation_id, note_id,
                start=previous[1] if previous else 0, limit=self._page_size(args.get('page_size')),
                expected_token=previous[0] if previous else None)
            offset = result.pop('next_offset')
            result['next_cursor'] = self._cursor(scope, [result['preview_token'], offset]) if offset is not None else None
            return result
        if operation == 'note.remove':
            # Drop derived RAM context before removal, including a refused or
            # interrupted attempt. Removal ownership/transactions are unchanged.
            self.service.working_context.clear()
            result = self.store.remove_note(self._id(args['conversation_id']), self._id(args['note_id']),
                args['preview_token'], self._id(args['mutation_key']))
            return self._removal_receipt(result, args)
        if operation == 'turn.status':
            return self._turn_value(self.store.turn_metadata(self._id(args['turn_id'])))
        if operation == 'turn.stream':
            turn_id = self._id(args['turn_id'])
            epoch = self._id(args['epoch'], nullable=True)
            maximum, offset = args['maximum_bytes'], args['offset']
            if (type(maximum) is not int or not 4 <= maximum <= self.bounds['maximum_body_bytes']
                    or type(offset) is not int or offset < 0):
                raise Failure('invalid_request', 'Stream page bounds are invalid.')
            return self.streams.page(self.store, self.store.turn_metadata(turn_id), epoch, offset, maximum)
        if operation == 'turn.inspect':
            turn_id = self._id(args['turn_id'])
            items = self.store.inspect_turn(turn_id)
            start = self._uncursor('inspect:' + turn_id, args['cursor']) if args.get('cursor') else 0
            if type(start) is not int or not 0 <= start <= len(items):
                raise Failure('invalid_cursor', 'Inspection cursor is invalid.')
            size = self._page_size(args.get('page_size'))
            page = list(items[start:start + size])
            return {'items': page, 'next_cursor': self._cursor('inspect:' + turn_id, start + size)
                    if start + size < len(items) else None}
        if operation == 'body.image':
            return self.store.image_read(self._id(args['conversation_id']), self._id(args['turn_id']),
                self._id(args['body_id']), args['index'], args['source_sha256'])
        if operation in ('body.read', 'body.view'):
            maximum = args['maximum_bytes']
            if type(maximum) is not int or not 1 <= maximum <= self.bounds['maximum_body_bytes']:
                raise Failure('invalid_request', 'Visible body page size is outside the native bound.')
            reader = self.store.body_view if operation == 'body.view' else self.store.body_page
            return reader(self._id(args['conversation_id']), self._id(args['turn_id']),
                                        self._id(args['body_id']), args['offset'], maximum)
        if operation == 'turn.cancel':
            turn_id = self._id(args['turn_id'])
            row = self.store.turn_metadata(turn_id)
            if row['status'] != 'running':
                return {'status': 'already_terminal', 'turn_status': row['status']}
            task = self.tasks.get(turn_id)
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self._finish_unstarted_cancellation(turn_id)
            return {'status': 'requested', 'turn_status': self.store.turn_metadata(turn_id)['status']}
        raise Failure('unsupported_operation', 'Native operation is not supported.')

    def _removal_receipt(self, result, intent):
        return {'status': 'accepted', 'kind': 'note.remove', 'mutation_key': result['operation_id'],
            'conversation_id': intent['conversation_id'], 'note_id': result['note_id'],
            'removed_at': result['removed_at'], 'intent_sha256': canonical_digest(
                {key: intent[key] for key in ('conversation_id', 'note_id', 'preview_token')})}

    async def close(self):
        if self.model_controls is not None:
            await self.model_controls.chatgpt.cancel()
        if self.workspace_controls is not None:await self.workspace_controls.media.close()
        await self.skill_controls.close()
        await self.titles.close()
        if self.web_restore is not None:
            self.web_restore.cancel()
            await asyncio.gather(self.web_restore, return_exceptions=True)
        if self.model_restore is not None:
            self.model_restore.cancel()
            await asyncio.gather(self.model_restore, return_exceptions=True)
        await self.background.close()
        if self.maintenance_loop is not None:
            self.maintenance_loop.cancel()
            await asyncio.gather(self.maintenance_loop,return_exceptions=True)
            self.maintenance_loop = None
        await self._stop_maintenance_batch()
        tasks = list(self.tasks.items())
        for _, task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        for turn_id, _ in tasks:
            self._finish_unstarted_cancellation(turn_id)
        self.service.working_context.clear()

    def _finish_unstarted_cancellation(self, turn_id):
        # Task.cancel before the first coroutine instruction cannot reach the
        # turn service's finally/except path. The admission owner closes that
        # gap after joining the task; an existing terminal outcome always wins.
        if self.store.turn_metadata(turn_id)['status'] == 'running':
            self.store.finish(turn_id, 'cancelled', reason='cancelled',
                detail='Owned execution stopped locally; no automatic retry was sent.')

    process_instance_id = ''
