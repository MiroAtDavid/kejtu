"""Streaming credential guard; pure key validation is shared by adapters."""
import re
from ...core.records import Failure
from ...core.credentials import validate_key, credential_echo, _LITERAL_JSON_ESCAPE


class CredentialText:
    """Hold only potential credential/escape prefixes across arbitrary chunks.

    Match the complete-response owner's one-layer literal-escape policy. A
    credential split at a chunk boundary never reaches provisional display.
    Ordinary text is emitted immediately, with no fixed-size trailing buffer.
    """
    def __init__(self, key):
        self.key, self.pending = key, ''

    def feed(self, delta):
        text = self.pending + delta
        if credential_echo(text, self.key):
            raise Failure('credential_exposure', 'Provider response contained a credential; no answer was saved.')
        unfinished = re.search(r'\\(?:u[0-9a-fA-F]{0,3})?$', text)
        stable = text[:unfinished.start()] if unfinished else text
        normalized, offsets = [], []
        position = 0
        for match in _LITERAL_JSON_ESCAPE.finditer(stable):
            for index in range(position, match.start()):
                normalized.append(text[index]); offsets.append(index)
            hexadecimal, short = match.groups()
            code = int(hexadecimal, 16) if hexadecimal is not None else None
            value = short if short is not None else chr(code) if 33 <= code <= 126 else match.group(0)
            normalized.extend(value); offsets.extend([match.start()] * len(value))
            position = match.end()
        for index in range(position, len(stable)):
            normalized.append(text[index]); offsets.append(index)
        safe = len(stable)
        for source, positions in ((stable, range(len(stable))), (''.join(normalized), offsets)):
            for size in range(min(len(self.key) - 1, len(source)), 0, -1):
                if self.key.startswith(source[-size:]):
                    safe = min(safe, positions[len(source) - size])
                    break
        self.pending = text[safe:]
        return text[:safe]

    def finish(self):
        text, self.pending = self.pending, ''
        return text


