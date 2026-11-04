"""On-demand, granted current topic vocabulary; no global injected catalogue.

Read only bounded note metadata, never note bodies or recall segmentation. This
is a reuse suggestion, not a semantic categoriser or a restriction on new topics.
"""
from collections import Counter
from difflib import SequenceMatcher
import hashlib
import re

from ...core.note_fields import FLAGS, tag_key, usable_topic, topic_error
from ...core.records import Failure
from .history import encoded


def catalogue(store,snapshot,args,ledger,maximum,budget=None):
    mem=store.memory
    def fits(result):
        return len(encoded(result).encode())<=maximum and (budget is None or budget.fits(result))
    with mem.bounded(ledger):
        bank=mem._check(snapshot,ledger)
        rows=mem.query(ledger,'''SELECT r.id AS record_id,r.head_revision AS id,r.head_revision FROM note_records a
            JOIN memory_records r ON r.id=a.record_id
            JOIN memory_revisions v ON v.id=r.head_revision AND v.record_id=r.id
            WHERE a.bank_id=? AND r.bank_id=? AND r.validity='current'
            AND r.readable=1 AND v.readable=1
            AND NOT EXISTS(SELECT 1 FROM excluded_memory_revisions e WHERE e.revision_id=v.id)
            ORDER BY r.id''', (snapshot.bank_id,snapshot.bank_id))
        # Stream the granted heads. Retention policy is not a discovery cutoff;
        # the shared SQL/payload/deadline ledger bounds this exact aggregation.
        counts=Counter(); proposals=[]
        for row in rows:
            value=store.notes.metadata(snapshot,row['head_revision'],ledger)
            if args.get('scope')=='tag_cleanup':
                tags=value.get('tags',[])
                kept=[]; issues=[]; review=False
                for tag in tags:
                    key=tag_key(tag); reason=topic_error(tag)
                    if reason:
                        issues.append(dict(tag=tag,reason=reason))
                        # Legacy semantic flags cannot disappear in housekeeping.
                        if key.replace('_','-') in FLAGS and key.replace('_','-') not in value.get('flags',[]): review=True
                    elif key in kept:
                        issues.append(dict(tag=tag,reason='tags_duplicate'))
                    else:
                        kept.append(key)
                if len(kept)>5:
                    issues.append(dict(reason='tags_limit')); review=True
                if issues:
                    proposal=dict(record_id=row['record_id'],revision_id=row['id'],
                        read_ref=mem._reference(snapshot,bank['handle_key'],'m','current',row),
                        issues=issues,review_required=review)
                    if not review: proposal['proposed_tags']=kept
                    proposals.append(proposal)
                continue
            if 'user-rejected' in value.get('flags',[]): continue
            counts.update({tag_key(tag) for tag in value.get('tags',[]) if usable_topic(tag)})
        q=tag_key(args['query'])
        words=set(re.findall(r'\w+',q))
        def similarity(tag):
            if not q: return 0
            if q==tag: return 3
            if q in tag or tag in q: return 2
            if words.intersection(re.findall(r'\w+',tag)): return 1
            ratio=SequenceMatcher(None,q,tag,autojunk=False).ratio()
            return ratio if ratio>=0.65 else 0
        ranked=sorted(((similarity(tag),count,tag) for tag,count in counts.items()),
            key=lambda item:(-item[0],-item[1],item[2]))
        if q: ranked=[item for item in ranked if item[0]>0]
        items=proposals if args.get('scope')=='tag_cleanup' else [dict(tag=tag,count=count) for _,count,tag in ranked]
        # Object-key order cannot invalidate an otherwise exact continuation.
        signature=hashlib.sha256(encoded({k:args[k] for k in sorted(args) if k!='cursor'}).encode()).hexdigest()
        position=0
        if args['cursor']:
            value=mem._decode(snapshot,bank['handle_key'],args['cursor'])
            if (not isinstance(value,list) or len(value)!=3 or value[:2]!=['tags',signature]
                    or type(value[2]) is not int or not 0<=value[2]<=len(items)):
                raise Failure('invalid_memory_reference','Topic cursor changed; browse again.')
            position=value[2]
        cursor=lambda n:mem._token(snapshot,bank['handle_key'],['tags',signature,n])
        result=dict(status='complete',kind='tag_cleanup' if args.get('scope')=='tag_cleanup' else 'tag_catalogue',
            results=[],delivered_count=0,total_count=len(items),next_cursor=None,suggestions_only=True)
        for index in range(position,min(len(items),position+args.get('limit',8))):
            more=index+1<len(items)
            candidate=dict(result,results=result['results']+[items[index]],
                delivered_count=len(result['results'])+1,status='page' if more else 'complete',
                next_cursor=cursor(index+1) if more else None)
            if not fits(candidate):
                result.update(status='page' if result['results'] else 'not_delivered',
                    next_cursor=cursor(index),reason='result_capacity')
                break
            result=candidate
        while result['results'] and not fits(result):
            result['results'].pop()
            result.update(delivered_count=len(result['results']),status='page' if result['results'] else 'not_delivered',
                next_cursor=cursor(position+len(result['results'])),reason='result_capacity')
        if not fits(result):
            raise Failure('memory_result_capacity','Topic catalogue envelope does not fit; no partial label delivered.')
        mem._check(snapshot,ledger)
        return result
