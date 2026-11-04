"""DeepSeek binding to the shared Chat Completions implementation."""
import sys
from ...core.records import Failure
from .chat_completions import ChatCompletions, decode_reply, exchange as chat_exchange
from ._secrets import CredentialText, validate_key, credential_echo
from ._transport import worker as transport_worker


class DeepSeek(ChatCompletions):
    def __init__(self, api_key, *, spawn=None):
        super().__init__('deepseek', api_key, spawn=spawn)


def exchange(packet, *, opener=None, progress=None):
    if packet['profile'].get('provider', 'ollama') != 'deepseek':
        raise Failure('provider_configuration', 'DeepSeek requires its own saved provider profile.')
    return chat_exchange(packet, opener=opener, progress=progress)


def worker():
    return transport_worker(exchange)


if __name__ == '__main__':
    if sys.argv[1:] != ['--worker']:
        raise SystemExit('This module is an internal transport worker.')
    raise SystemExit(worker())
