"""Narrow characterized no-loss contract; metadata is not a tokenizer or setting.

R028 observed exact full-GUIDE fit and typed overflow refusal for this version /
model digest / operating profile. Other combinations remain unverified. Do not
extend this table from version labels or advertised model capacity alone.
"""
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request as HttpRequest, build_opener

from ...core.records import Failure, Profile
from ._transport import NoRedirect, worker as transport_worker

VERSION = '0.30.11'
MODEL = 'qwen3.5:9b'
DIGEST = '6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7'
METADATA_BYTES = 262144


def characterized(profile):
    return (profile.provider == 'ollama' and profile.model == MODEL
            and profile.context_limit == 16384 and profile.generation_limit == 4096
            and profile.thinking_level is True)


def _get(profile, path, timeout, opener):
    request = HttpRequest(profile.endpoint.rstrip('/') + path,
        headers={'Accept-Encoding': 'identity'}, method='GET')
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.headers.get('Content-Encoding', 'identity') != 'identity':
                raise Failure('invalid_response', 'Runtime metadata used unsupported compression.')
            raw = response.read(METADATA_BYTES + 1)
        if len(raw) > METADATA_BYTES:
            raise Failure('response_size', 'Runtime metadata exceeded its fixed transport bound.')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except HTTPError as error:
        error.close()
        raise Failure('provider_http', 'Runtime metadata request failed; no generation or retry was sent.') from None
    except (URLError, TimeoutError, OSError):
        raise Failure('provider_unavailable', 'Runtime metadata transport failed.') from None
    except (ValueError, UnicodeError, RecursionError):
        raise Failure('invalid_response', 'Runtime metadata was not a bounded JSON object.') from None


def verified(profile, timeout, *, opener=None):
    if not characterized(profile):
        return False
    opener = opener or build_opener(ProxyHandler({}), NoRedirect())
    if _get(profile, '/api/version', timeout, opener).get('version') != VERSION:
        return False
    models = _get(profile, '/api/tags', timeout, opener).get('models')
    if not isinstance(models, list):
        return False
    # Alias disagreement or duplicate names are ambiguous, not validation.
    selected = [entry for entry in models if isinstance(entry, dict)
                and (entry.get('name') == profile.model or entry.get('model') == profile.model)]
    return (len(selected) == 1 and selected[0].get('name') == profile.model
            and selected[0].get('model') == profile.model and selected[0].get('digest') == DIGEST)


def exchange(packet):
    profile = Profile(**packet['profile'])
    return json.dumps({'verified': verified(profile, packet['timeout'])}).encode()


if __name__ == '__main__':
    if sys.argv[1:] != ['--worker']:
        raise SystemExit('This module is an internal metadata worker.')
    raise SystemExit(transport_worker(exchange))
