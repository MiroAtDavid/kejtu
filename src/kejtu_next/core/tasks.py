"""Personal commitments: outcome and attention are independent, never clock-driven."""
from datetime import date
import json
import re

from .records import Failure

DESCRIPTION_BYTES = 32768

OUTCOMES = ('open', 'done', 'cancelled')
ATTENTION = ('active', 'parked', 'archived')
FIELDS = {
    'list': {'query', 'outcome', 'attention', 'offset', 'limit', 'day', 'project_id', 'section_id'},
    'read': {'read_ref', 'offset', 'limit', 'view'},
    'save': {'read_ref', 'title', 'description', 'due', 'note_id', 'source_quote', 'placement'},
    'transition': {'read_ref', 'outcome', 'attention'},
}


def validate(value, *, manual=False):
    try:
        if not isinstance(value, dict) or value.get('action') not in FIELDS:
            raise ValueError
        action = value['action']
        if set(value) - FIELDS[action] - {'action'} - ({'mutation_key'} if manual else set()):
            raise ValueError
        if action in ('read', 'transition') and 'read_ref' not in value:
            raise ValueError
        if 'view' in value and value['view'] not in ('history','sources'):
            raise ValueError
        if action == 'save' and 'read_ref' not in value and 'title' not in value:
            raise ValueError
        if action == 'transition' and not {'outcome', 'attention'} & value.keys():
            raise ValueError
        if action == 'save' and not {'title', 'description', 'due', 'note_id', 'placement'} & value.keys():
            raise ValueError
        for key in ('due', 'day'):
            if key in value and value[key] is not None:
                if not isinstance(value[key], str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value[key]):
                    raise ValueError
                date.fromisoformat(value[key])
        for key, maximum in (('title', 500), ('query', 256), ('source_quote', 2048), ('read_ref', 512)):
            if key in value:
                text = value[key]
                if (not isinstance(text, str) or '\x00' in text or len(text.encode()) > maximum
                        or key != 'query' and not text.strip()):
                    raise ValueError
        if 'description' in value and (not isinstance(value['description'], str) or '\x00' in value['description']
                or len(value['description'].encode()) > DESCRIPTION_BYTES):
            raise ValueError
        if 'placement' in value:
            if not manual:
                raise ValueError
            place = value['placement']
            if not isinstance(place, dict) or set(place) != {'project_id', 'section_id', 'expected_links'}:
                raise ValueError
            for key in ('project_id', 'section_id'):
                if place[key] is not None and (not isinstance(place[key],str) or not re.fullmatch('[0-9a-f]{32}',place[key])):
                    raise ValueError
            links = place['expected_links']
            if not isinstance(links,list) or len(links)>100:
                raise ValueError
            for link in links:
                if (not isinstance(link,dict) or set(link)!={'link_id','revision'} or
                        not isinstance(link['link_id'],str) or not re.fullmatch('[0-9a-f]{32}',link['link_id']) or
                        type(link['revision']) is not int or not 1 <= link['revision'] < 9007199254740991):
                    raise ValueError
            if len({link['link_id'] for link in links}) != len(links):
                raise ValueError
        for key in ('note_id', 'mutation_key', 'project_id', 'section_id'):
            if key in value and not (key == 'note_id' and value[key] is None):
                if not isinstance(value[key], str) or re.fullmatch('[0-9a-f]{32}', value[key]) is None:
                    raise ValueError
        for key, options in (('outcome', OUTCOMES), ('attention', ATTENTION)):
            if key in value and value[key] not in options + (('all',) if action == 'list' else ()):
                raise ValueError
        for key, low, high in (('offset', 0, 10000), ('limit', 1, 50)):
            if key in value and (type(value[key]) is not int or not low <= value[key] <= high):
                raise ValueError
        if manual and action in ('save', 'transition') and 'mutation_key' not in value:
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeError):
        # Fixed, action-specific help, never echo private malformed arguments.
        action = value.get('action') if isinstance(value, dict) else None
        help_text = {
            'list': 'list accepts query, outcome, attention, offset, limit and day; all is a list-only filter.',
            'read': 'read needs read_ref and returns the current task. For older revisions use view=history; '
                    'for supporting sources use view=sources. offset/limit page the selected view.',
            'save': 'save uses title/description/due/note_id/placement and source_quote; add read_ref for edits. '
                    'Outcome/attention belong to transition, not save.',
            'transition': 'transition needs read_ref and outcome and/or attention. '
                    'Park: attention=parked; reactivate: attention=active. '
                    'Do not include title, due, note_id or source_quote.',
        }.get(action if isinstance(action, str) else None,
              'Choose action list, read, save or transition with that action\'s fields.')
        raise Failure('task_arguments', help_text + ' No change ran.') from None


def arguments(call):
    def unique(pairs):
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError
        return value
    try:
        return validate(json.loads(call.arguments, object_pairs_hook=unique))
    except (ValueError, TypeError):
        raise Failure('task_arguments', 'Task arguments must be a single valid object; no change ran.') from None


def schema(write=False):
    return {'type': 'function', 'function': {
        'name': 'personal_tasks',
        'description': 'User action commitments, separate from notes and reminders. '
            'list: query/outcome/offset/limit/day and optional project_id/section_id; defaults to all open tasks. Attention is legacy metadata, not a user workflow. '
            'read: read_ref returns the current task only; view=history adds older revisions, '
            'view=sources adds supporting sources; offset/limit page that view. ' + (
            'Use read_ref from this turn\'s list or read result to change that task; no extra read after list. '
            'save: create with title and exact current-user source_quote, optional Markdown description (32 KiB), due/note_id; '
            'edit with read_ref, source_quote and title/description/due/note_id only. Preserve formatting and checklist state. Use university task_link records to change project/section links. '
            'transition: read_ref plus outcome and/or attention ONLY; omit source_quote, title, due, note_id. '
            'Complete outcome=done; reopen outcome=open; remove outcome=cancelled. '
            'Outcome and attention are independent; the omitted axis is unchanged. Change only on user intent. '
            if write else 'Only list and read are available in this request. ') +
            'Dates never trigger reminders or work. Confirm changes only from operation receipts.',
        'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['action'],
            'properties': {
                'action': {'type': 'string', 'enum': ['list', 'read'] + (['save', 'transition'] if write else [])},
                'read_ref': {'type': 'string', 'description': 'Copy read_ref from list/read unchanged, never the task id. A current-turn list is enough for updates.'},
                'query': {'type': 'string', 'description': 'List only: title filter.'},
                'description': {'type': 'string', 'description': 'Save only: full Markdown description, paragraphs, lists, links and checklists; at most 32768 UTF-8 bytes. Empty clears it; omitted preserves it.'},
                'project_id': {'type':'string','description':'List only: tasks in this project, including its sections.'},
                'section_id': {'type':'string','description':'List only: tasks in this section.'},
                'title': {'type': 'string', 'description': 'Save only: required for creation.'},
                'source_quote': {'type': 'string', 'description': 'Save only: exact words from the current user message. Omit for transition.'},
                'due': {'type': ['string', 'null'], 'description': 'Save only: YYYY-MM-DD; null means no due date, not a reminder.'},
                'note_id': {'type': ['string', 'null'], 'description': 'Save only: optional existing note ID; null unlinks.'},
                'outcome': {'type': 'string', 'enum': ['all', *OUTCOMES], 'description': 'List filter or transition state. all is list-only.'},
                'attention': {'type': 'string', 'enum': ['all', *ATTENTION], 'description': 'List filter or transition state. all is list-only; active reactivates parked/archived work.'},
                'offset': {'type': 'integer', 'minimum': 0},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50},
                'day': {'type': 'string', 'description': 'Explicit civil date for due/overdue classification.'},
                'view': {'type': 'string', 'enum': ['history','sources'],
                         'description': 'Read only: omit for current task only; history requests revision history, sources requests supporting sources.'},
            }}}}
