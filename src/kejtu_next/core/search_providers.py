"""Search identity only; selection and routing stay outside the model's tools."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchProvider:
    id: str
    label: str
    endpoint: str
    requires_key: bool
    optional_key: bool = False


PROVIDERS = {
    'searxng': SearchProvider('searxng', 'SearXNG', 'http://127.0.0.1:8888', False),
    'brave': SearchProvider('brave', 'Brave Search', 'https://api.search.brave.com', True),
    'tavily': SearchProvider('tavily', 'Tavily', 'https://api.tavily.com', False, optional_key=True),
}


def provider_note(name):
    label = PROVIDERS[name].label if name in PROVIDERS else name
    note = f'Active search provider: {label}. Use web_search and web_read as usual; the app handles the provider.'
    if name == 'brave':
        from .brave_search import GUIDE
        return note + '\n' + GUIDE
    if name == 'tavily':
        from .tavily_search import GUIDE
        return note + '\n' + GUIDE
    return note
