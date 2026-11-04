"""Read-only memory identities and finite argument contract; no IO or routing."""
from dataclasses import dataclass
import json
import re

from .records import Failure, ToolCall
from .note_fields import PURPOSES, THREAD_STATES, FLAGS, tag_key

MEMORY_NOTICE = (
    '\nAdditional grant: search_memory discovers eligible cross-conversation '
    'memories in this bank. read_memory opens returned body, provenance or source '
    'references. Current/historical modes are explicit; no writes or import.'
)


@dataclass(frozen=True)
class MemorySnapshot:
    conversation_id: str | None
    bank_id: str
    epoch: int
    grant_epoch: int
    hybrid: bool = False
    archive_epoch: int = 0
    job_run_id: str | None = None
    archive_sources: int | None = None  # Research-only append-safe archive authority.


MEMORY_ARGUMENT_REASONS = frozenset({
    'duplicate_key', 'serialized_argument_bound', 'object_required',
    'search_fields', 'query', 'mode', 'cursor', 'read_fields', 'read_ref',
    'offset', 'max_bytes', 'unknown_capability', 'invalid_utf8',
    'json_nesting', 'invalid_json_or_type', 'collection_scope', 'role_scope',
})


class MemoryArgumentFailure(Failure):
    """Fixed private metadata; the public refusal never echoes supplied values."""
    def __init__(self, validation_reason: str):
        super().__init__('invalid_tool_arguments', 'Memory arguments are invalid; no operation ran.')
        self.validation_reason = validation_reason


def memory_arguments(call: ToolCall, *, defaults=False, hybrid=False) -> dict:
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise MemoryArgumentFailure('duplicate_key')
        return result
    try:
        if not isinstance(call.arguments, str):
            raise MemoryArgumentFailure('invalid_json_or_type')
        if len(call.arguments.encode()) > 4096:
            raise MemoryArgumentFailure('serialized_argument_bound')
        value = json.loads(call.arguments, object_pairs_hook=unique)
        if not isinstance(value, dict):
            raise MemoryArgumentFailure('object_required')
        if defaults:
            # Only the opted-in authoring interface: omitted paging controls are
            # mechanics, never inferred identity, permission or a historical mode.
            if call.name == 'search_memory':
                value = dict(mode='current', cursor='') | value
            elif call.name == 'read_memory':
                value = dict(offset=0, max_bytes=65536) | value
        if call.name == 'search_memory':
            optional = {'scope','collection','conversation_id','role','since','until','order','limit','purpose','tag','tags','tag_mode','thread_status','flag','include_rejected'} if hybrid else set()
            if not {'query','mode','cursor'} <= set(value) or set(value)-{'query','mode','cursor'}-optional:
                raise MemoryArgumentFailure('search_fields')
            if (not isinstance(value['query'], str) or '\x00' in value['query']
                    or len(value['query'].encode()) > (2000 if hybrid else 256)):
                raise MemoryArgumentFailure('query')
            if value['mode'] not in ('current', 'historical'):
                raise MemoryArgumentFailure('mode')
            if not isinstance(value['cursor'], str) or len(value['cursor']) > 512:
                raise MemoryArgumentFailure('cursor')
            if value.get('scope','all') not in ('all','notebook','memories','conversations','tools','tags','tag_cleanup'):
                raise MemoryArgumentFailure('search_fields')
            if 'collection' in value and (value.get('scope') not in ('notebook','memories')
                    or value['collection'] not in ('all','current','historical')):
                raise MemoryArgumentFailure('collection_scope')
            if 'tags' in value:
                tags=value['tags']
                if (not isinstance(tags,list) or not 1<=len(tags)<=5 or 'tag' in value
                        or any(not isinstance(tag,str) or not tag.strip() or '\x00' in tag or len(tag.encode())>80 for tag in tags)
                        or len({tag_key(tag) for tag in tags})!=len(tags)):
                    raise MemoryArgumentFailure('search_fields')
            if ('tag_mode' in value and ('tags' not in value or value['tag_mode'] not in ('any','all'))
                    or 'flag' in value and value['flag'] not in FLAGS
                    or 'include_rejected' in value and type(value['include_rejected']) is not bool):
                raise MemoryArgumentFailure('search_fields')
            if value.get('scope') in ('tags','tag_cleanup') and (value['mode']!='current' or set(value)-{
                    'query','mode','cursor','scope','limit'} or len(value['query'].encode())>80
                    or value['scope']=='tag_cleanup' and value['query']):
                raise MemoryArgumentFailure('search_fields')
            if ('purpose' in value and value['purpose'] not in PURPOSES or
                    'thread_status' in value and value['thread_status'] not in THREAD_STATES or
                    'tag' in value and (not isinstance(value['tag'],str) or not value['tag'].strip() or len(value['tag'].encode())>80)):
                raise MemoryArgumentFailure('search_fields')
            if 'role' in value and (value['role'] not in ('user','assistant') or value.get('scope')!='conversations'):
                raise MemoryArgumentFailure('role_scope')
            if value.get('order','relevance') not in ('relevance','asc','desc'):
                raise MemoryArgumentFailure('search_fields')
            if type(value.get('limit',8)) is not int or not 1<=value.get('limit',8)<=40:
                raise MemoryArgumentFailure('search_fields')
            if 'conversation_id' in value and (not isinstance(value['conversation_id'],str)
                    or not re.fullmatch('[0-9a-f]{32}',value['conversation_id'])):
                raise MemoryArgumentFailure('search_fields')
            for field in ('since','until'):
                if field in value:
                    from datetime import datetime
                    if not isinstance(value[field],str) or len(value[field])>64:
                        raise MemoryArgumentFailure('search_fields')
                    try:
                        if datetime.fromisoformat(value[field]).tzinfo is None: raise ValueError()
                    except ValueError:
                        raise MemoryArgumentFailure('search_fields') from None
        elif call.name == 'read_memory':
            if set(value) != {'read_ref', 'offset', 'max_bytes'}:
                raise MemoryArgumentFailure('read_fields')
            if (not isinstance(value['read_ref'], str)
                    # Syntax only: storage owns canonical decoding, types and
                    # scope authentication for both legacy and compact handles.
                    or not re.fullmatch(r'(?:[A-Za-z0-9_-]{1,480}\.[0-9a-f]{32}|R1\.[A-Za-z0-9_-]{66}(?:[A-Za-z0-9_-]{21})?)', value['read_ref'])):
                raise MemoryArgumentFailure('read_ref')
            if type(value['offset']) is not int or not 0 <= value['offset'] < 2**63:
                raise MemoryArgumentFailure('offset')
            if type(value['max_bytes']) is not int or not 1 <= value['max_bytes'] < 2**63:
                raise MemoryArgumentFailure('max_bytes')
        else:
            raise MemoryArgumentFailure('unknown_capability')
        return value
    except UnicodeError:
        raise MemoryArgumentFailure('invalid_utf8') from None
    except RecursionError:
        raise MemoryArgumentFailure('json_nesting') from None
    except (ValueError, TypeError):
        raise MemoryArgumentFailure('invalid_json_or_type') from None


def memory_schemas(*, hybrid=False) -> list[dict]:
    result = [
        {'type': 'function', 'function': {'name': 'search_memory',
         'description': 'Search granted notes and conversation originals by keywords and meaning when local embeddings are available; empty query browses. current resolves claims; historical finds old revisions. Previews only; use returned cursor unchanged.',
         'parameters': {'type': 'object', 'properties': {
             'query': {'type': 'string'}, 'mode': {'type': 'string', 'enum': ['current', 'historical']},
             'cursor': {'type': 'string'}, 'scope':{'type':'string','enum':['all','notebook','memories','conversations','tools','tags','tag_cleanup']},
             'collection':{'type':'string','enum':['all','current','historical'],
                           'description':'Notebook/memories only: current excludes the imported historical collection. Empty current-mode notebook query pages the complete eligible inventory.'},
             'conversation_id':{'type':'string'},'role':{'type':'string','enum':['user','assistant'],
                 'description':'Only with scope:conversations; omit for other scopes.'},
             'since':{'type':'string'},'until':{'type':'string'},'order':{'type':'string','enum':['relevance','asc','desc']},
             'purpose':{'type':'string'},'tag':{'type':'string'},'thread_status':{'type':'string'},
             'tags':{'type':'array','items':{'type':'string'}},'tag_mode':{'type':'string','enum':['any','all']},
             'flag':{'type':'string'},'include_rejected':{'type':'boolean'},
             'limit':{'type':'integer','minimum':1,'maximum':40}}, 'required': ['query', 'mode', 'cursor'], 'additionalProperties': False}}},
        {'type': 'function', 'function': {'name': 'read_memory',
         'description': 'Exact quoted memory/source bytes or whole provenance edges via returned read_ref. Start offset 0, then next_offset. max_bytes is a ceiling; continuation is unread.',
         'parameters': {'type': 'object', 'properties': {
             'read_ref': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0},
             'max_bytes': {'type': 'integer', 'minimum': 1}},
             'required': ['read_ref', 'offset', 'max_bytes'], 'additionalProperties': False}}},
    ]
    if not hybrid:
        function=result[0]['function']
        function['description']='Literal memory search; empty query browses. current resolves claims, historical finds old revisions. Previews only. Use returned cursor unchanged, or empty.'
        function['parameters']['properties']={k:v for k,v in function['parameters']['properties'].items() if k in ('query','mode','cursor')}
    return result
