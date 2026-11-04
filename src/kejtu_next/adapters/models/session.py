"""Registered-provider routing and isolated desktop-owned credentials."""
import asyncio
import json
from urllib.request import Request as HttpRequest, build_opener, ProxyHandler
from urllib.error import HTTPError

from ...core.records import Failure
from ...core.providers import PROVIDERS, provider
from .chat_completions import ChatCompletions
from .openai import OpenAIResponses
from .deepseek import DeepSeek
from ._secrets import validate_key, credential_echo
from .ollama import Ollama
from ._transport import NoRedirect


class ModelSession:
    providers = frozenset(PROVIDERS)
    supports_streaming = True

    def __init__(self, keyring, *, key=None, key_provider='deepseek', ollama=None,
                 deepseek_factory=DeepSeek, factories=None):
        # Factories are injectable for offline controls. Every actual request is
        # routed by its immutable conversation/job profile, never the UI default.
        self.factories = {'ollama': lambda: Ollama(), 'chat_completions': ChatCompletions,
                          'responses': OpenAIResponses}
        self.overrides = {'deepseek': deepseek_factory} | (factories or {})
        self.models = {'ollama': ollama or Ollama()}
        self.keyrings = {name: keyring.for_provider(name) for name, spec in PROVIDERS.items() if spec.requires_key}
        self.keys = {}
        self.key_statuses = {name: 'loading' if spec.requires_key else 'not_required' for name, spec in PROVIDERS.items()}
        self._lock = asyncio.Lock()
        self.chatgpt = None
        if key is not None:
            self._ring(key_provider)
            self._install(key_provider, validate_key(key), 'session')

    def _ring(self, provider_id):
        if provider_id not in self.keyrings:
            raise Failure('provider_configuration', 'Choose a registered provider that uses an API key.')
        return self.keyrings[provider_id]

    def _install(self, provider_id, key, status):
        spec = provider(provider_id)
        factory = self.overrides.get(provider_id)
        model = factory(key) if factory else self.factories[spec.transport](provider_id, key)
        self.models[provider_id], self.keys[provider_id] = model, key
        self.key_statuses[provider_id] = status

    async def restore(self, provider_id=None):
        names = tuple(self.keyrings) if provider_id is None else (provider_id,)
        async with self._lock:
            for name in names:
                ring = self._ring(name)
                # An explicitly supplied startup key owns this session. Restore
                # other suppliers independently without replacing that key.
                if provider_id is None and self.key_statuses[name] == 'session':
                    continue
                try:
                    key = await ring.read()
                    if key is not None:
                        self._install(name, validate_key(key), 'saved')
                    elif self.key_statuses[name] != 'session':
                        self.models.pop(name, None)
                        self.keys.pop(name, None)
                        self.key_statuses[name] = 'missing'
                except Failure:
                    self.key_statuses[name] = 'unavailable'

    async def save_key(self, key, provider_id='deepseek'):
        ring = self._ring(provider_id)
        key = validate_key(key)
        async with self._lock:
            await ring.save(key)
            self._install(provider_id, key, 'saved')

    async def remove_key(self, provider_id='deepseek'):
        ring = self._ring(provider_id)
        async with self._lock:
            await ring.remove()
            self.models.pop(provider_id, None)
            self.keys.pop(provider_id, None)
            self.key_statuses[provider_id] = 'missing'

    def require(self, profile):
        spec = provider(profile.provider)
        if spec.subscription:
            if self.chatgpt is None:
                raise Failure('credential_required', 'Continue with ChatGPT in Settings → Models before sending.')
            self.chatgpt.require()
        model = self.models.get(spec.id)
        if model is None:
            raise Failure('credential_required', f'Add your {spec.label} API key in Settings → Models before sending.')
        return model

    async def context_capability(self, profile, deadline):
        spec = provider(profile.provider)
        if spec.rejects_context_overflow:
            # This is a documented rejection contract, not a token estimate or
            # proof of fit. Creating a conversation makes no provider request.
            return True
        return await self.require(profile).context_capability(profile, deadline)

    async def complete(self, request):
        return await self.require(request.profile).complete(request)

    async def stream(self, request, progress):
        model = self.require(request.profile)
        stream = getattr(model, 'stream', None)
        return await stream(request, progress) if callable(stream) else await model.complete(request)

    async def check(self, profile):
        spec = provider(profile.provider)
        if spec.subscription:
            self.require(profile)
            choices, more = await self.chatgpt.catalog()
            return dict(models=[row['model'] for row in choices], choices=choices,
                        model_available=any(row['model'] == profile.model for row in choices), has_more=more)
        if spec.requires_key:
            self.require(profile)
        return await asyncio.to_thread(self._check, profile, self.keys.get(spec.id))

    @staticmethod
    def _check(profile, key):
        # Explicit Check only: bounded inventory request, no generation, loading,
        # embedding, ambient proxies or redirected credentials.
        endpoint = profile.endpoint.rstrip('/') + ('/models' if key is not None else '/api/tags')
        headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity'}
        if key is not None:
            headers['Authorization'] = 'Bearer ' + key
        try:
            opener = build_opener(ProxyHandler({}), NoRedirect())
            with opener.open(HttpRequest(endpoint, headers=headers), timeout=5) as response:
                raw = response.read(131073)
                if len(raw) > 131072:
                    raise ValueError
            data = json.loads(raw)
            rows = data['data' if key is not None else 'models']
            if not isinstance(rows, list):
                raise ValueError
            models = sorted(set(row['id' if key is not None else 'name'] for row in rows))
            if any(not isinstance(name, str) or not name or len(name.encode()) > 1024
                   or key is not None and credential_echo(name, key) for name in models):
                raise ValueError
            # The bounded inventory includes legacy, audio and image models.
            # Keep the selected/known supported models visible even when their
            # names sort beyond the first page; never invent account access.
            preferred = dict.fromkeys((profile.model, *(name for name, _ in provider(profile.provider).models)))
            displayed = [name for name in preferred if name in models]
            displayed.extend(name for name in models if name not in preferred)
            return {'models': displayed[:50], 'model_available': profile.model in models,
                    'has_more': len(models) > 50}
        except HTTPError as error:
            code = 'provider_auth' if error.code in (401, 403) else 'provider_unavailable'
            error.close()
            raise Failure(code, 'The model connection check failed. Check the address and API key.') from None
        except (OSError, ValueError, KeyError, TypeError):
            raise Failure('provider_unavailable', 'The model list could not be checked. No generation was requested.') from None
