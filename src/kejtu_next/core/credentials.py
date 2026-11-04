"""Bounded bearer-key validation and response echo protection, shared by suppliers."""
import re
from .records import Failure


_LITERAL_JSON_ESCAPE = re.compile(r'\\(?:u([0-9a-fA-F]{4})|(["\\/]))')


def credential_echo(text: str, key: str) -> bool:
    if key in text:
        return True

    def unescape(match):
        hexadecimal, short = match.groups()
        if short is not None:
            return short
        codepoint = int(hexadecimal, 16)
        # Keys accept printable ASCII only. Leave other Unicode, controls and
        # surrogates untouched; this is comparison, not a general text decoder.
        return chr(codepoint) if 33 <= codepoint <= 126 else match.group(0)

    # One fixed-width, non-recursive pass over already byte-bounded text. Never
    # rescan replacement output, combine fields or rewrite the returned text.
    return '\\' in text and key in _LITERAL_JSON_ESCAPE.sub(unescape, text)


def validate_key(api_key: str) -> str:
    if (not isinstance(api_key, str) or not 1 <= len(api_key) <= 4096
            or any(ord(c) < 33 or ord(c) > 126 for c in api_key)):
        raise Failure('credential_required', 'A valid API key is required; no request was sent.')
    return api_key
