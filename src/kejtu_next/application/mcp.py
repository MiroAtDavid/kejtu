"""MCP discovery and exact result paging inside the existing foreground loop."""
import asyncio

from ..core.mcp import TOOL, digest
from ..core.records import Failure
from ..core.reference import exact_page
from ..core.vision import ENCODING, result_projection, supported
import json

REQUEST_SECONDS = 60
FILE_SEARCH_SECONDS = 10


class McpSession:
    def __init__(self, host, store, conversation_id, turn_id, ledger, *, receipts=None, fresh=None, suspended=None):
        self.host, self.store, self.cid, self.tid, self.ledger = host, store, conversation_id, turn_id, ledger
        self.receipts = receipts or store.mcp
        self.fresh = fresh or (lambda: None)
        self.suspended = suspended or (lambda: False)
        self.settings = host.settings()
        self.connections, self.known, self.attempted = {}, {}, set()
        self.failed = set()
        self.authority_error = None
        self.monitor = asyncio.create_task(self._monitor())

    def check(self):
        self.fresh()
        self.check_permissions()

    def check_permissions(self):
        if self.authority_error:
            raise self.authority_error
        if self.host.connections.read()['revision'] != self.settings['revision'] or not self.store.conversation(self.cid).history_enabled:
            raise Failure('mcp_forbidden', 'MCP connections changed. This message cannot use their earlier permissions or results.')
        if any(row['requires_web'] for row in self.settings['servers'].values() if row['enabled']) and not self.store.web.settings()['enabled']:
            # Only already-open browser access is revoked. A disabled Web switch
            # must not prevent unrelated non-browser connectors from operating.
            if any(self.settings['servers'][name]['requires_web'] for name in self.connections):
                raise Failure('mcp_forbidden', 'Web access was disabled. The browser connection has closed.')

    async def _monitor(self):
        try:
            while True:
                await asyncio.sleep(.1)
                try:
                    # Foreground activity is expected during suspension, but
                    # changed connection/Web grants must still close access now.
                    if self.suspended():
                        self.check_permissions()
                    else:
                        self.check()
                except Failure as error:
                    self.authority_error = error
                    await self._close_connections()
                    return
        except asyncio.CancelledError:
            pass

    async def _close_connections(self):
        connections, self.connections = self.connections, {}
        await asyncio.gather(*(c.close() for c in connections.values()))

    async def close(self):
        self.monitor.cancel()
        await asyncio.gather(self.monitor, return_exceptions=True)
        await self._close_connections()

    def server(self, name, tool=None):
        self.check()
        row = self.settings['servers'].get(name)
        if row is None or not row['enabled'] or row['requires_web'] and not self.store.web.settings()['enabled']:
            raise Failure('mcp_forbidden', 'This connection is disabled, unavailable, or needs Web access.')
        if tool is not None and tool not in row['allowed_tools']:
            raise Failure('mcp_tool_forbidden', 'The user has not permitted this tool.')
        return row

    def preflight(self, name, args):
        if name == 'mcp_call':
            self.server(args['server'], args['tool'])
            # A batch is authored before any of its results are delivered. A
            # discovery earlier in that same batch cannot justify a guessed call.
            if (args['server'], args['tool']) not in self.known:
                raise Failure('mcp_discovery_required', 'Discover the tool in a separate mcp_tools request first. No call in this batch ran.')

    async def request(self, name, payload):
        row = self.server(name)
        if name in self.failed:
            raise Failure('mcp_connection_closed', 'This connection failed earlier in this message. No automatic reconnection was made.')
        if name not in self.connections:
            self.connections[name] = self.host.connection(name, row)
        connection = self.connections[name]
        # The reserved Files connection searches arbitrary user folder trees.
        # Bound that read separately from the turn; never imply an interrupted
        # walk established absence or silently exclude any shared subfolder.
        searching_files = name == 'kejtu-files' and payload.get('tool') == 'search_files'
        seconds = FILE_SEARCH_SECONDS if searching_files else REQUEST_SECONDS
        deadline = min(self.ledger.deadline, asyncio.get_running_loop().time()+seconds)
        if payload.get('action') == 'call':
            # The SDK and its enclosing transport must agree on this request's
            # lifetime, rather than passing the whole turn allowance downstream.
            payload = dict(payload, timeout=max(.001, deadline-asyncio.get_running_loop().time()))
        try:
            return await connection.request(payload, deadline=deadline, check=lambda: self.server(name))
        except (Failure, TimeoutError) as error:
            if isinstance(error, Failure) and error.code == 'mcp_not_executed':
                raise
            self.failed.add(name)
            await connection.close()
            if isinstance(error, TimeoutError):
                # Only the shared turn deadline is terminal. An individual MCP
                # timeout becomes tool feedback; its uncertain receipt survives
                # and the failed connection cannot reconnect/replay this turn.
                if asyncio.get_running_loop().time() >= self.ledger.deadline:
                    raise
                if searching_files:
                    raise Failure('mcp_search_timeout',
                        'The file search exceeded its 10-second request limit. Coverage is incomplete; '
                        'no search result was returned. This does not mean no matches. Choose a narrower '
                        'subfolder for a future search. This connection stays closed for this turn; '
                        'do not repeat the search. Explain the incomplete coverage using completed results.') from None
                raise Failure('mcp_request_timeout',
                    'The MCP request timed out. Its outcome is unknown; no retry was sent. '
                    'Completed receipts remain readable with mcp_read. This connection stays closed '
                    'for this turn. Report what completed and the unavailable result; do not repeat '
                    'the action or claim it succeeded.') from None
            raise

    def page(self, result_id, offset, maximum, budget):
        value = self.receipts.load(result_id, self.tid)
        self.server(value['server'], value['tool'])
        base = dict(domain='mcp', result_id=result_id, server=value['server'], tool=value['tool'],
            connection_revision=value['connection_revision'], schema_revision=value['schema_revision'],
            received_at=value['finished_at'], sha256=value['sha256'], encoding='UTF-8 JSON')
        if 'run_id' in value:base['source_run_id']=value['run_id']
        size = max(1, min(maximum, 32768))
        while True:
            page = exact_page(result_id, value['body'], offset, size).envelope()
            page.pop('reference_id')
            candidate = dict(base, **page)
            if budget.fits(candidate):
                return candidate
            if size <= 1:
                return dict(domain='mcp', status='not_delivered', reason='result_context_limit', result_id=result_id,
                    next_offset=offset, recovery='The complete result is saved. Continue with mcp_read; do not repeat the action.')
            size = max(1, size//2)

    def visual(self, result_id, budget, image_index=None):
        # Read from the exact saved receipt under the same live permission check.
        # The original JSON and its byte paging are independent of this view.
        value = self.receipts.load(result_id, self.tid)
        self.server(value['server'],value['tool'])
        self.ledger.spend_payload(value['bytes'])
        try:
            projected=result_projection(json.loads(value['body']),
                enabled=supported(self.ledger.profile.provider,self.ledger.profile.model),image_index=image_index)
        except ValueError as error:
            return dict(domain='mcp',status='not_delivered',reason='image_unavailable',result_id=result_id,recovery=str(error))
        if projected is None:
            return None
        content,images,total=projected
        candidate=dict(domain='mcp',encoding=ENCODING,result_id=result_id,
            server=value['server'],tool=value['tool'],sha256=value['sha256'],
            source_bytes=value['bytes'],connection_revision=value['connection_revision'],
            schema_revision=value['schema_revision'],image_count=total,
            content=content,visual_inputs=images,
            recovery='mcp_read with image_index views a saved image; offset/max_bytes reads exact original JSON.')
        if budget.fits(candidate):
            return candidate
        return dict(domain='mcp',status='not_delivered',reason='result_context_limit',result_id=result_id,
            image_count=total,recovery='The full image result is saved. Use mcp_read with image_index to select one; do not repeat the action.')

    async def operation(self, name, args, maximum, budget):
        self.check()
        try:
            if name == 'mcp_read':
                if 'image_index' in args:
                    return self.visual(args['result_id'],budget,args['image_index']) or dict(
                        domain='mcp',status='not_delivered',reason='image_unavailable',result_id=args['result_id'])
                return self.page(args['result_id'], args.get('offset', 0), min(maximum, args.get('max_bytes', 8192)), budget)
            if name == 'mcp_tools':
                server = args.get('server', '')
                if not server:
                    return dict(domain='mcp', servers=[row for row in self.host.manifest()['servers']
                        if not row['requires_web'] or self.store.web.settings()['enabled']])
                row = self.server(server)
                data = await self.request(server, {'action': 'tools'})
                terms = args.get('search', '').lower().split()
                tools = [t for t in data['tools'] if t['name'] in row['allowed_tools'] and
                    (not terms or any(term in (t['name']+' '+t.get('description', '')).lower() for term in terms))]
                offset = args.get('offset', 0)
                if offset > len(tools):
                    raise Failure('mcp_offset', 'Discovery offset exceeds this tool list.')
                result = dict(domain='mcp', server=server, tools=[], offset=offset, total_tools=len(tools), next_offset=None)
                for tool in tools[offset:offset+args.get('limit', 4)]:
                    if not TOOL.fullmatch(tool['name']):
                        raise Failure('mcp_schema', 'Server tool name exceeds the supported format.')
                    item = dict(tool, schema_revision=digest(tool))
                    candidate = dict(result, tools=result['tools']+[item])
                    end = offset+len(candidate['tools'])
                    candidate['next_offset'] = end if end < len(tools) else None
                    if not budget.fits(candidate):
                        break
                    result = candidate
                    self.known[(server, tool['name'])] = digest(tool)
                if not result['tools'] and offset < len(tools):
                    return dict(domain='mcp', status='not_delivered', reason='tool_schema_context_limit', next_offset=offset,
                        recovery='No complete tool definition fits. No tool was granted by this discovery.')
                return result
            server, tool = args['server'], args['tool']
            row = self.server(server, tool)
            schema = self.known.get((server, tool))
            if not schema:
                raise Failure('mcp_discovery_required', 'Discover this tool with mcp_tools in this message before calling it.')
            key = digest([server, tool, schema, args['arguments']])
            if key in self.attempted and tool not in row['repeatable_tools']:
                raise Failure('mcp_duplicate', 'This exact action was already attempted. Read its saved result or check its state; it was not repeated.')
            self.attempted.add(key)
            attempt = self.receipts.start(self.tid, server, tool, digest(row), schema, args['arguments'])
            try:
                data = await self.request(server, dict(action='call', tool=tool, arguments=args['arguments'],
                    schema_revision=schema, timeout=max(.1, self.ledger.deadline-asyncio.get_running_loop().time())))
            except BaseException as error:
                self.receipts.finish(attempt, rejected=isinstance(error, Failure) and error.code == 'mcp_not_executed')
                raise
            # Save completed originals before another await or permission check.
            # Revocation stops delivery, but cannot erase a completed effect.
            result_id = self.receipts.finish(attempt, data['result'])
            blocks=data['result'].get('content') if isinstance(data['result'],dict) else None
            if isinstance(blocks,list) and any(isinstance(b,dict) and b.get('type')=='image' for b in blocks):
                visual=self.visual(result_id,budget)
                if visual is not None: return visual
            return self.page(result_id, 0, min(maximum, 8192), budget)
        except Failure as error:
            if error.code in ('mcp_forbidden', 'mcp_configuration'):
                raise
            return dict(domain='mcp', status='not_delivered', reason=error.code, recovery=error.detail)
