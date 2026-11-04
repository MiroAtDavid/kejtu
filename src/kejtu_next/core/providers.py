"""Finite provider capabilities; shared by profiles, routing and generated UI contracts.

No IO or secrets live here. Saved profiles remain the only budget/settings owner.
New suppliers register here and implement a transport dialect when needed.
"""
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ThinkingPolicy:
    dialect: str
    options: tuple[bool | str, ...]
    default: bool | str

    def accepts(self, value):
        return any(type(value) is type(option) and value == option for option in self.options)


LOCAL = ThinkingPolicy('ollama', (False, True, 'low', 'medium', 'high'), False)
DEEPSEEK = ThinkingPolicy('deepseek', (False, True, 'low', 'medium', 'high', 'max'), 'medium')
EFFORT = ThinkingPolicy('effort', ('low', 'high', 'max'), 'high')
ALWAYS = ThinkingPolicy('always', (True,), True)
PRESERVED = ThinkingPolicy('preserved', (True, False), True)
OPENAI = ThinkingPolicy('responses', ('low', 'medium', 'high', 'xhigh', 'max'), 'medium')
OPENAI_LUNA = ThinkingPolicy('responses', (False, 'low', 'medium', 'high', 'xhigh', 'max'), 'medium')


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    endpoint: str
    transport: str
    default_model: str
    default_context: int
    default_generation: int
    thinking: ThinkingPolicy
    models: tuple[tuple[str, ThinkingPolicy], ...] = ()
    preserve_reasoning: bool = False
    rejects_context_overflow: bool = False
    reasoning_state_format: str = 'completed_reasoning_v1'
    subscription: bool = False

    @property
    def requires_key(self):
        return self.transport != 'ollama' and not self.subscription

    @property
    def unique_tool_ids(self):
        return self.transport in ('chat_completions', 'responses')

    def thinking_for(self, model):
        if not self.models:
            return self.thinking
        for name, policy in self.models:
            if model == name:
                return policy
        raise ValueError(f'Choose a supported {self.label} model; its thinking contract must be known.')

    def validate(self, profile):
        if self.transport != 'ollama':
            # Credentials are scoped to this official origin; changing a model
            # address must never send another supplier's key to a new host.
            if profile.endpoint not in (self.endpoint, self.endpoint + '/'):
                raise ValueError(f'{self.label} requires its official HTTPS endpoint')
            if profile.keep_loaded:
                raise ValueError(f'{self.label} does not support local model residency')
        else:
            if not isinstance(profile.endpoint, str):
                raise ValueError('Local address must be an explicit loopback HTTP URL')
            url = urlsplit(profile.endpoint)
            if (url.scheme != 'http' or url.hostname not in {'127.0.0.1', '::1'}
                    or url.username is not None or url.password is not None or url.query or url.fragment
                    or url.path not in {'', '/'} or url.port is None):
                raise ValueError('Ollama requires a loopback HTTP endpoint with an explicit port')
        if not self.thinking_for(profile.model).accepts(profile.thinking_level):
            raise ValueError(f'Unsupported thinking setting for {self.label} / {profile.model}')

    def settings_descriptor(self):
        def options(policy):
            return ['on' if v is True else 'off' if v is False else v for v in policy.options]
        return dict(id=self.id, label=self.label, credential_required=self.requires_key,
                    editable_endpoint=self.transport == 'ollama', keep_loaded=self.transport == 'ollama',
                    thinking_options=options(self.thinking),
                    models=[dict(model=name, thinking_options=options(policy)) for name, policy in self.models])


PROVIDERS = MappingProxyType({p.id: p for p in (
    Provider('ollama', 'Local', 'http://127.0.0.1:11434', 'ollama',
             'qwen3.5:4b-q4_K_M', 32768, 8192, LOCAL),
    Provider('deepseek', 'DeepSeek', 'https://api.deepseek.com', 'chat_completions',
             'deepseek-flash', 264000, 16384, DEEPSEEK,
             preserve_reasoning=True, rejects_context_overflow=True,
             reasoning_state_format='deepseek_completed_reasoning_v1'),
    Provider('kimi', 'Kimi', 'https://api.moonshot.ai/v1', 'chat_completions',
             'kimi-k3.1-pro', 65536, 16384, EFFORT,
             models=(('kimi-k3.1-pro', EFFORT), ('kimi-k3.1-pro-highspeed', EFFORT),
                     ('kimi-k3', EFFORT), ('kimi-k2.7-code', ALWAYS),
                     ('kimi-k2.7-code-highspeed', ALWAYS), ('kimi-k2.6', PRESERVED)),
             preserve_reasoning=True, rejects_context_overflow=True),
    Provider('openai', 'OpenAI', 'https://api.openai.com/v1', 'responses',
             'gpt-6.1-sol', 65536, 16384, OPENAI,
             models=(('gpt-6.1-sol', OPENAI), ('gpt-6-astra', OPENAI), ('gpt-6-luna', OPENAI_LUNA)),
             preserve_reasoning=True, rejects_context_overflow=True,
             reasoning_state_format='openai_responses_v1'),
    Provider('chatgpt', 'ChatGPT plan', 'https://api.openai.com/v1', 'responses',
             'gpt-6.1-sol', 240000, 16384, OPENAI,
             models=(('gpt-6.1-sol', OPENAI), ('gpt-6-astra', OPENAI), ('gpt-6-luna', OPENAI_LUNA)),
             preserve_reasoning=True, rejects_context_overflow=True,
             reasoning_state_format='chatgpt_responses_v1', subscription=True),
)})


def provider(identifier: str) -> Provider:
    try:
        return PROVIDERS[identifier]
    except (KeyError, TypeError):
        raise ValueError('Unsupported model provider') from None
