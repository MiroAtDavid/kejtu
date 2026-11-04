"""People assertions and conservative family-path semantics, independent of notes."""
from collections import deque
from datetime import date
import json
import re

from .records import Failure

RELATIONS = ('parent', 'sibling', 'partner', 'spouse', 'friend', 'colleague',
             'manager', 'neighbor', 'aunt_uncle', 'niece_nephew', 'cousin',
             'grandparent', 'parent_in_law', 'sibling_in_law', 'guardian',
             'godparent', 'housemate', 'classmate', 'other')
SYMMETRIC = {'sibling', 'partner', 'spouse', 'friend', 'colleague', 'neighbor',
             'cousin', 'sibling_in_law', 'housemate', 'classmate'}
# The advertised contract and storage validation share this owner. Qualifiers
# state only what the user asserted; they never supply missing relatives.
FAMILY_QUALIFIERS = ('unspecified', 'biological', 'adoptive', 'step')
QUALIFIERS = ('unspecified', 'biological', 'adoptive', 'step', 'full', 'half')
RELATION_QUALIFIERS = {
    **{kind: FAMILY_QUALIFIERS for kind in ('parent', 'grandparent', 'aunt_uncle', 'niece_nephew', 'cousin')},
    'sibling': QUALIFIERS,
}
DIRECT_FAMILY = {
    'parent': ('child', 'parent'), 'sibling': ('sibling', 'sibling'),
    'grandparent': ('grandchild', 'grandparent'),
    'aunt_uncle': ('niece or nephew', 'aunt or uncle'),
    'niece_nephew': ('aunt or uncle', 'niece or nephew'), 'cousin': ('cousin', 'cousin'),
    'parent_in_law': ('child-in-law', 'parent-in-law'),
    'sibling_in_law': ('sibling-in-law', 'sibling-in-law'),
    'guardian': ('person under guardianship', 'guardian'),
    'godparent': ('godchild', 'godparent'), 'spouse': ('spouse', 'spouse'),
    'partner': ('partner', 'partner'),
}


def relation_qualifiers(kind):
    return RELATION_QUALIFIERS.get(kind, ('unspecified',))


def qualifier_guidance():
    groups = {}
    for kind in RELATIONS:
        groups.setdefault(relation_qualifiers(kind), []).append(kind)
    return 'Qualifiers: ' + '; '.join('/'.join(kinds) + ': ' + '/'.join(options)
        for options, kinds in groups.items()) + '. Omit unknown qualifiers; never guess full/half or biological ties.'
WRITES = {'record', 'correct', 'withdraw', 'merge', 'split'}
FIELDS = {
    'find': {'query', 'cursor', 'limit'},
    'read': {'person_id', 'cursor', 'limit', 'view'},
    'record': {'subject', 'kind', 'value', 'object', 'qualifier', 'certainty', 'status', 'since', 'until', 'source_quote', 'source_offset'},
    'correct': {'read_ref', 'subject', 'value', 'object', 'qualifier', 'certainty', 'status', 'since', 'until', 'source_quote', 'source_offset'},
    'withdraw': {'read_ref', 'source_quote', 'source_offset'},
    'kinship': {'person_id', 'relative_to', 'as_of', 'cursor', 'limit'},
    'preview_merge': {'person_id', 'target_id'},
    'merge': {'preview', 'source_quote', 'source_offset'},
    'split': {'person_id', 'preview', 'source_quote', 'source_offset'},
    'preview_split': {'person_id'},
}


def identity(value):
    return value == 'self' or isinstance(value, str) and re.fullmatch('[0-9a-f]{32}', value) is not None


def terms(value):
    return sorted(set(re.findall(r'\w+', value.casefold())))[:64]


def validate(value):
    try:
        if not isinstance(value, dict) or not isinstance(value.get('action'), str) or value['action'] not in FIELDS:
            raise ValueError
        action = value['action']
        if set(value) - FIELDS[action] - {'action'}:
            raise ValueError
        required = {'read': {'person_id'}, 'record': {'subject', 'kind', 'value', 'source_quote'},
                    'correct': {'read_ref', 'source_quote'}, 'withdraw': {'read_ref', 'source_quote'},
                    'kinship': {'person_id', 'relative_to', 'as_of'},
                    'preview_merge': {'person_id', 'target_id'}, 'merge': {'preview', 'source_quote'},
                    'preview_split': {'person_id'}, 'split': {'person_id', 'preview', 'source_quote'}}.get(action, set())
        if not required <= value.keys():
            raise ValueError
        for key in ('person_id', 'target_id', 'relative_to', 'subject', 'object'):
            if key in value and not (key == 'subject' and value[key] == 'new') and not identity(value[key]):
                raise ValueError
        for key, maximum in (('query', 256), ('value', 1000), ('source_quote', 2048),
                             ('cursor', 4096), ('read_ref', 1024), ('preview', 4096)):
            if key in value and (not isinstance(value[key], str) or '\x00' in value[key]
                    or len(value[key].encode()) > maximum or key not in ('query', 'cursor') and not value[key].strip()):
                raise ValueError
        for key, options in (('kind', ('name', 'fact', 'relation')), ('certainty', ('explicit', 'tentative')),
                             ('status', ('current', 'former', 'denied')), ('view', ('current', 'history'))):
            if key in value and value[key] not in options:
                raise ValueError
        if 'qualifier' in value and value['qualifier'] not in QUALIFIERS:
            raise ValueError
        for key in ('since', 'until', 'as_of'):
            if key in value and value[key] is not None:
                if not isinstance(value[key], str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value[key]):
                    raise ValueError
                date.fromisoformat(value[key])
        if action == 'kinship' and not value['as_of']:
            raise ValueError
        for key, low, high in (('limit', 1, 30), ('source_offset', 0, 2**53-1)):
            if key in value and (type(value[key]) is not int or not low <= value[key] <= high):
                raise ValueError
        if action == 'correct' and not (set(value) - {'action', 'read_ref', 'source_quote', 'source_offset'}):
            raise ValueError
        return value
    except (TypeError, ValueError, UnicodeError):
        raise Failure('people_arguments', 'Use the fields for the selected people action. Writes need exact current-user source_quote; edits need a current read_ref. No change ran.') from None


def arguments(call):
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError
        return result
    try:
        if len(call.arguments.encode()) > 8192:
            raise ValueError
        return validate(json.loads(call.arguments, object_pairs_hook=unique))
    except (ValueError, TypeError):
        raise Failure('people_arguments', 'People arguments must be one bounded JSON object; no change ran.') from None


def schema(write=False):
    actions = [name for name in FIELDS if write or name not in WRITES]
    properties = {key: {'type': 'string'} for key in (
        'query', 'cursor', 'person_id', 'relative_to', 'target_id', 'subject', 'object',
        'value', 'source_quote', 'read_ref', 'preview', 'as_of')}
    properties.update(action={'type': 'string', 'enum': actions},
        kind={'type': 'string', 'enum': ['name', 'fact', 'relation']},
        qualifier={'type': 'string', 'enum': list(QUALIFIERS), 'description': qualifier_guidance()},
        certainty={'type': 'string', 'enum': ['explicit', 'tentative']},
        status={'type': 'string', 'enum': ['current', 'former', 'denied']},
        view={'type': 'string', 'enum': ['current', 'history']},
        since={'type': ['string', 'null']}, until={'type': ['string', 'null']},
        source_offset={'type': 'integer', 'minimum': 0}, limit={'type': 'integer', 'minimum': 1, 'maximum': 30})
    return {'type': 'function', 'function': {'name': 'people', 'description':
        'Source-backed people, separate from notes. find(query) matches name words; empty browses. '
        'read(person_id,view) pages assertions and exact sources; cursor continues unchanged. '
        'kinship(person_id,relative_to,as_of YYYY-MM-DD) explains family paths; limits are disclosed. '
        'self is the user. Same names are distinct; clarify ambiguity, never infer identity from similarity. ' + (
        'Record only clear user-supported real claims, not fiction/quoted third-party claims. '
        'record: subject=id/self/new, kind=name/fact/relation, value=name/fact/relation type, '
        'object=id for relation, source_quote=exact current user words; source_offset=UTF-8 byte offset if repeated. '
        'Relations: ' + '/'.join(RELATIONS) + '. parent/grandparent/manager/aunt_uncle/parent_in_law/guardian/godparent point to child/grandchild/report/niece-nephew/child-in-law/ward/godchild. Reverse the endpoints for the inverse relation. '
        'New creates an independent person; record one assertion per call. Use returned IDs. '
        'correct/withdraw: copy the exact current read_ref from an admitted read and current source_quote. '
        'certainty explicit/tentative; status current/former/denied; since/until inclusive dates or unknown. '
        'Correct only the identified claim; preserve conflicts and independent reports. '
        'On explicit identity correction, subject/object moves that claim; subject=new separates it into a new person. '
        'Identity repair only on explicit user intent: preview_merge(person_id,target_id) then merge(preview,source_quote); '
        'preview_split(person_id) then split(person_id,preview,source_quote) reverses that merge. '
        'Confirm only committed receipts; quotes establish provenance, not interpretation correctness.' if write else
        'Reading only; saving and identity repair are not granted.'),
        'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['action'], 'properties': properties}}}


LABELS = {'U': 'parent', 'D': 'child', 'S': 'sibling', 'UU': 'grandparent', 'DD': 'grandchild',
          'UUU': 'great-grandparent', 'DDD': 'great-grandchild', 'US': 'aunt or uncle',
          'SD': 'niece or nephew', 'USD': 'first cousin', 'UUS': 'great-aunt or great-uncle',
          'SDD': 'grandniece or grandnephew', 'UD': 'sibling', 'UDD': 'niece or nephew',
          'UUD': 'aunt or uncle', 'UUDD': 'first cousin'}


def family_paths(start, end, neighbors, as_of, *, maximum=1000):
    """Load only visited neighborhoods; no transitive sibling/social inference.

    Direct parent paths also represent shared-parent siblings. No invented
    intermediate people or materialized derived assertions. Budget exhaustion
    marks the view incomplete, never a proof of unrelatedness.
    """
    queue = deque([(start, '', [], frozenset((start,)))])
    found, seen_results, work = [], set(), 0
    while queue and work < maximum:
        node, pattern, path, seen = queue.popleft()
        work += 1
        if node == end and pattern in LABELS:
            key = (LABELS[pattern], tuple(sorted(e['id'] for e in path)))
            if key not in seen_results:
                seen_results.add(key)
                found.append(dict(relationship=LABELS[pattern], path=path,
                                  basis='derived from stated family connections; qualifiers remain in path'))
            continue
        if len(pattern) >= 4:
            continue
        edges, complete = neighbors(node)
        if not complete:
            return found, False, work
        denied = {(e['subject'], e['object'], e['value']) for e in edges if e['status'] == 'denied'
                  and (not e['since'] or e['since'] <= as_of) and (not e['until'] or as_of <= e['until'])}
        for edge in edges:
            if (edge['certainty'] != 'explicit' or edge['status'] != 'current' 
                    or edge['since'] and as_of < edge['since'] or edge['until'] and as_of > edge['until']):
                continue
            a, b, kind = edge['subject'], edge['object'], edge['value']
            if (a, b, kind) in denied or kind in SYMMETRIC and (b, a, kind) in denied:
                continue
            target = b if node == a else a
            if node == start and target == end and kind in DIRECT_FAMILY:
                label = DIRECT_FAMILY[kind][0 if node == a else 1]
                if edge['qualifier'] == 'step':
                    label = 'step-' + label
                key = (label, (edge['id'],))
                if key not in seen_results:
                    seen_results.add(key)
                    found.append(dict(relationship=label,path=[edge],basis='directly stated; no intermediate relatives inferred'))
            # Directly stated step/in-law/guardian/grandparent links are readable,
            # but never invent intermediate parents or blood relationships.
            if edge['qualifier'] == 'step':
                continue
            direction = ('D' if node == a else 'U') if kind == 'parent' else 'S' if kind == 'sibling' else None
            target = b if node == a else a
            if direction is None or target in seen or not any(label.startswith(pattern + direction) for label in LABELS):
                continue
            # A denied sibling claim also suppresses a shared-parent path.
            if pattern.endswith('U') and direction == 'D':
                previous_child = path[-1]['object']
                original_edges, ok = neighbors(previous_child)
                if not ok:
                    return found, False, work
                if any(e['status'] == 'denied' and e['value'] == 'sibling'
                       and {e['subject'], e['object']} == {previous_child, target}
                       and (not e['since'] or e['since'] <= as_of) and (not e['until'] or as_of <= e['until'])
                       for e in original_edges):
                    continue
            if work + len(queue) >= maximum:
                return found, False, work
            queue.append((target, pattern + direction, path + [edge], seen | {target}))
    return found, not queue, work
