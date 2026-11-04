"""Atomic notebook-care effects through NoteStore's revision/provenance owners.

The group receipt commits with every affected head and retirement. Undo compares
the whole resulting state before any effect; it never overwrites a later edit.
No original payload is copied into the cleanup journal or permanently erased.
"""
from dataclasses import replace
import hashlib
import json
import uuid

from ...core.notes import ACTOR
from ...core.records import Failure
from .history import encoded
from . import note_metadata


def apply(notes, scope, group_id, proposal, sources, ledger, now):
    from ...core.care import plan
    from ...core.records import Reply
    proposal = plan(Reply(encoded(proposal), 'stop'), sources, revision_bytes=notes.policy.revision_bytes)
    care, snapshot = notes.store.care, scope.memory
    with notes._transaction(ledger, write=True):
        group = care.group(group_id)
        definition, run = care._run(scope.job_run_id)
        bank = notes._authority(snapshot, ledger, write_epoch=scope.write_epoch)
        if group['run_id'] != scope.job_run_id or not definition.permissions.write_notes:
            raise Failure('care_forbidden', 'The group has no matching automatic-cleanup authority.')
        if group['status'] != 'running':
            return group, snapshot  # Already committed; never replay an uncertain effect.
        if bank['index_epoch'] != snapshot.epoch:
            raise Failure('note_index_stale', 'Cleanup cannot certify an already stale source discovery index.')
        origin = notes._scope_origin(scope, ledger)
        expected = json.loads(group['manifest'])
        if [{k:n[k] for k in ('record_id','revision_id','sha256','total_bytes')} for n in sources] != expected:
            raise Failure('care_source_changed', 'Cleanup input identity changed.')
        q = lambda sql, args=(): notes._query(ledger, sql, args)
        by_id = {n['record_id']:n for n in sources}
        before = []
        for item in expected:
            row = notes.memory._revision(snapshot, item['record_id'], item['revision_id'], ledger)
            if row['head_revision'] != item['revision_id'] or row['owned_note'] is None or row['validity'] != 'current':
                raise Failure('care_source_changed', 'A selected note was edited or revoked before commit.')
            metadata = notes.metadata(snapshot, row['id'], ledger)
            if 'user-rejected' in metadata.get('flags', []):
                raise Failure('care_source_changed', 'Rejected notes cannot be cleaned up.')
            meta = notes.memory._meta(snapshot, row['payload_id'], 'memory', ledger)
            if meta['sha256'] != item['sha256']:
                raise Failure('memory_integrity', 'Selected source hash changed.')
            source = by_id[item['record_id']]
            if hashlib.sha256(source['text'].encode()).hexdigest() != item['sha256'] or source['metadata'] != metadata:
                raise Failure('memory_integrity', 'Cleanup sources do not match the complete admitted originals.')
            before.append(dict(record_id=item['record_id'], revision_id=item['revision_id'], readable=1))
        outputs = []
        for output in proposal['outputs']:
            source = by_id[output['target'] or output['sources'][0]]
            fields = dict(source['metadata'])
            if not fields:
                fields = dict(title='', purpose='notebook', tags=[], thread_status=None, basis='reference')
            fields.update(title=output['title'], tags=output['tags'], change_kind='edited' if output['target'] else 'created')
            flags = sorted({flag for ident in output['sources'] for flag in by_id[ident]['metadata'].get('flags', [])})
            if flags: fields['flags'] = flags
            # Output authorship remains the assistant's; originals carry their
            # exact quotes/attribution through immutable explicit basis links.
            fields.pop('attribution', None); fields.pop('correction', None)
            fields['basis'] = 'reference'
            raw = output['text'].encode()
            if output['target'] and raw == source['text'].encode() and all(
                    fields.get(k) == source['metadata'].get(k) for k in ('title','tags','flags')):
                continue  # A semantic no-op must not manufacture a revision.
            outputs.append((output, raw, fields))
        count, retained = q('''SELECT count(*),coalesce(sum(p.bytes),0) FROM note_revisions n
            JOIN memory_revisions v ON v.id=n.revision_id JOIN memory_payloads p ON p.id=v.payload_id
            WHERE n.bank_id=?''', (snapshot.bank_id,)).fetchone()
        records = q('SELECT count(*) FROM note_records WHERE bank_id=?', (snapshot.bank_id,)).fetchone()[0]
        outcomes = q('SELECT count(*) FROM note_outcomes WHERE bank_id=?', (snapshot.bank_id,)).fetchone()[0]
        if (count+len(outputs) > notes.policy.revisions or retained+sum(len(raw) for _,raw,_ in outputs) > notes.policy.retained_bytes
                or records+sum(o['target'] is None for o,_,_ in outputs) > notes.policy.notes
                or outcomes+len(outputs) > notes.policy.outcomes
                or any(len(raw)>notes.policy.revision_bytes for _,raw,_ in outputs)):
            raise Failure('note_quota', 'The whole recoverable cleanup group exceeds retention capacity; nothing changed.')
        after = {v['record_id']:dict(v) for v in before}
        new_ids = []
        for index, (output, raw, fields) in enumerate(outputs):
            ledger.spend_payload(4*len(raw))
            target = output['target']; rid = target or uuid.uuid4().hex; revision = uuid.uuid4().hex
            key = group_id+'-'+str(index)
            args = dict(operation='revise' if target else 'create', operation_key=key,
                reason='Automatic notebook care', expected_revision=by_id[target]['revision_id'] if target else None)
            bases = [('m', ident, by_id[ident]['revision_id'], None, by_id[ident]['sha256']) for ident in output['sources']]
            # Retain existing evidence associations as well as complete inputs.
            for ident in output['sources']:
                for base in q('SELECT kind,record_id,memory_revision,source_revision,sha256 FROM note_bases WHERE revision_id=? ORDER BY ordinal',
                              (by_id[ident]['revision_id'],)).fetchall():
                    if tuple(base) not in bases: bases.append(tuple(base))
            if len(bases)>32:
                raise Failure('care_provenance_limit', 'Source associations exceed a bounded group; no links were dropped.')
            notes._revision(snapshot, None, args, origin, rid, revision, raw, bases, ledger)
            for ident in output['sources']:
                # This transformation knowingly retains source information.
                # The existing permanent-erasure owner must see that copy link,
                # including paraphrases that a whole-body text check cannot find.
                q('INSERT INTO memory_edges(revision_id,source_revision_id,relation) VALUES(?,?,?)',
                    (revision, by_id[ident]['revision_id'], 'contains_sensitive_copy_of'))
            note_metadata.save(notes, snapshot, revision, fields, ledger)
            epoch = q('SELECT epoch FROM memory_banks WHERE id=?', (snapshot.bank_id,)).fetchone()[0]
            receipt = dict(status='committed', operation_key=key, note_id=rid, revision_id=revision,
                sha256=hashlib.sha256(raw).hexdigest(), bank_epoch=epoch)
            body = encoded(receipt)
            q('INSERT INTO note_outcomes VALUES(?,?,?,?,?,?,?)', (snapshot.bank_id, ACTOR, key,
                hashlib.sha256(encoded(output).encode()).hexdigest(), None, body, hashlib.sha256(body.encode()).hexdigest()))
            q('INSERT INTO note_job_origins VALUES(?,?,?,?,?)',
                (snapshot.bank_id, ACTOR, key, scope.job_run_id, origin['event_sequence']))
            after[rid] = dict(record_id=rid, revision_id=revision, readable=1)
            if target is None: new_ids.append(rid)
        for rid in proposal['retire']:
            q('UPDATE memory_records SET readable=0 WHERE id=?', (rid,))
            q('INSERT INTO notebook_care_retired VALUES(?,?,?)', (rid, group_id, by_id[rid]['revision_id']))
            after[rid]['readable'] = 0
        status = 'changed' if outputs or proposal['retire'] else 'reviewed'
        affected = {o['target'] for o,_,_ in outputs if o['target']} | set(proposal['retire'])
        outcome = dict(before=[i for i in before if i['record_id'] in affected],
            after=[i for i in after.values() if i['record_id'] in affected or i['record_id'] in new_ids], created=new_ids,
            revised=len(outputs)-len(new_ids), retired=len(proposal['retire']), produced=len(new_ids))
        care._checkpoint(group, ledger, status, now, outcome)
        # The produced revisions are already covered. They do not trigger the
        # next daily cycle; a later independent head revision does.
        for item in after.values():
            if item['readable']:
                sequence = q('SELECT sequence FROM memory_revisions WHERE id=?', (item['revision_id'],)).fetchone()[0]
                q('''INSERT INTO notebook_care_items VALUES(?,?,?,?,?,?)
                    ON CONFLICT(job_id,record_id) DO UPDATE SET revision_id=excluded.revision_id,
                    sequence=excluded.sequence,status=excluded.status,group_id=excluded.group_id''',
                    (group['job_id'],item['record_id'],item['revision_id'],sequence,status,group_id))
            else:
                q('DELETE FROM notebook_care_items WHERE job_id=? AND record_id=?', (group['job_id'],item['record_id']))
        epoch = q('SELECT epoch FROM memory_banks WHERE id=?', (snapshot.bank_id,)).fetchone()[0]
        q('UPDATE memory_banks SET index_epoch=? WHERE id=?', (epoch, snapshot.bank_id))
        advanced = replace(snapshot, epoch=epoch)
        notes._authority(advanced, ledger, write_epoch=scope.write_epoch)
        notes._scope_origin(replace(scope, memory=advanced), ledger)
        return care.group(group_id), advanced


def undo(notes, group_id, mutation_key, ledger, now):
    """Explicit local user action, separate from model job permissions."""
    from .jobs import identity
    identity(group_id); identity(mutation_key)
    care = notes.store.care
    with notes._transaction(ledger, write=True):
        q = lambda sql, args=(): notes._query(ledger, sql, args)
        if notes.store.active_turn_id() is not None or q("SELECT 1 FROM background_runs WHERE status IN ('running','cancel_requested')").fetchone():
            raise Failure('care_busy', 'Wait for active work to finish before Undo.')
        prior = q('SELECT * FROM notebook_care_undo WHERE mutation_key=?', (mutation_key,)).fetchone()
        if prior:
            if prior['group_id'] != group_id:
                raise Failure('care_undo_conflict', 'This Undo key belongs to another group.')
            return json.loads(prior['outcome'])
        group = care.group(group_id)
        if group['status'] != 'changed' or not group['outcome']:
            raise Failure('care_undo_unavailable', 'This group has no recoverable committed change.')
        if group['undone_at']:
            return dict(status='already_undone', group_id=group_id)
        outcome = json.loads(group['outcome'])
        for item in outcome['after']:
            row = q('SELECT head_revision,readable,validity FROM memory_records WHERE id=?', (item['record_id'],)).fetchone()
            if row is None or tuple(row) != (item['revision_id'],item['readable'],'current'):
                raise Failure('care_undo_conflict', 'A later edit affects this group; Undo changed nothing.')
            retired = q('SELECT group_id FROM notebook_care_retired WHERE record_id=?', (item['record_id'],)).fetchone()
            if not item['readable'] and (retired is None or retired[0] != group_id):
                raise Failure('care_undo_conflict', 'Retirement ownership changed; nothing restored.')
        for item in outcome['before'] + outcome['after']:
            row = q('''SELECT readable FROM memory_revisions v WHERE v.id=? AND v.record_id=?
                AND NOT EXISTS(SELECT 1 FROM excluded_memory_revisions e WHERE e.revision_id=v.id)''',
                (item['revision_id'],item['record_id'])).fetchone()
            if row is None or row[0] != 1:
                raise Failure('care_undo_revoked', 'Removed or revoked source material cannot be restored.')
        bank = q('SELECT epoch,index_epoch FROM memory_banks WHERE slot=1').fetchone()
        if bank is None or bank['index_epoch'] != bank['epoch']:
            raise Failure('note_index_stale', 'Undo cannot certify an already stale source discovery index.')
        for item in outcome['before']:
            q('UPDATE memory_records SET head_revision=?,readable=1 WHERE id=?', (item['revision_id'],item['record_id']))
            q('DELETE FROM notebook_care_retired WHERE record_id=?', (item['record_id'],))
        for rid in outcome['created']:
            revision = next(i['revision_id'] for i in outcome['after'] if i['record_id']==rid)
            q('UPDATE memory_records SET readable=0 WHERE id=?', (rid,))
            q('INSERT INTO notebook_care_retired VALUES(?,?,?)', (rid,group_id,revision))
        # Undo itself is a covered local decision, so daily care does not
        # immediately repeat the rejected transformation of unchanged inputs.
        for item in outcome['before']:
            sequence = q('SELECT sequence FROM memory_revisions WHERE id=?', (item['revision_id'],)).fetchone()[0]
            q('''INSERT INTO notebook_care_items VALUES(?,?,?,?,'reviewed',?)
                ON CONFLICT(job_id,record_id) DO UPDATE SET revision_id=excluded.revision_id,
                sequence=excluded.sequence,status='reviewed',group_id=excluded.group_id''',
                (group['job_id'],item['record_id'],item['revision_id'],sequence,group_id))
        for rid in outcome['created']:
            q('DELETE FROM notebook_care_items WHERE record_id=?', (rid,))
        q('UPDATE notebook_care_groups SET undone_at=? WHERE id=?', (now,group_id))
        q('UPDATE memory_banks SET index_epoch=epoch WHERE slot=1')
        result = dict(status='undone', group_id=group_id)
        q('INSERT INTO notebook_care_undo VALUES(?,?,?,?)', (mutation_key,group_id,encoded(result),now))
        return result
