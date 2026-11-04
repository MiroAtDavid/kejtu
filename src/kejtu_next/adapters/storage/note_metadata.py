"""Revision-owned note organisation, attribution and correction evidence.

No extracted assertions and no global correction table. These fields describe
one authored record; unrelated notes are never rewritten as a side effect.
"""
import hashlib
import json
import re
from ...core.note_fields import FIELDS, tag_key
from ...core.records import Failure
from .history import encoded

SCHEMA = '''
CREATE TABLE note_metadata(revision_id TEXT PRIMARY KEY REFERENCES note_revisions(revision_id) ON DELETE CASCADE,
 bank_id TEXT NOT NULL REFERENCES memory_banks(id), body TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE INDEX note_metadata_scope ON note_metadata(bank_id,json_extract(body,'$.purpose'),json_extract(body,'$.thread_status'));
'''
for event,rows in (('INSERT',('NEW',)),('UPDATE',('OLD','NEW')),('DELETE',('OLD',))):
    SCHEMA += f'''CREATE TRIGGER note_metadata_{event.lower()} AFTER {event} ON note_metadata BEGIN
        UPDATE memory_banks SET epoch=epoch+1 WHERE id IN ({','.join(r+'.bank_id' for r in rows)});
        DELETE FROM recall_segments WHERE kind='memory' AND source_id IN ({','.join(r+'.revision_id' for r in rows)});
        INSERT OR IGNORE INTO recall_dirty SELECT 'memory',revision_id FROM memory_index
            WHERE revision_id IN ({','.join(r+'.revision_id' for r in rows)});
        END;\n'''

def load(notes,snapshot,revision,ledger):
    size=notes._query(ledger,'SELECT length(CAST(body AS BLOB)) FROM note_metadata WHERE revision_id=? AND bank_id=?',
        (revision,snapshot.bank_id)).fetchone()
    if size is None: return {}
    if not 0<size[0]<=32768:
        raise Failure('memory_integrity','Note organisation metadata exceeds its bounded envelope.')
    ledger.spend_payload(size[0])
    row=notes._query(ledger,'SELECT body,sha256 FROM note_metadata WHERE revision_id=? AND bank_id=?',
        (revision,snapshot.bank_id)).fetchone()
    if row is None: return {}
    if hashlib.sha256(row['body'].encode()).hexdigest()!=row['sha256']:
        raise Failure('memory_integrity','Note organisation metadata changed.')
    return json.loads(row['body'])

def original_quote(quote,body):
    # Old notebook accepted whitespace differences, preserving original bytes.
    parts=re.split(r'\s+',quote.strip())
    found=re.search(r'\s+'.join(re.escape(part) for part in parts),body)
    if found is None:
        raise Failure('note_quote_invalid','source_quote must match the current user message; ordinary authored saves need no quote.')
    exact=found.group()
    if len(exact.encode())>2048:
        raise Failure('note_quote_invalid','Use a shorter exact source_quote; original whitespace exceeded the quote bound.')
    return exact

def prepare(notes,snapshot,args,origin,ledger):
    parent=args.get('expected_revision')
    previous=load(notes,snapshot,parent,ledger) if parent else {}
    supplied=args.get('fields',{})
    if not previous and not (set(supplied)&FIELDS):
        return {},False
    value=dict(title='',purpose='notebook',tags=[],thread_status=None,basis='unspecified')|previous
    for name in ('title','purpose','tags','thread_status','basis','flags','preference_scope'):
        if name in supplied: value[name]=supplied[name]
    if 'tags' in supplied: value['tags']=[tag_key(tag) for tag in supplied['tags']]
    if value['purpose']=='open_thread':
        value['thread_status']=value.get('thread_status') or 'open'
    else:
        if 'thread_status' in supplied:
            raise Failure('note_thread_status_invalid','Only open_thread notes have thread_status; no note changed.')
        value['thread_status']=None
    if supplied.get('source_quote'):
        if origin['user_id'] is None:
            raise Failure('note_quote_invalid', 'Private review is not a new user statement or user correction; use independent authorship and deliberate source links.')
        body=notes.store._history.body(origin['user_id'],'user_message',ledger,4*ledger.profile.context_limit)
        quote=original_quote(supplied['source_quote'],body)
        value['attribution']={'source_quote':quote,'artifact_id':origin['user_id']}
    if value['basis'] in ('user_statement','user_summary'):
        if not value.get('attribution'):
            raise Failure('note_quote_invalid','A user-attributed note needs an exact source_quote; your own ideas do not.')
        if (args['operation']!='metadata' and value['basis']=='user_statement'
                and ' '.join(args.get('text','').split())!=' '.join(value['attribution']['source_quote'].split())):
            value['basis']='user_summary'
    else:
        value.pop('attribution',None)
    if value.get('preference_scope') and (value['purpose'] != 'about_user'
            or value['basis'] not in ('user_statement','user_summary') or not value.get('attribution')):
        raise Failure('note_preference_invalid',
            'A scoped preference needs purpose about_user and an explicitly sourced user statement or summary. '
            'For an ordinary note omit preference_scope, or clear an existing scope with an empty string.')
    # The exact same user action has one lifecycle owner. This does not gate
    # independent observations or ordinary edits of an existing open thread.
    attribution=value.get('attribution')
    if (value['purpose']=='open_thread' and attribution and
            (previous.get('purpose')!='open_thread' or previous.get('attribution')!=attribution)):
        if notes._query(ledger,'''SELECT 1 FROM personal_task_sources
                WHERE user_id=? AND quote=?''',
                (attribution['artifact_id'],attribution['source_quote'])).fetchone():
            raise Failure('task_action_owned','This user action already belongs to a task; no duplicate open thread was created.')
    if supplied.get('correction_reason'):
        value['correction']={'reason':supplied['correction_reason'],'source_quote':quote,
            'artifact_id':origin['user_id'],'from_revision':parent}
        value['change_kind']='user_correction'
    else:
        value['change_kind']='created' if not parent else 'edited'
    # Change markers alone cannot manufacture revisions for a repeated save.
    before={k:v for k,v in previous.items() if k!='change_kind'}
    after={k:v for k,v in value.items() if k!='change_kind'}
    return value,before!=after

def save(notes,snapshot,revision,value,ledger):
    if not value: return
    if value.get('change_kind')=='user_correction':
        value['correction']=dict(value['correction'],to_revision=revision)
    body=encoded(value)
    if len(body.encode())>32768:
        raise Failure('note_fields_invalid','Note metadata exceeds its bounded envelope; no change committed.')
    ledger.spend_payload(len(body.encode()))
    notes._query(ledger,'INSERT INTO note_metadata VALUES(?,?,?,?)',
        (revision,snapshot.bank_id,body,hashlib.sha256(body.encode()).hexdigest()))

def summary(value):
    if not value: return {}
    result={k:value[k] for k in ('title','purpose','tags','thread_status','basis','change_kind')}
    if value.get('correction'):
        result['last_correction_revision']=value['correction']['to_revision']
    if value.get('flags'): result['flags']=value['flags']
    if value.get('preference_scope'): result['preference_scope']=value['preference_scope']
    return result
