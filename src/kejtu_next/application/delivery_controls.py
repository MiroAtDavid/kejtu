"""Authenticated renderer-neutral delivery controls, never a screen-paint claim."""
from ..core.records import Failure


class DeliveryControls:
    def __init__(self, application):
        self.app = application
        self.port = application.store.deliveries

    async def dispatch(self, operation, args):
        port, runner = self.port, self.app.background
        if operation in ('deliveries.pending', 'deliveries.list'):
            scope = 'delivery-pending' if operation == 'deliveries.pending' else 'delivery-list:' + args['conversation_id']
            after = self.app._uncursor(scope, args['cursor']) if args.get('cursor') else 0
            limit = self.app._page_size(args.get('page_size'))
            page = (port.pending(after=after, limit=min(limit, 5), eligible=runner._eligible)
                    if operation == 'deliveries.pending' else
                    port.page(args['conversation_id'], after=after, limit=limit))
            return dict(page, next_cursor=self.app._cursor(scope, page['next_cursor']) if page['next_cursor'] is not None else None)
        if operation == 'deliveries.read':
            return port.read(args['delivery_id'])
        if operation == 'deliveries.validate':
            return port.validate(args['delivery_id'], args['receipt'], eligible=runner._eligible)
        if operation == 'deliveries.lookup':
            return port.lookup(args['mutation_key'], args['intent_sha256'])
        if operation == 'deliveries.cancel':
            result = port.cancel(args['delivery_id'], mutation_key=args['mutation_key'], now=runner.wall())
            await runner.foreground()
            return result
        if operation == 'deliveries.acknowledge':
            result = port.acknowledge(args['delivery_id'], args['receipt'], channel=args['channel'],
                mutation_key=args['mutation_key'], now=runner.wall(), eligible=runner._eligible)
            # Previously prepared session summaries must not hide a newly
            # presented assistant message attached to an unchanged user turn.
            self.app.service.working_context.clear()
            if args['channel'] == 'dismissed':
                await runner.foreground()
            return result
        raise Failure('invalid_request', 'No such delivery control exists.')
