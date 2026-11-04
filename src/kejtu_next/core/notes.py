"""Finite authored-note contract. No IO, authority inference or token allocator."""
from dataclasses import dataclass
import hashlib
import json
import re

from .memory import MemorySnapshot
from .records import Failure, ToolCall
from .note_fields import FIELDS, METADATA_FIELDS, field_schemas, validate_fields

ACTOR = 'assistant'  # Application identity, never a model-supplied field.

def note_schemas(*, write: bool) -> list[dict]:
    result = []
    if write:
        result.append({'type': 'function', 'function': {'name': 'write_note',
            'description': 'Own ideas need no quote or approval. Edit: full current '
                'search_memory/read_memory, read_ref + revised text. Metadata: read_ref + fields; no text. '
                'thread_status: open_thread only.',
            'parameters': {'type': 'object', 'properties': {
                'text': {'type': 'string'}, 'read_ref': {'type': 'string'}, **field_schemas()},
                'required': [], 'additionalProperties': False}}})
    result.append({'type': 'function', 'function': {'name': 'note_outcome',
        'description': 'Inspect operation_key; unknown is not failed. Does not write.',
        'parameters': {'type': 'object', 'properties': {'operation_key': {'type': 'string'}},
                       'required': ['operation_key'], 'additionalProperties': False}}})
    return result


@dataclass(frozen=True)
class NotePolicy:
    """Finite notebook retention allowances, independent of per-run admission.

    Payload bytes count every retained exact revision, not physical SQLite space.
    Counts also bound metadata/FTS growth; actual work uses the caller's Ledger.
    """
    revision_bytes: int = 65536
    retained_bytes: int = 536870912
    notes: int = 10000
    revisions: int = 100000
    outcomes: int = 100000
    basis_refs: int = 4
    key_bytes: int = 64
    reason_bytes: int = 256
    reference_bytes: int = 512
    outcome_bytes: int = 2048
    argument_bytes: int = 4096

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise ValueError('Note policy requires positive finite integer bounds.')

    @property
    def save_argument_bytes(self):
        # A full revision may be JSON-escaped. This is a serialization bound,
        # not a new context/output allowance; the existing ledger still owns fit.
        return 6 * self.revision_bytes + self.argument_bytes


@dataclass(frozen=True)
class NoteScope:
    memory: MemorySnapshot
    turn_id: str | None
    write_epoch: int
    job_run_id: str | None = None


@dataclass(frozen=True)
class NoteResult:
    outcome: dict
    # Only this exact own-commit transition may advance the active read view.
    # Replayed/no-op/refused operations return the caller's unchanged snapshot.
    memory: MemorySnapshot


def identity(value) -> bool:
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{32}', value) is not None


class NoteArgumentFailure(Failure):
    """Strict refusal with fixed diagnostics and application-owned field names."""

    def __init__(self, validation_reason: str, *, missing_identifiers: tuple[str, ...] = ()):
        super().__init__('invalid_tool_arguments', 'Invalid note arguments; no mutation was admitted.')
        self.validation_reason = validation_reason
        self.missing_identifiers = missing_identifiers

    @property
    def missing_revision(self):
        # Preserve the meaning of the earlier diagnostic/test marker, not a
        # second recovery policy. The application uses the complete field list.
        return self.missing_identifiers == ('expected_revision',)


def note_arguments(call: ToolCall, policy: NotePolicy = NotePolicy()) -> dict:
    def unique(pairs):
        value = dict(pairs)
        if len(value) != len(pairs):
            raise NoteArgumentFailure('duplicate_key')
        return value

    def text(value, maximum):
        return isinstance(value, str) and len(value.encode('utf-8')) <= maximum

    try:
        if not text(call.arguments, policy.save_argument_bytes):
            raise NoteArgumentFailure('serialized_argument_bound')
        args = json.loads(call.arguments, object_pairs_hook=unique)
        if not isinstance(args, dict):
            raise NoteArgumentFailure('object_required')
        if call.name == 'write_note' and 'operation' not in args:
            if set(args)-{'text','read_ref'}-FIELDS:
                raise NoteArgumentFailure('save_fields')
            if 'text' not in args and (not args.get('read_ref') or not set(args)&METADATA_FIELDS
                    or set(args)-{'read_ref'}-METADATA_FIELDS):
                raise NoteArgumentFailure('metadata_fields')
            if 'text' in args and (not text(args['text'], policy.revision_bytes) or not args['text']):
                raise NoteArgumentFailure('text')
            if 'read_ref' in args and (not text(args['read_ref'], policy.reference_bytes) or not args['read_ref']):
                raise NoteArgumentFailure('read_ref')
            try:
                validate_fields(args)
            except ValueError as error:
                raise NoteArgumentFailure(str(error)) from None
            return args
        # Retained exact-edit compatibility has its original bounds and checks.
        # It is not advertised as the ordinary authoring interface.
        if not text(call.arguments, policy.argument_bytes):
            raise NoteArgumentFailure('serialized_argument_bound')
        key = args.get('operation_key')
        if (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]+', key)
                or len(key) > policy.key_bytes):
            raise NoteArgumentFailure('operation_key')
        if call.name == 'note_outcome':
            if set(args) != {'operation_key'}:
                raise NoteArgumentFailure('outcome_fields')
            return args
        if call.name != 'write_note':
            raise NoteArgumentFailure('unknown_capability')
        base = {'operation', 'operation_key', 'reason', 'basis_refs'}
        mode = args.get('operation')
        if mode == 'create':
            keys = base | {'text'}
        elif mode == 'append':
            keys = base | {'note_id', 'expected_revision', 'text'}
        elif mode == 'replace':
            keys = base | {'note_id', 'expected_revision', 'offset', 'expected_text', 'replacement'}
        else:
            raise NoteArgumentFailure('unknown_mutation')
        if set(args) != keys:
            # Still reject exactly the same argument shapes. Missing identity
            # fields can be named without fetching or inferring their values.
            missing = keys - set(args)
            recoverable = (mode in ('append', 'replace') and not set(args) - keys
                           and missing <= {'note_id', 'expected_revision'})
            raise NoteArgumentFailure('mutation_fields',
                missing_identifiers=tuple(sorted(missing)) if recoverable else ())
        if not text(args['reason'], policy.reason_bytes) or not args['reason'].strip():
            raise NoteArgumentFailure('reason')
        refs = args['basis_refs']
        if (not isinstance(refs, list) or len(refs) > policy.basis_refs
                or any(not text(ref, policy.reference_bytes) or not ref for ref in refs)
                or len(set(refs)) != len(refs)):
            raise NoteArgumentFailure('basis_refs')
        if mode != 'create' and not identity(args['note_id']):
            raise NoteArgumentFailure('note_id')
        if mode != 'create' and not identity(args['expected_revision']):
            raise NoteArgumentFailure('expected_revision')
        if mode in ('create', 'append'):
            if not text(args['text'], policy.argument_bytes) or mode == 'create' and not args['text']:
                raise NoteArgumentFailure('text')
        elif (type(args['offset']) is not int or not 0 <= args['offset'] < 2**63
              or not text(args['expected_text'], policy.argument_bytes) or not args['expected_text']
              or not text(args['replacement'], policy.argument_bytes)):
            raise NoteArgumentFailure('exact_span')
        return args
    except UnicodeError:
        raise NoteArgumentFailure('invalid_utf8') from None
    except RecursionError:
        raise NoteArgumentFailure('json_nesting') from None
    except (ValueError, TypeError):
        raise NoteArgumentFailure('invalid_json_or_type') from None


def intent_digest(args: dict) -> str:
    # JSON spelling/order are not new intent. Do not retain another body copy.
    return hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=True,
                                     separators=(',', ':')).encode()).hexdigest()


def operation_key(turn_id: str | None, args: dict, *, job_run_id: str | None = None) -> str:
    """Application-owned identity for an authored save within its origin turn.

    Ignore provider call IDs (Ollama reuses them). Reordered JSON is the same
    intent; different text/target is not. A later turn can intentionally save anew.
    """
    if 'operation' in args or set(args) == {'operation_key'}:
        return args['operation_key']
    if job_run_id is not None:
        if turn_id is not None or not identity(job_run_id):
            raise Failure('note_origin_invalid', 'Choose one exact application-owned note origin.')
        return 'save:' + intent_digest({'job_run_id': job_run_id, 'save': args})[:59]
    return 'save:' + intent_digest({'turn_id': turn_id, 'save': args})[:59]


def edited_bytes(previous: bytes, args: dict, policy: NotePolicy) -> bytes:
    """Exact byte edits; callers verify the old payload and reserve construction work."""
    mode = args['operation']
    if mode == 'metadata':
        result = previous
    elif mode in ('create', 'revise'):
        result = args['text'].encode()
    elif mode == 'append':
        result = previous + args['text'].encode()
    else:
        start, expected = args['offset'], args['expected_text'].encode()
        end = start + len(expected)
        if previous[start:end] != expected:
            raise Failure('note_span_conflict', 'The exact expected bytes do not match at this offset.')
        if start == 0 and end == len(previous):
            raise Failure('note_whole_replace', 'Whole-note replacement is outside this slice.')
        try:
            previous[:start].decode('utf-8')
            previous[end:].decode('utf-8')
        except UnicodeError:
            raise Failure('note_span_conflict', 'Replacement does not respect UTF-8 boundaries.') from None
        result = previous[:start] + args['replacement'].encode() + previous[end:]
    if len(result) > policy.revision_bytes:
        raise Failure('note_quota', 'The exact revision exceeds the development byte allowance.')
    return result
