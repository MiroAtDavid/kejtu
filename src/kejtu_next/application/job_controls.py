"""Local explicit job controls; no model tool, implicit timer or delivery owner."""
import uuid
import time

from ..core.jobs import JobDefinition
from ..core.budget import Ledger
from ..core.records import Failure


class JobControls:
    def __init__(self, application):
        self.app = application
        self.jobs = application.store.jobs
        self.runner = application.background
        self.observer = None
        self.sequence = 0

    def _session(self, args):
        if args['process_instance_id'] != self.app.process_instance_id:
            raise Failure('job_activity_session', 'Activity belongs to another native process; reconnect explicitly.')

    def _observation(self, args):
        self._session(args)
        if (self.observer is None or args['observer_id'] != self.observer
                or type(args['sequence']) is not int or not self.sequence < args['sequence'] <= 9007199254740991):
            raise Failure('job_activity_stale', 'Activity observer or sequence is stale; no lease was renewed.')

    def _page(self, args):
        return self.app._page_size(args.get('page_size'))

    @staticmethod
    def _job(row):
        return dict(row, enabled=bool(row['enabled']), cancelled=bool(row['cancelled']))

    def _receipt(self, result, key):
        receipt = self.jobs.control_receipt(key)
        if receipt['status'] == 'absent':
            raise Failure('job_outcome_unknown', 'Job change returned without its durable receipt; inspect before another intent.')
        return dict(receipt, replayed=result['replayed'])

    async def dispatch(self, operation, args):
        if operation.startswith('care.'):
            care = self.app.store.care
            if operation == 'care.status':
                return care.status(args['job_id'])
            if operation == 'care.changes':
                return care.changes(args['job_id'], before=int(args['cursor']) if args.get('cursor') else None, limit=self._page(args))
            group = care.group(args['group_id'])
            profile = self.jobs.definition(group['job_id']).profile
            ledger = Ledger(profile, time.monotonic(), with_history=True, with_memory=True)
            if operation == 'care.read':
                return care.body(args['group_id'],args['record_id'],args['revision_id'],args['offset'],args['maximum_bytes'],ledger)
            if operation == 'care.undo':
                await self.runner.foreground()
                self.app.service.working_context.clear()
                return care.undo(args['group_id'],args['mutation_key'],ledger,self.runner.wall())
            raise Failure('native_operation', 'Unknown notebook-care operation.')
        if operation == 'background.status':
            activity = self.runner.activity
            now = activity.clock()
            observed = activity.seen is not None and 0 <= now - activity.seen <= 15
            return dict(contract='app_running', activity_fresh=observed,
                scheduler_running=self.runner.loop is not None and not self.runner.loop.done(),
                active_run_id=self.runner.run_id, error_code=self.runner.error_code)
        if operation == 'background.attach':
            self._session(args)
            # Publish the new identity and revoke idle BEFORE joining transport.
            # Concurrent old observations cannot restore the replaced lease.
            self.observer, self.sequence = uuid.uuid4().hex, 0
            observer = self.observer
            self.runner.activity.reset()
            await self.app.titles.cancel()
            await self.runner.interrupt('job_activity_replaced')
            return dict(observer_id=observer, sequence=0, lease_seconds=15)
        if operation == 'background.observe':
            self._observation(args)
            cid = args['conversation_id']
            if cid is not None:
                self.app._id(cid)
                self.app.store.conversation(cid)
            values = {key: args[key] for key in ('conversation_id', 'visible', 'focused', 'draft', 'dialog', 'interacting')}
            # Validate all values before advancing the sequence; the activity
            # owner updates synchronously before observe joins cancellation.
            if any(type(values[key]) is not bool for key in ('visible', 'focused', 'draft', 'dialog', 'interacting')):
                raise Failure('job_activity', 'Activity flags must be explicit booleans.')
            self.sequence = args['sequence']
            await self.runner.observe(**values)
            if (cid != self.app.titles.cid or not values['visible'] or values['draft'] or values['dialog'] or values['interacting']):
                await self.app.titles.cancel()
            return dict(observer_id=args['observer_id'], sequence=args['sequence'], accepted=True)
        if operation == 'background.detach':
            self._observation(args)
            self.observer, self.sequence = None, args['sequence']
            self.runner.activity.reset()
            await self.app.titles.cancel()
            await self.runner.interrupt('job_activity_detached')
            return dict(observer_id=args['observer_id'], sequence=args['sequence'], accepted=True)
        if operation == 'jobs.list':
            page = self.jobs.page(after=args.get('cursor') or '', limit=self._page(args))
            return dict(page, items=[self._job(row) for row in page['items']])
        if operation == 'jobs.read':
            return self.jobs.inspect(args['job_id'], revision=args.get('revision'))
        if operation == 'jobs.lookup':
            return self.jobs.control_receipt(args['mutation_key'], expected_intent=args['intent_sha256'])
        if operation in ('jobs.save', 'jobs.cancel', 'jobs.cancel_run'):
            # User foreground intent takes precedence, even if later validation
            # refuses this change. Cancellation joins before storage can return.
            await self.runner.foreground()
            key, now = args['mutation_key'], self.runner.wall()
            if operation == 'jobs.save':
                try:
                    definition = JobDefinition.from_record(args['definition'])
                except (ValueError, TypeError) as error:
                    raise Failure('job_definition', str(error)) from None
                if definition.enabled and not self.runner.owns_provider(definition.profile.provider):
                    raise Failure('job_provider', 'Enable this job only in a session owning its saved provider.')
                result = self.jobs.save(definition, mutation_key=key, now=now,
                    job_id=args.get('job_id'), expected_revision=args.get('expected_revision'))
            elif operation == 'jobs.cancel':
                result = self.jobs.cancel(args['job_id'], expected_revision=args['expected_revision'],
                    mutation_key=key, now=now)
            else:
                result = self.jobs.cancel_run(args['run_id'], mutation_key=key, now=now)
            return self._receipt(result, key)
        if operation == 'jobs.runs':
            scope = 'job-runs:' + args['job_id']
            before = self.app._uncursor(scope, args['cursor']) if args.get('cursor') else None
            page = self.jobs.runs(args['job_id'], before=before, limit=self._page(args))
            return dict(items=[dict(row, sequence=str(row['sequence'])) for row in page['items']],
                next_cursor=self.app._cursor(scope, page['next_cursor']) if page['next_cursor'] is not None else None)
        if operation == 'jobs.run':
            row = self.jobs.run(args['run_id'])
            return dict(row, sequence=str(row['sequence']))
        if operation == 'jobs.events':
            scope = 'job-events:' + args['run_id']
            after = self.app._uncursor(scope, args['cursor']) if args.get('cursor') else 0
            page = self.jobs.events(args['run_id'], after=after, limit=self._page(args))
            return dict(items=[dict(row, sequence=str(row['sequence'])) for row in page['items']],
                next_cursor=self.app._cursor(scope, page['next_cursor']) if page['next_cursor'] is not None else None)
        if operation in ('jobs.result', 'jobs.event'):
            options = dict(offset=args.get('offset', 0), maximum=args.get('maximum_bytes', 16384))
            if operation == 'jobs.result':
                return self.jobs.result_page(args['run_id'], **options)
            raw = args['sequence']
            if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal() or len(raw) > 19:
                raise Failure('job_page', 'Use the exact event sequence.')
            return self.jobs.event_page(args['run_id'], int(raw), **options)
        raise Failure('invalid_request', 'No such background control exists.')
