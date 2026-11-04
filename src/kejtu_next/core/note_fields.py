"""Optional authored-note organisation; no inferred topics or approvals."""
import re
import unicodedata

PURPOSES = ('about_user','shared_experience','knowledge','notebook','open_thread')
THREAD_STATES = ('open','parked','done')
BASES = ('unspecified','user_statement','user_summary','observation','hypothesis','reference')
FLAGS = ('coverage-partial','user-rejected')
FIELDS = frozenset(('title','purpose','tags','thread_status','basis','source_quote','correction_reason','basis_refs','flags','preference_scope'))
METADATA_FIELDS = frozenset(('title','purpose','tags','thread_status','flags','preference_scope'))

def tag_key(value):
    return unicodedata.normalize('NFKC',value).strip().casefold()

# Calendar-shaped labels, not an NLP classifier: ISO/numeric dates, bare
# modern years and English month/day/year labels. Product numbers such as
# 3090, software versions and event dates in note text remain untouched.
_MONTH = r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
_SEP = r'[\s_./-]*'
_CALENDAR = re.compile(
    r'(?<!\w)(?:19|20|21)\d{2}[-/._](?:0?[1-9]|1[0-2])(?:[-/._](?:0?[1-9]|[12]\d|3[01]))?(?!\d)'
    r'|(?<!\w)(?:0?[1-9]|[12]\d|3[01])[-/._](?:0?[1-9]|1[0-2])[-/._](?:\d{2}|(?:19|20|21)\d{2})(?!\d)'
    r'|^(?:19|20|21)\d{2}$'
    r'|^(?:19|20|21)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])$'
    r'|^(?:(?:0?[1-9]|1[0-2])[-/](?:0?[1-9]|[12]\d|3[01])|(?:0?[1-9]|[12]\d|3[01])[-/](?:0?[1-9]|1[0-2]))$'
    r'|^\d{1,4}'+_SEP+_MONTH+r'(?:'+_SEP+r'\d{1,4})?$'
    r'|^'+_MONTH+r'(?:'+_SEP+r'\d{1,4}){1,2}$'
    r'|^'+_MONTH+r'$'
    r'|^(?:today|yesterday|tomorrow)$')

def topic_error(value):
    if (not isinstance(value,str) or not tag_key(value) or '\x00' in value
            or len(value.encode())>80 or len(tag_key(value).encode())>80):
        return 'tags'
    key=tag_key(value)
    if _CALENDAR.search(key): return 'tags_date'
    if key.replace('-','_') in {*PURPOSES,*BASES,*THREAD_STATES} or key.replace('_','-') in FLAGS:
        return 'tags_state'
    return None

def usable_topic(value):
    try: return topic_error(value) is None
    except (ValueError,TypeError,UnicodeError): return False

def validate_fields(args):
    def text(value,maximum):
        return isinstance(value,str) and '\x00' not in value and len(value.encode())<=maximum
    if 'title' in args and not text(args['title'],240): raise ValueError('title')
    if 'preference_scope' in args and (not text(args['preference_scope'],240)
            or args['preference_scope'] and not args['preference_scope'].strip()):
        raise ValueError('preference_scope')
    for name,allowed in (('purpose',PURPOSES),('basis',BASES),('thread_status',THREAD_STATES)):
        if name in args and args[name] not in allowed: raise ValueError(name)
    if 'tags' in args:
        tags=args['tags']
        if not isinstance(tags,list) or len(tags)>5: raise ValueError('tags_limit')
        for tag in tags:
            reason=topic_error(tag)
            if reason: raise ValueError(reason)
        if len({tag_key(tag) for tag in tags})!=len(tags): raise ValueError('tags_duplicate')
    if 'flags' in args:
        flags=args['flags']
        if (not isinstance(flags,list) or len(flags)>len(FLAGS) or
                any(flag not in FLAGS for flag in flags) or len(set(flags))!=len(flags)):
            raise ValueError('flags')
    if 'source_quote' in args and (not text(args['source_quote'],2048) or not args['source_quote'].strip()):
        raise ValueError('source_quote')
    if 'correction_reason' in args and (not text(args['correction_reason'],256) or
            not args['correction_reason'].strip() or not args.get('source_quote') or not args.get('read_ref')):
        raise ValueError('correction_reason')
    if 'basis_refs' in args:
        refs=args['basis_refs']
        if (not isinstance(refs,list) or len(refs)>4 or
                any(not text(ref,512) or not ref for ref in refs) or len(set(refs))!=len(refs)):
            raise ValueError('basis_refs')

def field_schemas():
    return {**{name:{'type':'string'} for name in
        ('title','source_quote','correction_reason')},
        **{name:{'type':'string','enum':list(values)} for name,values in
           (('purpose',PURPOSES),('basis',BASES),('thread_status',THREAD_STATES))},
        'tags':{'type':'array','items':{'type':'string'}},
        'flags':{'type':'array','items':{'type':'string'}},
        'basis_refs':{'type':'array','items':{'type':'string'}},
        'preference_scope':{'type':'string','description':
            'Optional concrete activity where an explicitly stated standing user preference applies '
            '(for example: gathering a daily news roundup). Keep it in a short separate about_user note, '
            'basis user_statement/user_summary with source_quote. Do not promote guesses or one-off '
            'requests to preferences. Empty string removes preference status; omitted preserves it.'}}
