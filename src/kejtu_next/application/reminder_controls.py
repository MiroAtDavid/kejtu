"""Local reminder controls and renderer-neutral, receipt-bound quiet delivery."""
from ..core.records import Failure
from ..core.reminders import ReminderDefinition


class ReminderControls:
    def __init__(self, application):
        self.app = application
        self.reminders = application.store.reminders

    @staticmethod
    def _record(row):
        return dict(row, enabled=bool(row['enabled']), cancelled=bool(row['cancelled']))

    def _cursor(self, scope, value):
        return self.app._cursor(scope, value) if value is not None else None

    async def dispatch(self, operation, args):
        port = self.reminders
        chat, now = self.app.store.reminder_chat, self.app.background.wall()
        if operation == 'reminders.attention':
            scope = 'reminder-attention'
            after = self.app._uncursor(scope,args['cursor']) if args.get('cursor') else 0
            page = self.app.store.reminder_attention.page(now=now,after=after,limit=self.app._page_size(args.get('page_size')))
            return dict(page,next_cursor=self._cursor(scope,page['next_cursor']))
        if operation == 'reminders.attend':
            if args['action'] == 'lookup':
                return port.control_receipt(args['mutation_key'],expected_intent=args['intent_sha256'])
            await self.app.background.foreground()
            result = self.app.store.reminder_attention.change(args,now=now)
            self.app.service.working_context.clear()
            return result
        if operation == 'reminders.messages':
            scope = 'reminder-chat:' + args['conversation_id']
            before = self.app._uncursor(scope,args['cursor']) if args.get('cursor') else None
            page = chat.page(args['conversation_id'],now=now,before=before,limit=self.app._page_size(args.get('page_size')))
            return dict(page,next_cursor=self._cursor(scope,page['next_cursor']))
        if operation == 'reminders.inbox':
            scope = 'reminder-inbox'
            after = self.app._uncursor(scope,args['cursor']) if args.get('cursor') else 0
            page = chat.inbox(now=now,after=after,limit=self.app._page_size(args.get('page_size')))
            return dict(page,next_cursor=self._cursor(scope,page['next_cursor']))
        if operation == 'reminders.message':
            if args['action'] == 'lookup':
                return port.control_receipt(args['mutation_key'],expected_intent=args['intent_sha256'])
            if args['action'] == 'validate':
                return chat.validate(args['id'],args['receipt'],now)
            if args['action'] != 'present':
                await self.app.background.foreground()
            result = chat.change(args,now=now)
            self.app.service.working_context.clear()
            return result
        limit = self.app._page_size(args.get('page_size'))
        if operation == 'reminders.access':
            if args.get('action') == 'set':
                await self.app.background.foreground()
                self.app.service.working_context.clear()
            return port.model.access(args, self.app.background.wall())
        if operation == 'reminders.list':
            page = port.page(after=args.get('cursor') or '', limit=limit)
            return dict(page, items=[self._record(row) for row in page['items']])
        if operation == 'reminders.read':
            scope = 'reminder-history:' + args['reminder_id']
            before = self.app._uncursor(scope, args['cursor']) if args.get('cursor') else None
            read = port.read(args['reminder_id'], revision=args.get('revision'), before=before, limit=limit)
            return dict(read, reminder=self._record(read['reminder']), next_cursor=self._cursor(scope, read['next_cursor']))
        if operation == 'reminders.occurrences':
            scope = 'reminder-occurrences:' + args['reminder_id']
            after = self.app._uncursor(scope, args['cursor']) if args.get('cursor') else 0
            page = port.occurrences(args['reminder_id'], after=after, limit=limit)
            return dict(items=[dict(row, sequence=str(row['sequence'])) for row in page['items']],
                        next_cursor=self._cursor(scope, page['next_cursor']))
        if operation == 'reminders.pending':
            scope = 'reminder-pending'
            after = self.app._uncursor(scope, args['cursor']) if args.get('cursor') else 0
            # Return only complete, currently eligible notices. Retrieval is not
            # presentation; no occurrence is consumed here or by scheduler polling.
            page = port.pending(after=after, limit=min(limit, 5))
            return dict(items=[{key:value for key,value in row.items() if key != 'links'} for row in page['items']],
                        next_cursor=self._cursor(scope, page['next_cursor']))
        if operation == 'reminders.lookup':
            return port.control_receipt(args['mutation_key'], expected_intent=args['intent_sha256'])
        if operation == 'reminders.validate':
            result = port.validate_notice(args['occurrence_id'], args['receipt'])
            return {key:value for key,value in result.items() if key != 'links'}
        if operation in ('reminders.save', 'reminders.cancel', 'reminders.acknowledge'):
            if operation != 'reminders.acknowledge' or args['channel'] == 'dismissed':
                await self.app.background.foreground()
            # A renderer presentation receipt is not new user activity. It must
            # not reset idle timing or fabricate a foreground conversation turn.
            key, now = args['mutation_key'], self.app.background.wall()
            if operation == 'reminders.save':
                try:
                    value = ReminderDefinition.from_record(args['definition'])
                except (TypeError, ValueError) as error:
                    raise Failure('reminder_definition', str(error)) from None
                result = port.save(value, mutation_key=key, now=now,
                    reminder_id=args.get('reminder_id'), expected_revision=args.get('expected_revision'),
                    reactivate=args['reactivate'])
            elif operation == 'reminders.cancel':
                result = port.cancel(args['reminder_id'], expected_revision=args['expected_revision'], mutation_key=key, now=now)
            else:
                if args['channel'] not in ('dismissed', 'presented'):
                    raise Failure('reminder_receipt', 'Local acknowledgement must explicitly mean presented or dismissed.')
                result = port.acknowledge(args['occurrence_id'], args['receipt'], mutation_key=key, channel=args['channel'], now=now)
            receipt = port.control_receipt(key)
            if receipt['status'] == 'absent':
                raise Failure('reminder_outcome_unknown', 'Inspect the exact pending reminder outcome before another intent.')
            return dict(receipt, replayed=result['replayed'])
        raise Failure('invalid_request', 'No such reminder control exists.')
