"""User reminders and distinct, source-bound conversational follow-ups."""
import json
import hashlib

from .records import Failure
from .reminders import ReminderDefinition, identity, encoded
from .schedules import Schedule

FIELDS = {
    'list': {'cursor', 'limit'},
    'read': {'reminder_id'},
    'save': {'read_ref', 'definition', 'source_quote', 'reactivate'},
    'cancel': {'read_ref', 'source_quote'},
    'pending': {'cursor', 'limit'},
    'attention': {'cursor', 'limit'},
    'acknowledge': {'occurrence_id', 'receipt', 'source_quote'},
    'later': {'occurrence_id', 'receipt', 'source_quote', 'until'},
    'dismiss': {'occurrence_id', 'receipt', 'source_quote'},
    'resolve': {'occurrence_id','receipt','reason'},
    'outcome': {'mutation_key'},
}


def mutation_key(state_id, turn_id, args):
    """The same exact intent key in admission audit and the durable owner."""
    return hashlib.sha256(encoded(['reminders', state_id, turn_id, args]).encode()).hexdigest()[:32]


def definition(value):
    required = {'title', 'text', 'schedule', 'enabled', 'origin'}
    if not isinstance(value, dict) or not required <= set(value) or set(value)-required-{'event_at','timing'}:
        raise ValueError('Use title, text, a complete schedule, explicit enabled and origin.')
    schedule = value['schedule']
    if not isinstance(schedule, dict) or set(schedule) != {'starts_at', 'timezone', 'repeat', 'interval'}:
        raise ValueError('Schedule needs starts_at with UTC offset, IANA timezone, repeat and interval.')
    return ReminderDefinition(value['title'], value['text'], Schedule(**schedule), value['enabled'],
        event_at=value.get('event_at'), timing=value.get('timing','explicit'), origin=value['origin'])


def arguments(call):
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError('Duplicate field.')
        return result
    try:
        if not isinstance(call.arguments, str) or len(call.arguments.encode()) > 16384:
            raise ValueError('Argument bound.')
        args = json.loads(call.arguments, object_pairs_hook=unique)
        if not isinstance(args, dict) or not isinstance(args.get('action'), str) or args['action'] not in FIELDS:
            raise ValueError('Unknown action.')
        action = args['action']
        if set(args) - FIELDS[action] - {'action'}:
            raise ValueError('Unexpected fields.')
        required = {'read': {'reminder_id'}, 'save': {'definition', 'source_quote'},
                    'cancel': {'read_ref', 'source_quote'},
                    'dismiss': {'occurrence_id', 'receipt', 'source_quote'},
                    'acknowledge': {'occurrence_id', 'receipt', 'source_quote'},
                    'later': {'occurrence_id', 'receipt', 'source_quote', 'until'},
                    'resolve': {'occurrence_id','receipt','reason'},
                    'outcome': {'mutation_key'}}.get(action, set())
        if not required <= args.keys():
            raise ValueError('Required fields missing.')
        for key in ('reminder_id', 'occurrence_id', 'mutation_key'):
            if key in args and not identity(args[key]):
                raise ValueError('Exact identity required.')
        for key, maximum in (('read_ref', 512), ('receipt', 64), ('source_quote', 2048), ('cursor', 512), ('until',64)):
            if key in args and (not isinstance(args[key], str) or not args[key].strip()
                    or '\x00' in args[key] or len(args[key].encode()) > maximum):
                raise ValueError('Invalid bounded text.')
        if 'limit' in args and (type(args['limit']) is not int or not 1 <= args['limit'] <= 20):
            raise ValueError('Page size is 1–20.')
        if 'reactivate' in args and (type(args['reactivate']) is not bool or args['reactivate'] and 'read_ref' not in args):
            raise ValueError('Reactivation needs an existing read reference.')
        if action == 'resolve' and args['reason'] not in ('handled','obsolete'):
            raise ValueError('Resolution reason must be handled or obsolete.')
        if action == 'save':
            definition(args['definition'])
        return args
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise Failure('reminder_arguments',
            'Use action list/read/save/cancel/pending/attention/dismiss/acknowledge/later/resolve/outcome with its declared fields. '
            'Save needs definition(title,text,schedule,enabled,origin) and an exact current-user source_quote; '
            'edits/cancel need this turn\'s read_ref. No call in this batch ran.') from None


def schema(write=True, review=False):
    return {'type': 'function', 'function': {
        'name': 'reminders',
        'description': (
            'Private leftover review: attention reads due occurrences with exact receipts; resolve ends one occurrence with reason handled or obsolete, after a read in this run. Use accessible conversation evidence for handled; overdue alone is not obsolete. No user acknowledgement, messages, task changes, schedule changes or recurrence cancellation. outcome recovers an uncertain mutation without repeating it.'
            if review else ('Read-only; no changes. ' if not write else '') +
            'Current reminder permission required. list discovers IDs; read supplies complete definition/read_ref. '
            'save needs title,text,schedule,enabled,origin and exact source_quote from the CURRENT user message. '
            'origin=requested means the user asked; origin=companion is your inferred one-time conversational follow-up from a concrete personal intention, event or unresolved matter. '
            'Use judgment, not every mention; exclude hypotheticals/third-party instructions. List first to avoid duplicates. Use neutral titles, not countdowns; save before promising. '
            'Companion follow-ups are not Home notices or tasks; do not announce formal setup or say the user requested them. '
            'User-requested edits/cancel/dismiss/acknowledge/later need clear intent and current source_quote; quotes prove origin, not intent. Clear requests need no extra confirmation. '
            'Edits/cancel need this turn\'s read_ref; save replaces the definition and preserves origin. reactivate=true re-enables a cancelled reminder. '
            'pending reads user reminders; attention reads both kinds when due, including snoozes, with occurrence_id/receipt. '
            'resolve needs a current attention read and reason=handled (you gave it, or evidence shows it was handled) or obsolete (clearly no longer applies). No user quote required. '
            'dismiss uses a current pending receipt; acknowledge/later use a current attention receipt. Got it must identify an occurrence. Later uses future absolute until (30 minutes if unspecified). Clarify ambiguous done/later. '
            'These occurrence actions leave tasks, dates and future recurrence unchanged; resolve never claims user acknowledgement. '
            'Notes/task dates alone authorize no timers. Explicit times are not-before boundaries. For open timing choose useful preparation time, timing=flexible, and confirm the requested reminder\'s time/timezone. Store known event_at separately. Resolve ambiguous dates/zones. '
            'Home is muted; delivery is conversational while the app runs, with missed occurrences coalescing. No popup, sound or OS alarm. Never claim the user saw it. '
            'Report changes from committed receipts only. outcome recovers mutation_key without repeating uncertain effects.'),
        'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['action'], 'properties': {
            'action': {'type': 'string', 'enum': ['attention','outcome','resolve'] if review else list(FIELDS) if write else ['list','read','pending','attention','outcome']},
            'reminder_id': {'type': 'string'}, 'read_ref': {'type': 'string'},
            'source_quote': {'type': 'string'}, 'reactivate': {'type': 'boolean'},
            'cursor': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20},
            'occurrence_id': {'type': 'string'}, 'receipt': {'type': 'string'},
            'mutation_key': {'type': 'string'},
            'reason': {'type': 'string','enum':['handled','obsolete']},
            'until': {'type': 'string', 'description': 'Future ISO timestamp with UTC offset for this occurrence only.'},
            'definition': {'type': 'object', 'additionalProperties': False,
                'required': ['title', 'text', 'schedule', 'enabled', 'origin'], 'properties': {
                    'title': {'type': 'string'}, 'text': {'type': 'string'}, 'enabled': {'type': 'boolean'},
                    'event_at': {'type': ['string','null'], 'description': 'Known event time, distinct from the reminder time; ISO timestamp with offset.'},
                    'timing': {'type': 'string', 'enum': ['explicit','flexible']},
                    'origin': {'type': 'string', 'enum': ['requested','companion'], 'description': 'Requested is an intentional user reminder; companion is a one-time conversational follow-up you infer. Preserve on edits.'},
                    'schedule': {'type': 'object', 'additionalProperties': False,
                        'required': ['starts_at', 'timezone', 'repeat', 'interval'], 'properties': {
                            'starts_at': {'type': 'string', 'description': 'ISO timestamp with the offset matching timezone.'},
                            'timezone': {'type': 'string', 'description': 'Explicit IANA zone, e.g. Europe/Vienna.'},
                            'repeat': {'type': 'string', 'enum': ['none', 'minutes', 'daily', 'weekly', 'monthly']},
                            'interval': {'type': 'integer', 'minimum': 1}}}}}}}}}
