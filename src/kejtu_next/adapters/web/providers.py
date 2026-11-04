"""Explicit provider factories. Future APIs implement the same search port."""
import json
import asyncio
from pathlib import Path
from ...core.credentials import validate_key
from ...core.records import Failure, ToolCall
from ...core.web import arguments as web_arguments
from .brave import Brave
from .tavily import Tavily
from . import worker
from .searxng import WebSearchController, SEARCH_POOLS
from .reader import WebReader
from .network import public_url


class Searxng:
    name = 'searxng'
    categories = ('general', *SEARCH_POOLS)

    def __init__(self):
        self.controller = WebSearchController()

    def prepare(self, arguments):
        web_arguments(ToolCall('validation', 'web_search', json.dumps(arguments)), self.name)

    async def search(self, arguments, *, deadline):
        self.prepare(arguments)
        category = arguments.get('category','general')
        args = dict(action='search',query=arguments['query'],site=arguments.get('site'),
            category='general',pool=None if category=='general' else category,
            language=arguments.get('language','all'),time_range=arguments.get('time_range'),
            page=arguments.get('page',1),offset=0,limit=20)
        result = await worker.run(lambda seconds,cancelled:self.controller.run(args,timeout=seconds,cancelled=cancelled),deadline=deadline)
        data = json.loads(result['output'])
        # Adapter-specific diagnostics stay labelled; callers need no knowledge
        # of SearXNG's engine selectors or result wire format.
        return dict(provider=self.name, **data)


class BrowserReader:
    def __init__(self, runtime_root):
        self.reader = WebReader(runtime_root)

    async def read(self, url, *, deadline):
        public_url(url)
        return await worker.run(lambda seconds,cancelled:self.reader.fetch(url,seconds,cancelled),deadline=deadline)


class WebBackends:
    def __init__(self, runtime_root, *, providers=None, reader=None, keyring=None, keyrings=None):
        self.keyrings = dict(keyrings or {})
        if keyring is not None:
            self.keyrings['brave'] = keyring
        self.keys = {'brave': None, 'tavily': None}
        self.key_statuses = {name: 'loading' if self.keyrings.get(name) is not None else
                             'missing' if name == 'tavily' else 'unavailable' for name in self.keys}
        self.lock = asyncio.Lock()
        self.providers = providers if providers is not None else {
            'searxng': Searxng, 'brave': lambda: Brave(self.keys['brave']),
            'tavily': lambda: Tavily(self.keys['tavily'],
                credentials_available=self.key_statuses['tavily'] in ('saved', 'missing'))}
        self.reader_factory = reader if reader is not None else lambda:BrowserReader(Path(runtime_root))

    # Existing private integrations address Brave's credential directly. These
    # aliases share the same per-provider owner rather than duplicating state.
    keyring = property(lambda self: self.keyrings.get('brave'), lambda self, value: self.keyrings.__setitem__('brave', value))
    key = property(lambda self: self.keys['brave'], lambda self, value: self.keys.__setitem__('brave', value))
    key_status = property(lambda self: self.key_statuses['brave'])

    async def credential(self, action, value=None, *, provider='brave'):
        if provider not in self.keys or action not in ('save', 'remove', 'reload'):
            raise Failure('web_arguments', 'Unsupported search credential operation.')
        async with self.lock:
            # A failed restore/save must not leave a stale key silently active.
            self.keys[provider] = None
            keyring = self.keyrings.get(provider)
            if keyring is None:
                self.key_statuses[provider] = 'unavailable'
                raise Failure('credential_store', 'The desktop keyring is unavailable in this session.')
            try:
                if action == 'save':
                    await keyring.save(validate_key(value))
                elif action == 'remove':
                    await keyring.remove()
                self.keys[provider] = await keyring.read()
                if self.keys[provider] is not None: validate_key(self.keys[provider])
                self.key_statuses[provider] = 'saved' if self.keys[provider] else 'missing'
            except BaseException:
                self.keys[provider] = None
                self.key_statuses[provider] = 'unavailable'
                raise

    async def restore(self):
        for name, keyring in self.keyrings.items():
            if keyring is None: continue
            try:
                await self.credential('reload', provider=name)
            except Failure:
                pass  # Opening the app never requires working search credentials.

    def open(self, name):
        if name not in self.providers:
            raise Failure('web_provider_unavailable','The selected search provider is not installed.')
        return self.providers[name](), self.reader_factory()
