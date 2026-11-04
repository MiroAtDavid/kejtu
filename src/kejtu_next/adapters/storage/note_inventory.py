"""Complete keyset paging of eligible memory heads, independent of ranked search.

One identity per record: segmentation and stale derived embeddings cannot hide a
note or make a long note consume the inventory. Signed cursors bind the exact
query and authority snapshot; previews remain explicitly incomplete reads.
"""
import hashlib

from ...core.records import Failure
from .history import encoded


def page(store, snapshot, args, ledger, maximum, budget=None):
    mem = store.memory
    # JSON object order is presentation, not query identity. Model continuations
    # may reorder fields while copying the exact cursor and all filter values.
    signature = hashlib.sha256(encoded({k:args[k] for k in sorted(args) if k != 'cursor'}).encode()).hexdigest()
    ascending = args.get('order') == 'asc'
    direction, comparison = ('ASC', '>') if ascending else ('DESC', '<')
    collection = args.get('collection', 'all')
    limit = args.get('limit', 8)
    def fits(value):
        return len(encoded(value).encode()) <= maximum and (budget is None or budget.fits(value))
    with mem.bounded(ledger):
        bank = mem._check(snapshot, ledger)
        position = 0 if ascending else 2**63 - 1
        if args['cursor']:
            value = mem._decode(snapshot, bank['handle_key'], args['cursor'])
            if (not isinstance(value,list) or len(value) != 3 or value[:2] != ['inventory',signature]
                    or type(value[2]) is not int or not 0 <= value[2] < 2**63):
                raise Failure('invalid_memory_reference', 'Inventory cursor does not match this query and snapshot.')
            position = value[2]
        params = [snapshot.bank_id, position]
        where = ["r.bank_id=?", "r.validity='current'", 'r.readable=1', 'v.readable=1',
                 'v.id=r.head_revision', 'v.record_id=r.id', 'v.sequence'+comparison+'?',
                 'NOT EXISTS(SELECT 1 FROM excluded_memory_revisions e WHERE e.revision_id=v.id)']
        if args.get('scope') == 'notebook':
            where.append("(n.record_id IS NOT NULL OR h.record_id IS NOT NULL OR v.author='user' AND v.origin='manual_memory')")
        if collection == 'current':
            where.append('h.record_id IS NULL')
        elif collection == 'historical':
            where.append('h.record_id IS NOT NULL')
        metadata = 'coalesce(nm.body,hm.metadata)'
        if not (args.get('include_rejected') or args.get('flag') == 'user-rejected'):
            where.extend(['coalesce(h.rejected,0)=0',
                "NOT EXISTS(SELECT 1 FROM json_each(nm.body,'$.flags') flag WHERE flag.value='user-rejected')"])
        for field in ('purpose','thread_status'):
            if field in args:
                where.append("json_extract("+metadata+",'$."+field+"')=?")
                params.append(args[field])
        tags = args.get('tags', [args['tag']] if 'tag' in args else [])
        if tags:
            predicate = "EXISTS(SELECT 1 FROM json_each("+metadata+",'$.tags') t WHERE note_tag_key(t.value)=note_tag_key(?))"
            where.append('('+(' AND ' if args.get('tag_mode')=='all' else ' OR ').join([predicate]*len(tags))+')')
            params.extend(tags)
        if 'flag' in args:
            where.append("(EXISTS(SELECT 1 FROM json_each(nm.body,'$.flags') f WHERE f.value=?) OR "
                         "EXISTS(SELECT 1 FROM json_each(hm.metadata,'$.tags') f WHERE f.value=?))")
            params.extend([args['flag']]*2)
        for name, op in (('since','>='), ('until','<')):
            if name in args:
                where.append('v.created_at'+op+'?'); params.append(args[name])
        if args.get('conversation_id'):
            where.append('''EXISTS(SELECT 1 FROM note_revisions nr
                LEFT JOIN recall_turns t ON t.id=nr.turn_id
                LEFT JOIN note_job_origins o ON o.bank_id=nr.bank_id AND o.actor=nr.actor AND o.operation_key=nr.operation_key
                LEFT JOIN background_runs run ON run.id=o.run_id
                LEFT JOIN background_job_versions j ON j.job_id=run.job_id AND j.revision=run.revision
                WHERE nr.revision_id=v.id AND (t.conversation_id=? OR nr.turn_id IS NULL
                    AND json_extract(j.definition,'$.conversation_id')=?))''')
            params.extend([args['conversation_id']]*2)
        # The revision primary key supports continuation without OFFSET or a
        # bounded segment shortlist. SQL filters run before LIMIT.
        rows = mem.query(ledger, '''SELECT r.id,v.id AS revision_id,v.sequence FROM memory_revisions v
            JOIN memory_records r ON r.id=v.record_id
            LEFT JOIN note_records n ON n.record_id=r.id
            LEFT JOIN historical_notes h ON h.record_id=r.id
            LEFT JOIN note_metadata nm ON nm.revision_id=v.id
            LEFT JOIN historical_note_revisions hm ON hm.revision_id=v.id
            WHERE '''+' AND '.join(where)+' ORDER BY v.sequence '+direction+' LIMIT ?',
            (*params,limit+1)).fetchall()
        token = lambda sequence: mem._token(snapshot,bank['handle_key'],['inventory',signature,sequence])
        result = dict(status='complete',kind='note_inventory',mode='current',collection=collection,
                      previews_only=True,results=[],delivered_count=0,next_cursor=None,
                      coverage='eligible_records',ordering='revision_sequence_'+direction.lower())
        positions = [position]
        for index, entry in enumerate(rows[:limit]):
            row = mem._revision(snapshot,entry['id'],entry['revision_id'],ledger)
            meta = mem._meta(snapshot,row['payload_id'],'memory',ledger)
            preview = mem._page(meta,0,128,ledger)
            if preview['reason']:
                raise Failure('memory_integrity', 'Inventory preview could not be delivered exactly.')
            item = dict(record_id=row['record_id'],revision_id=row['id'],
                        read_ref=mem._reference(snapshot,bank['handle_key'],'m','current',row),
                        sha256=meta['sha256'],total_bytes=meta['bytes'],preview=preview['content'],
                        preview_offset=0,preview_end=preview['end'])
            if row['owned_note'] is not None:
                item.update(record_kind='authored_note',note=store.notes.metadata_summary(snapshot,row['id'],ledger))
            elif row['author']=='user' and row['origin']=='manual_memory':
                item.update(record_kind='user_note',author='user')
            if row['historical_note'] is not None:
                item.update(record_kind='historical_note',historical_note=store.historical_notes.summary(snapshot,row['id'],ledger))
            more = index+1 < len(rows)
            candidate = dict(result,results=result['results']+[item],delivered_count=len(result['results'])+1,
                             status='page' if more else 'complete',next_cursor=token(entry['sequence']) if more else None)
            if not fits(candidate):
                result.update(status='page' if result['results'] else 'not_delivered',
                              reason='result_capacity',next_cursor=token(positions[-1]))
                break
            result = candidate
            positions.append(entry['sequence'])
        while result['results'] and not fits(result):
            result['results'].pop(); positions.pop()
            result.update(delivered_count=len(result['results']),status='page' if result['results'] else 'not_delivered',
                          reason='result_capacity',next_cursor=token(positions[-1]))
        if not fits(result):
            raise Failure('memory_result_capacity','Inventory envelope cannot fit; no item was skipped.')
        mem._check(snapshot,ledger)
        return result
