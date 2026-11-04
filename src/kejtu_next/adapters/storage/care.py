"""Durable notebook coverage and exact groups; uses the existing job scheduler.

Only identities, hashes and outcome references are retained here. Bodies and
metadata remain with their original source owners, including after retirement.
"""
import json
import hashlib
import uuid
from datetime import timedelta

from ...core.care import ATTEMPTS, GROUP_SIZE
from ...core.jobs import JobDefinition
from ...core.records import Failure
from ...core.schedules import instant, utc
from .history import encoded

CARE_SCHEMA = '''
CREATE UNIQUE INDEX notebook_care_one ON background_jobs(kind) WHERE kind='notebook_care' AND cancelled=0;
CREATE TABLE notebook_care_state(job_id TEXT PRIMARY KEY REFERENCES background_jobs(id),
 selection INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
 retry_at TEXT, attention TEXT);
CREATE TABLE notebook_care_items(job_id TEXT NOT NULL REFERENCES background_jobs(id),
 record_id TEXT NOT NULL, revision_id TEXT NOT NULL, sequence INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','reviewed','changed','proposed','deferred','blocked')),
 group_id TEXT, PRIMARY KEY(job_id,record_id));
CREATE INDEX notebook_care_queue ON notebook_care_items(job_id,status,sequence);
CREATE TABLE notebook_care_groups(sequence INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,
 job_id TEXT NOT NULL REFERENCES background_jobs(id), run_id TEXT NOT NULL REFERENCES background_runs(id),
 manifest TEXT NOT NULL, status TEXT NOT NULL, outcome TEXT, created_at TEXT NOT NULL,
 finished_at TEXT, undone_at TEXT);
CREATE INDEX notebook_care_group_job ON notebook_care_groups(job_id,sequence);
CREATE TABLE notebook_care_retired(record_id TEXT PRIMARY KEY REFERENCES memory_records(id),
 group_id TEXT NOT NULL REFERENCES notebook_care_groups(id), revision_id TEXT NOT NULL);
CREATE TABLE notebook_care_undo(mutation_key TEXT PRIMARY KEY, group_id TEXT NOT NULL,
 outcome TEXT NOT NULL, created_at TEXT NOT NULL);
'''


class CareStore:
    def __init__(self, store):
        self.store, self.db = store, store.db

    def _run(self, run_id):
        definition = self.store.jobs.check_run(run_id)
        if definition.kind != 'notebook_care' or definition.conversation_id is not None:
            raise Failure('care_forbidden', 'Only an explicitly enabled notebook-care job can run this procedure.')
        return definition, self.store.jobs.run(run_id)

    def sync(self, run_id, snapshot, ledger):
        """Merge the current eligible revision inventory, never duplicate a sweep."""
        notes = self.store.notes
        with notes._transaction(ledger, write=True):
            definition, run = self._run(run_id)
            notes._authority(snapshot, ledger)
            job = run['job_id']
            q = lambda sql, args=(): notes._query(ledger, sql, args)
            q('INSERT OR IGNORE INTO notebook_care_state(job_id) VALUES(?)', (job,))
            q('''INSERT INTO notebook_care_items(job_id,record_id,revision_id,sequence,status)
                SELECT ?,r.id,v.id,v.sequence,'pending' FROM note_records n
                JOIN memory_records r ON r.id=n.record_id
                JOIN memory_revisions v ON v.id=r.head_revision AND v.record_id=r.id
                LEFT JOIN note_metadata m ON m.revision_id=v.id
                WHERE n.bank_id=? AND r.bank_id=? AND r.validity='current' AND r.readable=1 AND v.readable=1
                AND NOT EXISTS(SELECT 1 FROM excluded_memory_revisions e WHERE e.revision_id=v.id)
                AND NOT EXISTS(SELECT 1 FROM notebook_care_retired t WHERE t.record_id=r.id)
                AND NOT EXISTS(SELECT 1 FROM json_each(m.body,'$.flags') f WHERE f.value='user-rejected')
                ON CONFLICT(job_id,record_id) DO UPDATE SET revision_id=excluded.revision_id,
                    sequence=excluded.sequence,status='pending',group_id=NULL
                    WHERE notebook_care_items.revision_id!=excluded.revision_id''',
                (job, snapshot.bank_id, snapshot.bank_id))
            # Revoked, retired and removed sources are no longer queue inputs.
            q('''DELETE FROM notebook_care_items WHERE job_id=? AND NOT EXISTS(
                SELECT 1 FROM memory_records r JOIN memory_revisions v ON v.id=r.head_revision
                LEFT JOIN note_metadata m ON m.revision_id=v.id
                WHERE r.id=notebook_care_items.record_id AND r.head_revision=notebook_care_items.revision_id
                AND r.validity='current' AND r.readable=1 AND v.readable=1
                AND NOT EXISTS(SELECT 1 FROM excluded_memory_revisions e WHERE e.revision_id=v.id)
                AND NOT EXISTS(SELECT 1 FROM json_each(m.body,'$.flags') f WHERE f.value='user-rejected'))''', (job,))
            if definition.permissions.write_notes:
                q("UPDATE notebook_care_items SET status='pending' WHERE job_id=? AND status='proposed'", (job,))

    def select(self, run_id, snapshot, ledger):
        """Alternate oldest/newest anchors; related heads are possible overlap."""
        notes = self.store.notes
        with notes._transaction(ledger):
            _, run = self._run(run_id); job = run['job_id']
            notes._authority(snapshot, ledger)
            q = lambda sql, args=(): notes._query(ledger, sql, args)
            state = q('SELECT selection FROM notebook_care_state WHERE job_id=?', (job,)).fetchone()
            direction = 'ASC' if state[0] % 2 == 0 else 'DESC'
            anchor = q("SELECT record_id,revision_id FROM notebook_care_items WHERE job_id=? AND status='pending' ORDER BY sequence "+direction+' LIMIT 1', (job,)).fetchone()
            if anchor is None:
                return []
            selected = [dict(anchor)]
            # Repeated malformed/truncated plans get a smaller complete group,
            # never shortened source excerpts or a larger model window.
            if self.attempts(job, anchor['record_id'], anchor['revision_id'], ledger) >= 2:
                return selected
            related = q('''SELECT i.record_id,i.revision_id FROM notebook_care_items i
                JOIN note_metadata m ON m.revision_id=i.revision_id
                JOIN note_metadata a ON a.revision_id=?
                WHERE i.job_id=? AND i.record_id!=? AND i.status IN ('pending','reviewed','changed')
                AND json_extract(m.body,'$.purpose')=json_extract(a.body,'$.purpose') AND (
                 (json_extract(a.body,'$.title')!='' AND json_extract(m.body,'$.title')=json_extract(a.body,'$.title'))
                 OR EXISTS(SELECT 1 FROM json_each(m.body,'$.tags') mt JOIN json_each(a.body,'$.tags') at
                     ON note_tag_key(mt.value)=note_tag_key(at.value)))
                ORDER BY (i.status='pending') DESC,i.sequence ASC LIMIT ?''',
                (anchor['revision_id'], job, anchor['record_id'], GROUP_SIZE-1)).fetchall()
            return selected + [dict(r) for r in related]

    def attempts(self, job, record, revision, ledger):
        return self.store.notes._query(ledger, '''SELECT count(*) FROM notebook_care_groups g
            WHERE g.job_id=? AND g.status IN ('retrying','blocked')
            AND EXISTS(SELECT 1 FROM background_runs r JOIN background_jobs j ON j.id=r.job_id
                WHERE r.id=g.run_id AND r.revision=j.revision)
            AND json_extract(g.outcome,'$.reason') IN ('care_plan_invalid','care_response_incomplete','provider_context_limit')
            AND EXISTS(SELECT 1 FROM json_each(g.manifest) n
                WHERE json_extract(n.value,'$.record_id')=? AND json_extract(n.value,'$.revision_id')=?)''',
            (job, record, revision)).fetchone()[0]

    def retry(self, ident, snapshot, ledger, *, reason, now):
        group = self.group(ident)
        sources = json.loads(group['manifest'])
        attempts = max(self.attempts(group['job_id'], n['record_id'], n['revision_id'], ledger) for n in sources)
        self.record(ident, snapshot, ledger, status='retrying' if attempts+1 < ATTEMPTS else 'blocked',
            now=now, outcome=dict(reason=reason, checked=0, attempt=attempts+1))

    def read(self, snapshot, selected, ledger, *, body=True):
        """Complete source bodies or a refusal. Never a preview as coverage."""
        mem = self.store.memory
        result = []
        with mem.bounded(ledger):
            mem._check(snapshot, ledger)
            for source in selected:
                row = mem._revision(snapshot, source['record_id'], source['revision_id'], ledger)
                if row['owned_note'] is None or row['head_revision'] != source['revision_id']:
                    raise Failure('care_source_changed', 'Group no longer consists of current authored notes.')
                meta = mem._meta(snapshot, row['payload_id'], 'memory', ledger)
                metadata = self.store.notes.metadata(snapshot, row['id'], ledger)
                if 'user-rejected' in metadata.get('flags', []):
                    raise Failure('care_source_changed', 'Rejected notes cannot enter cleanup.')
                value = dict(source, sha256=meta['sha256'], total_bytes=meta['bytes'], metadata=metadata)
                if body:
                    if meta['bytes'] > ledger.payload_limit-ledger.payload_bytes-min(131072,ledger.payload_limit//4):
                        raise Failure('care_source_size', 'The complete note exceeds remaining source-work capacity.')
                    raw = mem._bytes(meta, 0, meta['bytes'], ledger)
                    if len(raw) != meta['bytes'] or hashlib.sha256(raw).hexdigest() != meta['sha256']:
                        raise Failure('memory_integrity', 'The complete note does not match its source hash.')
                    value['text'] = raw.decode('utf-8')
                result.append(value)
            mem._check(snapshot, ledger)
        return result

    def apply(self, scope, group_id, proposal, sources, ledger, now):
        from .note_care import apply
        return apply(self.store.notes, scope, group_id, proposal, sources, ledger, now)

    def undo(self, group_id, mutation_key, ledger, now):
        from .note_care import undo
        return undo(self.store.notes, group_id, mutation_key, ledger, now)

    def begin(self, run_id, snapshot, sources, ledger, now):
        notes = self.store.notes
        with notes._transaction(ledger, write=True):
            _, run = self._run(run_id)
            notes._authority(snapshot, ledger)
            count = notes._query(ledger,'SELECT count(*) FROM notebook_care_groups').fetchone()[0]
            if count >= notes.policy.outcomes:
                raise Failure('care_history_capacity', 'Cleanup receipt capacity is exhausted; originals and recovery remain intact.')
            ident = uuid.uuid4().hex
            manifest = [{k:n[k] for k in ('record_id','revision_id','sha256','total_bytes')} for n in sources]
            notes._query(ledger, '''INSERT INTO notebook_care_groups(id,job_id,run_id,manifest,status,created_at)
                VALUES(?,?,?,?,'running',?)''', (ident, run['job_id'], run_id, encoded(manifest), now))
            notes._query(ledger, 'UPDATE notebook_care_state SET selection=selection+1 WHERE job_id=?', (run['job_id'],))
            return ident

    def group(self, ident):
        row = self.db.execute('SELECT * FROM notebook_care_groups WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise Failure('care_group_missing', 'The selected cleanup group is unavailable.')
        return dict(row)

    def record(self, ident, snapshot, ledger, *, status, now, outcome=None):
        """Revision checked checkpoint, also the lost-response reconciliation key."""
        notes = self.store.notes
        with notes._transaction(ledger, write=True):
            group = self.group(ident)
            self._run(group['run_id']); notes._authority(snapshot, ledger)
            if group['status'] != 'running':
                return group
            for item in json.loads(group['manifest']):
                row = self.store.memory._revision(snapshot, item['record_id'], item['revision_id'], ledger)
                if row['head_revision'] != item['revision_id']:
                    raise Failure('care_source_changed', 'A note changed before cleanup checkpoint.')
            self._checkpoint(group, ledger, status, now, outcome or {})
            return self.group(ident)

    def _checkpoint(self, group, ledger, status, now, outcome):
        q = lambda sql, args=(): self.store.notes._query(ledger, sql, args)
        for item in json.loads(group['manifest']):
            q('''UPDATE notebook_care_items SET status=?,group_id=?
                WHERE job_id=? AND record_id=? AND revision_id=?''',
                ('pending' if status=='retrying' else status, group['id'], group['job_id'], item['record_id'], item['revision_id']))
        q('UPDATE notebook_care_groups SET status=?,outcome=?,finished_at=? WHERE id=?',
            (status, encoded(outcome), now, group['id']))
        q('UPDATE notebook_care_state SET failures=0,retry_at=NULL,attention=NULL WHERE job_id=?', (group['job_id'],))

    def settled(self, run, status, error, now):
        """Inside JobStore's terminal transaction; no second scheduler or reset."""
        job = run['job_id']
        if self.store.jobs.definition(job, run['revision']).kind != 'notebook_care':
            return
        self.db.execute('INSERT OR IGNORE INTO notebook_care_state(job_id) VALUES(?)', (job,))
        self.db.execute("UPDATE notebook_care_groups SET status='interrupted',finished_at=? WHERE run_id=? AND status='running'", (now, run['id']))
        if status == 'failed':
            state = self.db.execute('SELECT failures FROM notebook_care_state WHERE job_id=?', (job,)).fetchone()
            failures = state[0]+1
            self.db.execute('UPDATE notebook_care_state SET failures=?,retry_at=?,attention=? WHERE job_id=?',
                (failures, utc(instant(now)+timedelta(minutes=15*2**min(failures-1,4))),
                 error if failures >= ATTEMPTS or error=='care_history_capacity' else None, job))

    def continuation(self, now):
        """Called by JobStore.poll inside its reservation transaction."""
        for row in self.db.execute('''SELECT j.id,j.revision FROM background_jobs j JOIN notebook_care_state c ON c.job_id=j.id
                WHERE j.kind='notebook_care' AND j.enabled=1 AND j.cancelled=0 AND c.attention IS NULL
                AND (c.retry_at IS NULL OR c.retry_at<=?)
                AND EXISTS(SELECT 1 FROM notebook_care_items i WHERE i.job_id=j.id AND i.status='pending')
                AND NOT EXISTS(SELECT 1 FROM background_runs r WHERE r.job_id=j.id AND r.status IN ('pending','running','cancel_requested'))''', (now,)).fetchall():
            self.db.execute('''INSERT OR IGNORE INTO background_runs(id,job_id,revision,scheduled_at,covered_through,created_at,status)
                VALUES(?,?,?,?,?,?,'pending')''', (uuid.uuid4().hex, row['id'], row['revision'], now, now, now))

    def status(self, job_id):
        definition = self.store.jobs.definition(job_id)
        if definition.kind != 'notebook_care':
            raise Failure('care_forbidden', 'Select a notebook-care job.')
        counts = dict(self.db.execute('''SELECT coalesce(i.status,'pending'),count(*) FROM note_records n
            JOIN memory_records r ON r.id=n.record_id JOIN memory_revisions v ON v.id=r.head_revision
            LEFT JOIN notebook_care_items i ON i.job_id=? AND i.record_id=r.id AND i.revision_id=v.id
            LEFT JOIN note_metadata m ON m.revision_id=v.id
            WHERE r.validity='current' AND r.readable=1 AND v.readable=1
            AND NOT EXISTS(SELECT 1 FROM excluded_memory_revisions e WHERE e.revision_id=v.id)
            AND NOT EXISTS(SELECT 1 FROM json_each(m.body,'$.flags') f WHERE f.value='user-rejected')
            GROUP BY coalesce(i.status,'pending')''', (job_id,)))
        state = self.db.execute('SELECT * FROM notebook_care_state WHERE job_id=?', (job_id,)).fetchone()
        running = self.db.execute("SELECT 1 FROM background_runs WHERE job_id=? AND status='running'", (job_id,)).fetchone()
        attention = state['attention'] if state else None
        status = ('needs_attention' if attention or counts.get('blocked') or counts.get('deferred')
            else 'working' if running else 'continuing_later' if counts.get('pending') or state is None else 'up_to_date')
        return dict(status=status, counts=counts, attention=attention, retry_at=state['retry_at'] if state else None,
                    scope='eligible_current_authored_revisions', enabled=definition.enabled)

    def changes(self, job_id, *, before=None, limit=20):
        self.status(job_id)
        if type(limit) is not int or not 1<=limit<=50 or before is not None and (type(before) is not int or before<1):
            raise Failure('care_page', 'Use a bounded exact change cursor.')
        rows = self.db.execute('''SELECT * FROM notebook_care_groups WHERE job_id=?
            AND (? IS NULL OR sequence<?) ORDER BY sequence DESC LIMIT ?''', (job_id,before,before,limit+1)).fetchall()
        items = []
        for row in rows[:limit]:
            outcome = json.loads(row['outcome']) if row['outcome'] else {}
            sources = json.loads(row['manifest'])
            items.append(dict(id=row['id'], sequence=str(row['sequence']), status=row['status'], created_at=row['created_at'],
                checked=len(sources) if row['status'] in ('changed','reviewed','proposed') else outcome.get('checked',0),
                revised=outcome.get('revised',0), retired=outcome.get('retired',0), produced=outcome.get('produced',0),
                reason=outcome.get('reason'), undone_at=row['undone_at'],
                sources=[dict(record_id=n['record_id'],revision_id=n['revision_id']) for n in sources],
                outputs=[dict(record_id=n['record_id'],revision_id=n['revision_id']) for n in outcome.get('after',[]) if n['readable']]))
        return dict(items=items,next_cursor=str(rows[limit-1]['sequence']) if len(rows)>limit else None)

    def body(self, group_id, record_id, revision_id, offset, maximum, ledger):
        """Explicit local history only; ordinary recall cannot read retirement."""
        if type(offset) is not int or offset<0 or type(maximum) is not int or not 1<=maximum<=32768:
            raise Failure('care_page', 'Use exact byte offsets and a bounded history page.')
        group = self.group(group_id)
        refs = json.loads(group['manifest'])
        outcome = json.loads(group['outcome']) if group['outcome'] else {}
        refs += outcome.get('before',[]) + outcome.get('after',[])
        if not any(n['record_id']==record_id and n['revision_id']==revision_id for n in refs):
            raise Failure('care_source_missing', 'This exact source is not part of the selected group.')
        mem = self.store.memory
        with mem.bounded(ledger):
            row = mem.query(ledger, '''SELECT v.payload_id,r.bank_id FROM memory_revisions v JOIN memory_records r ON r.id=v.record_id
                WHERE r.id=? AND v.id=? AND v.readable=1 AND r.validity='current'
                AND (r.readable=1 OR EXISTS(SELECT 1 FROM notebook_care_retired t WHERE t.record_id=r.id))
                AND NOT EXISTS(SELECT 1 FROM excluded_memory_revisions e WHERE e.revision_id=v.id)''', (record_id,revision_id)).fetchone()
            if row is None:
                raise Failure('care_source_revoked', 'Removed or revoked material is unavailable in cleanup history.')
            # Local notebook history uses explicit user access, like manual
            # notebook browsing. It does not issue reusable model read handles.
            meta = mem.query(ledger,'SELECT * FROM memory_payloads WHERE id=? AND bank_id=?', (row['payload_id'],row['bank_id'])).fetchone()
            if meta is None: raise Failure('memory_integrity', 'The exact source payload is unavailable.')
            result = mem._page(meta,offset,maximum,ledger)
            result.pop('verification',None)
            return dict(result,reference_id=revision_id)
