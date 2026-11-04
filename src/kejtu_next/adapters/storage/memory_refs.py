"""Bounded memory-handle spellings over one unchanged scoped authenticator.

R1 is a wire-format version, not a task/revision number. Its 49/65-byte packet
contains kind/mode, two/three 128-bit identities and the existing 128-bit tag.
No lookup table, alternate key or new authority is introduced. Legacy cursors
and read handles keep their canonical JSON spelling and verification contract.
"""
import base64
import hashlib
import hmac
import json
import re

from ...core.records import Failure
from .history import encoded


def legacy_token(snapshot, key, value):
    body = base64.urlsafe_b64encode(encoded(value).encode()).decode().rstrip('=')
    scope = [snapshot.bank_id, snapshot.conversation_id, snapshot.epoch, snapshot.grant_epoch]
    # Keep existing chat handles byte-identical; independent jobs bind handles
    # to a single live run, so another review cannot borrow its read authority.
    if snapshot.job_run_id is not None:
        scope.append(snapshot.job_run_id)
    scope = encoded(scope)
    digest = hmac.new(key, (scope + '.' + body).encode(), hashlib.sha256).hexdigest()[:32]
    return body + '.' + digest


def reference(snapshot, key, value):
    """Issue only typed references; invalid stored identities fail explicitly."""
    if (not isinstance(value, list) or len(value) != 5
            or value[0] not in ('m', 'p', 's') or value[1] not in ('current', 'historical')):
        raise Failure('memory_integrity', 'Memory reference identity is invalid.')
    kind, mode, record, revision, source = value
    identities = [record, revision] + ([source] if kind == 's' else [])
    if ((kind != 's' and source is not None)
            or any(not isinstance(v, str) or not re.fullmatch('[0-9a-f]{32}', v) for v in identities)):
        raise Failure('memory_integrity', 'Memory reference identity is invalid.')
    flags = ('m', 'p', 's').index(kind) + (4 if mode == 'historical' else 0)
    tag = bytes.fromhex(legacy_token(snapshot, key, value).rsplit('.', 1)[1])
    packet = bytes([flags]) + b''.join(bytes.fromhex(v) for v in identities) + tag
    return 'R1.' + base64.urlsafe_b64encode(packet).decode().rstrip('=')


def decode(snapshot, key, token):
    try:
        if not isinstance(token, str):
            raise ValueError('shape')
        if token.startswith('R1.'):
            body = token[3:]
            # Bound before decoding, reject padding/alternate alphabets and
            # nonzero unused base64 bits; one packet has one canonical spelling.
            if len(body) not in (66, 87) or not re.fullmatch('[A-Za-z0-9_-]+', body):
                raise ValueError('shape')
            packet = base64.b64decode(body + '=' * (-len(body) % 4), altchars=b'-_', validate=True)
            if base64.urlsafe_b64encode(packet).decode().rstrip('=') != body:
                raise ValueError('canonicality')
            if packet[0] not in (0, 1, 2, 4, 5, 6):
                raise ValueError('flags')
            kind = ('m', 'p', 's')[packet[0] % 4]
            if len(packet) != (65 if kind == 's' else 49):
                raise ValueError('length')
            value = [kind, 'historical' if packet[0] & 4 else 'current',
                     packet[1:17].hex(), packet[17:33].hex(),
                     packet[33:49].hex() if kind == 's' else None]
            expected = bytes.fromhex(legacy_token(snapshot, key, value).rsplit('.', 1)[1])
            if not hmac.compare_digest(packet[-16:], expected):
                raise ValueError('scope')
            return value
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,480}\.[0-9a-f]{32}', token):
            raise ValueError('shape')
        body, _ = token.split('.')
        value = json.loads(base64.b64decode(body + '=' * (-len(body) % 4), altchars=b'-_', validate=True))
        if not hmac.compare_digest(token, legacy_token(snapshot, key, value)):
            raise ValueError('scope')
        return value
    except (ValueError, TypeError, UnicodeError):
        raise Failure('invalid_memory_reference', 'Reference is not issued for this bank, grant epoch and conversation.') from None
