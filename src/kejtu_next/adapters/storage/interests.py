"""Source-bound interest state and delivery receipts; the existing job owns work."""
from datetime import timedelta
import hashlib
import json
import uuid

from ...core.interests import KIND, MAX_TOPICS, MAX_NOTE_BYTES, MAX_SOURCE_BYTES, MENTION_GAP, TOPIC_GAP, instant, opening, reference_link
from ...core.records import Failure
from .history import encoded
from .research import ResearchWebSources

INTEREST_SCHEMA = '''
CREATE UNIQUE INDEX interest_job_one ON background_jobs(kind) WHERE kind='interest_following' AND cancelled=0;
CREATE TABLE interest_scan(singleton INTEGER PRIMARY KEY CHECK(singleton=1), sequence INTEGER NOT NULL);
INSERT INTO interest_scan VALUES(1,0);
CREATE TABLE interests(id TEXT PRIMARY KEY, note_id TEXT NOT NULL UNIQUE REFERENCES memory_records(id) ON DELETE CASCADE,
 revision_id TEXT NOT NULL REFERENCES memory_revisions(id) ON DELETE CASCADE,
 source_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE, source_sha256 TEXT NOT NULL,
 quote TEXT NOT NULL, topic TEXT NOT NULL, topic_key TEXT NOT NULL, category TEXT NOT NULL, query_kind TEXT NOT NULL,
 stopped INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1,
 next_at TEXT NOT NULL, checked_at TEXT, mentioned_at TEXT, created_at TEXT NOT NULL);
CREATE INDEX interest_topic ON interests(topic_key);
CREATE TABLE interest_findings(id TEXT PRIMARY KEY, interest_id TEXT NOT NULL REFERENCES interests(id) ON DELETE CASCADE,
 source_id TEXT NOT NULL REFERENCES research_web_sources(id) ON DELETE CASCADE,
 identity TEXT NOT NULL, body TEXT NOT NULL, checked_at TEXT NOT NULL, expires_at TEXT NOT NULL,
 UNIQUE(interest_id,identity));
CREATE TABLE interest_offers(turn_id TEXT PRIMARY KEY REFERENCES turns(id) ON DELETE CASCADE,
 finding_id TEXT NOT NULL,
 reference_id TEXT NOT NULL UNIQUE, admission TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('offered','unused','mentioned','possibly_seen')), created_at TEXT NOT NULL);
CREATE INDEX interest_offer_finding ON interest_offers(finding_id,status);
CREATE TABLE reply_references(turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
 answer_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
 reference_id TEXT NOT NULL REFERENCES interest_offers(reference_id) ON DELETE CASCADE,
 record TEXT NOT NULL, PRIMARY KEY(turn_id,reference_id));
CREATE TABLE interest_stops(topic_key TEXT PRIMARY KEY, note_id TEXT NOT NULL, source_id TEXT NOT NULL, stopped_at TEXT NOT NULL);
CREATE TABLE interest_reviewed(revision_id TEXT PRIMARY KEY REFERENCES memory_revisions(id) ON DELETE CASCADE, checked_at TEXT NOT NULL);
CREATE TABLE interest_controls(mutation_key TEXT PRIMARY KEY, intent_sha256 TEXT NOT NULL, receipt TEXT NOT NULL);
'''


class InterestStore:
    def __init__(self, store):
        self.store, self.db = store, store.db

    def enabled(self):
        row = self.db.execute('SELECT id FROM background_jobs WHERE kind=? AND enabled=1 AND cancelled=0', (KIND,)).fetchone()
        return self.store.jobs.definition(row[0]) if row else None

    def context_epoch(self):
        # Preserve individual values: a revision increment and disable must not
        # cancel each other out. Status changes and expiry also invalidate reuse.
        return (tuple(tuple(r) for r in self.db.execute("SELECT id,revision,enabled,cancelled FROM background_jobs WHERE kind='interest_following' ORDER BY id")),
                tuple(tuple(r) for r in self.db.execute('SELECT id,revision,stopped,next_at,mentioned_at FROM interests ORDER BY id')))

    def guard(self, definition, **unused):
        if (definition.kind != KIND or definition.conversation_id is not None
                or not definition.enabled or not definition.permissions.read_memory
                or definition.permissions.write_notes or definition.permissions.deliver):
            raise Failure('interest_forbidden', 'Following interests requires its own saved read-only permission.')
        if not self.store.web.settings()['enabled']:
            raise Failure('web_forbidden', 'Enable web access before following interests.')

    def web_sources(self, run_id):
        # Same immutable snapshots, public-page boundary and provider accounting
        # as assigned research; only the explicit authority owner differs.
        return ResearchWebSources(self, run_id)

    def _query(self, ledger, sql, parameters=()):
        return self.store.memory.query(ledger, sql, parameters)

    def _source(self, source_id, bank_id, ledger):
        row = self._query(ledger, '''SELECT a.id,a.sha256,a.bytes FROM artifacts a
            JOIN recall_turns t ON t.user_id=a.id JOIN memory_grants g ON g.conversation_id=t.conversation_id
            WHERE a.id=? AND a.kind='user_message' AND t.status='completed' AND g.enabled=1 AND g.bank_id=?''',
            (source_id, bank_id)).fetchone()
        if row is None or row['bytes'] > MAX_SOURCE_BYTES:
            return None
        body = self.store._history.body(source_id, 'user_message', ledger, MAX_SOURCE_BYTES)
        return dict(source_id=source_id, sha256=row['sha256'], text=body)

    def _support(self, snapshot, note_id, revision_id, ledger, depth=0):
        if depth > 2:
            return []
        memory, notes = self.store.memory, self.store.notes
        row = memory._revision(snapshot, note_id, revision_id, ledger)
        if row['head_revision'] != revision_id or row['validity'] != 'current':
            return []
        fields = notes.metadata(snapshot, revision_id, ledger)
        if 'user-rejected' in fields.get('flags', []):
            return []
        sources = []
        attribution = fields.get('attribution', {})
        if fields.get('basis') in ('user_statement', 'user_summary') and attribution.get('artifact_id'):
            source = self._source(attribution['artifact_id'], snapshot.bank_id, ledger)
            quote = attribution.get('source_quote')
            if source and isinstance(quote, str) and quote and quote in source['text']:
                # Verify the whole immutable original locally, send only its
                # already-authored attribution span to the configured model.
                sources.append(dict(source, text=quote))
        # Reflection may deliberately link a supported note or its original.
        # It cannot manufacture user attribution, and an unlinked reflection
        # remains ineligible. Never inspect unrelated archive conversations.
        for basis in self._query(ledger, 'SELECT record_id,memory_revision FROM note_bases WHERE revision_id=? ORDER BY ordinal LIMIT 4', (revision_id,)):
            try:
                sources.extend(self._support(snapshot, basis['record_id'], basis['memory_revision'], ledger, depth + 1))
            except Failure as error:
                if error.code not in ('memory_unavailable', 'memory_source_unavailable', 'memory_resolution_failed'):
                    raise
        return list({s['source_id']: s for s in sources}.values())[:4]

    def _candidate(self, snapshot, note_id, revision_id, ledger):
        memory = self.store.memory
        memory.check(snapshot, ledger)
        row = memory._revision(snapshot, note_id, revision_id, ledger)
        fields = self.store.notes.metadata(snapshot, revision_id, ledger)
        if (row['head_revision'] != revision_id or row['validity'] != 'current' or fields.get('purpose') != 'about_user'
                or 'user-rejected' in fields.get('flags', []) or self._query(ledger,
                    'SELECT 1 FROM notebook_care_retired WHERE record_id=?', (note_id,)).fetchone()):
            return None
        sources = self._support(snapshot, note_id, revision_id, ledger)
        if not sources:
            return None
        meta = memory._meta(snapshot, row['payload_id'], 'memory', ledger)
        if meta['bytes'] > MAX_NOTE_BYTES:
            return None
        page = memory._page(meta, 0, MAX_NOTE_BYTES, ledger)
        if page['next_offset'] is not None or page['status'] != 'complete':
            return None
        return dict(note_id=note_id, revision_id=revision_id, text=page['content'], sources=sources)

    def candidates(self, run_id, snapshot, ledger, now):
        self.guard(self.store.jobs.check_run(run_id))
        with self.db:
            self.db.execute('''DELETE FROM interests WHERE NOT EXISTS(
                SELECT 1 FROM memory_records r JOIN memory_revisions v ON v.id=interests.revision_id
                JOIN recall_turns t ON t.user_id=interests.source_id JOIN memory_grants g ON g.conversation_id=t.conversation_id
                WHERE r.id=interests.note_id AND r.readable=1 AND v.readable=1 AND g.enabled=1 AND g.bank_id=r.bank_id
                AND NOT EXISTS(SELECT 1 FROM excluded_memory_revisions e WHERE e.revision_id=v.id))''')
        since = self.db.execute('SELECT sequence FROM interest_scan').fetchone()[0]
        # Rotate complete eligible note records; an oversized or unsupported
        # note never becomes a silently shortened assertion or blocks the queue.
        rows = self._query(ledger, '''SELECT r.id,v.id AS revision_id,v.sequence FROM memory_records r
            JOIN note_records n ON n.record_id=r.id JOIN memory_revisions v ON v.id=r.head_revision
            JOIN note_metadata m ON m.revision_id=v.id LEFT JOIN interests i ON i.note_id=r.id
            WHERE r.bank_id=? AND json_extract(m.body,'$.purpose')='about_user'
            AND (i.id IS NULL AND NOT EXISTS(SELECT 1 FROM interest_reviewed c WHERE c.revision_id=v.id)
                OR i.stopped=0 AND julianday(i.next_at)<=julianday(?))
            ORDER BY CASE WHEN i.id IS NOT NULL THEN 0 WHEN v.sequence>? THEN 1 ELSE 2 END,v.sequence LIMIT 12''',
            (snapshot.bank_id, now, since)).fetchall()
        result = []
        for row in rows:
            try:
                candidate = self._candidate(snapshot, row['id'], row['revision_id'], ledger)
                if candidate:
                    result.append(candidate)
            except Failure as error:
                if error.code not in ('memory_unavailable', 'memory_source_unavailable', 'memory_resolution_failed'):
                    raise
        if rows:
            with self.db:
                self.db.execute('UPDATE interest_scan SET sequence=?', (max((r['sequence'] for r in rows if r['sequence'] > since), default=0),))
        return result

    def reviewed(self, candidates, now, *, pause_existing=False):
        with self.db:
            self.db.executemany('INSERT OR REPLACE INTO interest_reviewed VALUES(?,?)',
                [(c['revision_id'], now) for c in candidates])
            if pause_existing:
                for candidate in candidates:
                    # A note edit changes its revision before the interest is
                    # rebound. Pause by note identity, but preserve user snoozes.
                    row = self.db.execute('SELECT id FROM interests WHERE note_id=? AND stopped=0 AND julianday(next_at)<=julianday(?)',
                        (candidate['note_id'],now)).fetchone()
                    if row:
                        self.db.execute('UPDATE interests SET next_at=?,revision=revision+1 WHERE id=?',
                            ((instant(now)+timedelta(days=30)).isoformat(),row['id']))
                        self.db.execute('UPDATE interest_findings SET expires_at=? WHERE interest_id=?', (now,row['id']))

    def stopped_topics(self):
        return [r[0] for r in self.db.execute('SELECT topic_key FROM interest_stops ORDER BY stopped_at DESC LIMIT 64')]

    def plan(self, run_id, snapshot, ledger, value, now):
        self.guard(self.store.jobs.check_run(run_id))
        candidate = self._candidate(snapshot, value['note_id'], value['revision_id'], ledger)
        if candidate is None or not any(s['source_id'] == value['source_id'] and s['sha256'] == value['source_sha256']
                and value['quote'] in s['text'] for s in candidate['sources']):
            raise Failure('interest_source', 'Interest support changed before the check.')
        key = value['topic'].casefold()
        with self.db:
            if self.db.execute('SELECT 1 FROM interest_stops WHERE topic_key=? OR note_id=? OR source_id=?', (key, value['note_id'], value['source_id'])).fetchone():
                return None
            old = self.db.execute('SELECT * FROM interests WHERE note_id=?', (value['note_id'],)).fetchone()
            if old and (old['stopped'] or instant(old['next_at']) > instant(now)):
                return None
            duplicate = self.db.execute('SELECT id FROM interests WHERE topic_key=? AND stopped=0 AND note_id!=?', (key, value['note_id'])).fetchone()
            if duplicate:
                return None
            if not old and self.db.execute('SELECT count(*) FROM interests WHERE stopped=0').fetchone()[0] >= MAX_TOPICS:
                return None
            ident = old['id'] if old else uuid.uuid4().hex
            # Debit the next attempt before IO. Failures/interruption do not
            # create an immediate retry loop or restore spent allowance.
            next_at = (instant(now) + timedelta(days=1)).isoformat()
            if old:
                self.db.execute('''UPDATE interests SET revision_id=?,source_id=?,source_sha256=?,quote=?,topic=?,topic_key=?,
                    category=?,query_kind=?,next_at=?,revision=revision+1 WHERE id=?''',
                    (value['revision_id'], value['source_id'], value['source_sha256'], value['quote'], value['topic'], key,
                     value['category'], value['query_kind'], next_at, ident))
            else:
                self.db.execute('''INSERT INTO interests(id,note_id,revision_id,source_id,source_sha256,quote,topic,topic_key,category,query_kind,next_at,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (ident, value['note_id'], value['revision_id'], value['source_id'],
                    value['source_sha256'], value['quote'], value['topic'], key, value['category'], value['query_kind'], next_at, now))
        return ident

    def valid(self, ident, snapshot, ledger):
        row = self._query(ledger, 'SELECT * FROM interests WHERE id=? AND stopped=0', (ident,)).fetchone()
        if row is None:
            return False
        try:
            candidate = self._candidate(snapshot, row['note_id'], row['revision_id'], ledger)
        except Failure as error:
            if error.code in ('memory_unavailable', 'memory_source_unavailable', 'memory_resolution_failed'):
                return False
            raise
        return candidate is not None and any(s['source_id'] == row['source_id'] and s['sha256'] == row['source_sha256']
            and row['quote'] in s['text'] for s in candidate['sources'])

    def publish(self, run_id, ident, snapshot, ledger, value, now):
        self.guard(self.store.jobs.check_run(run_id))
        if not self.valid(ident, snapshot, ledger):
            raise Failure('interest_source', 'Interest support changed; no finding was saved.')
        with self.db:
            result = value['finding']
            # A successful refresh supersedes older evidence, including a quiet
            # result (e.g. cancellation). Failure never extends old freshness.
            self.db.execute('UPDATE interest_findings SET expires_at=? WHERE interest_id=?', (now, ident))
            if result:
                self.db.execute('''INSERT INTO interest_findings VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(interest_id,identity) DO UPDATE SET body=excluded.body,source_id=excluded.source_id,
                    checked_at=excluded.checked_at,expires_at=excluded.expires_at''',
                    (uuid.uuid4().hex, ident, result['source_id'], result['identity'], encoded(result),
                     result['checked_at'], result['expires_at']))
            self.db.execute('UPDATE interests SET next_at=?,checked_at=?,revision=revision+1 WHERE id=?',
                (value['next_at'], now, ident))

    def offer(self, conversation, turn_id, user, snapshot, ledger, now):
        if not opening(user) or not self.enabled() or not self.store.web.settings()['enabled']:
            return None
        recent = self.db.execute("SELECT created_at FROM interest_offers WHERE status IN ('mentioned','possibly_seen') ORDER BY julianday(created_at) DESC LIMIT 1").fetchone()
        recent = recent[0] if recent else None
        if recent and instant(now) - instant(recent) < MENTION_GAP:
            return None
        rows = self._query(ledger, '''SELECT f.*,i.topic,i.mentioned_at FROM interest_findings f JOIN interests i ON i.id=f.interest_id
            JOIN research_web_sources w ON w.id=f.source_id JOIN background_runs b ON b.id=w.run_id
            WHERE i.stopped=0 AND b.status='completed' AND NOT EXISTS(SELECT 1 FROM excluded_background_runs e WHERE e.run_id=b.id)
            AND julianday(f.expires_at)>julianday(?)
            AND NOT EXISTS(SELECT 1 FROM interest_offers o WHERE o.status IN ('mentioned','possibly_seen')
                AND (o.finding_id=f.id OR json_extract(o.admission,'$.identity')=f.identity))
            ORDER BY julianday(f.checked_at) DESC LIMIT 4''', (now,)).fetchall()
        for row in rows:
            if instant(row['expires_at']) <= instant(now) or (row['mentioned_at'] and instant(now) - instant(row['mentioned_at']) < TOPIC_GAP):
                continue
            if not self.valid(row['interest_id'], snapshot, ledger):
                continue
            value = json.loads(row['body'])
            reference_id = uuid.uuid4().hex
            try:
                link = reference_link(value['url'], reference_id)
            except ValueError:
                continue
            return dict(identity=value['identity'], reference_id=reference_id, source_id=value['source_id'], finding_id=row['id'], interest_id=row['interest_id'], topic=row['topic'], title=value['title'],
                detail=value['detail'], quote=value['quote'], checked_at=value['checked_at'], expires_at=value['expires_at'],
                source_link=link, url=value['url'],
                first_mention=self.db.execute("SELECT 1 FROM interest_offers WHERE status='mentioned' LIMIT 1").fetchone() is None)
        return None

    def offered(self, turn_id, value, now):
        with self.db:
            support = self.db.execute('SELECT note_id,revision_id,source_id,source_sha256 FROM interests WHERE id=?', (value['interest_id'],)).fetchone()
            # Immutable admission: later refreshes never rewrite the source a
            # reply actually received. Original IDs also feed recall exclusion.
            admission = dict(value, support=dict(support), presentation='context')
            self.db.execute('INSERT INTO interest_offers VALUES(?,?,?,?,?,?)',
                (turn_id, value['finding_id'], value['reference_id'], encoded(admission), 'offered', now))

    def admission(self, turn_id):
        row = self.db.execute('SELECT admission FROM interest_offers WHERE turn_id=?', (turn_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def check_offer(self, turn_id, snapshot, ledger, now):
        admitted = self.admission(turn_id)
        if not admitted:
            return False
        try:
            return bool(self.enabled() and self.store.web.settings()['enabled']
                and instant(admitted['expires_at']) > instant(now)
                and self.valid(admitted['interest_id'], snapshot, ledger))
        except (Failure, ValueError, TypeError):
            # Revalidate input before each provider call, including tool
            # continuations. Expired context is withdrawn without rewriting prose.
            return False

    def finish(self, turn_id):
        # Reuse the existing conservative exposure state. We know the finding
        # entered context, not whether it was mentioned or read. No reply parsing,
        # mention claim, or generated reference receipt controls ordinary speech.
        self.db.execute("UPDATE interest_offers SET status='possibly_seen' WHERE turn_id=?", (turn_id,))

    def recover(self):
        self.db.execute("UPDATE interest_offers SET status='possibly_seen' WHERE status='offered'")

    def model_operation(self, args, snapshot, tid, ledger, budget, reads, now):
        """Foreground bridge to the same Stop/Not now owner used by Settings."""
        from ...core.interests import control_key
        self.store.memory.check(snapshot, ledger)
        turn = self.store._row(tid)
        if ledger.notebook_only or ledger.web_only or turn['status'] != 'running' or turn['conversation_id'] != snapshot.conversation_id:
            raise Failure('interest_forbidden', 'Interest controls require a current foreground memory grant.')
        result = dict(domain='interests')
        try:
            receipt = None
            if args['action'] == 'outcome':
                row = self._query(ledger, 'SELECT receipt FROM interest_controls WHERE mutation_key=?', (args['mutation_key'],)).fetchone()
                if row is None:
                    return dict(result,status='not_recorded')
                receipt = json.loads(row[0])
            # Bounded existing catalogue. The same source validation used by
            # background checks and context admission protects these tool reads.
            if args['action'] == 'list':
                rows = self._query(ledger, 'SELECT * FROM interests ORDER BY stopped,julianday(created_at) DESC,id LIMIT 32').fetchall()
            else:
                ident = receipt['interest_id'] if receipt else args['id']
                rows = self._query(ledger, 'SELECT * FROM interests WHERE id=?', (ident,)).fetchall()
            eligible = []
            for row in rows:
                candidate = self._candidate(snapshot,row['note_id'],row['revision_id'],ledger)
                if not candidate or not any(s['source_id']==row['source_id'] and s['sha256']==row['source_sha256']
                           and row['quote'] in s['text'] for s in candidate['sources']):
                    continue
                eligible.append(dict(id=row['id'],revision=row['revision'],topic=row['topic'],stopped=bool(row['stopped']),
                    next_at=row['next_at'],source_dependencies=[row['note_id'],row['revision_id'],row['source_id']]))
            if receipt:
                # A committed action remains recoverable on a later turn or
                # after reopening, but its original source must still be allowed.
                if not eligible:
                    raise Failure('interest_source', 'The recorded outcome is no longer accessible; do not repeat the action.')
                result.update(receipt,source_dependencies=eligible[0]['source_dependencies'])
                return result if budget.fits(result) else dict(domain='interests',status='not_delivered',reason='result_capacity')
            if args['action'] == 'list':
                offset, limit = args.get('offset',0), args.get('limit',5)
                items = eligible[offset:offset+limit]
                result.update(status='complete',items=items,next_offset=offset+len(items) if offset+len(items)<len(eligible) else None)
                return result if budget.fits(result) else dict(domain='interests',status='not_delivered',reason='result_capacity')
            if not eligible or (args['id'],args['revision']) not in reads:
                raise Failure('interest_read_required', 'Use the id and revision from a current-turn list.')
            key = control_key(self.store.state_id,tid,args)
            receipt = dict(result,status='committed',interest_id=args['id'],action=args['action'],mutation_key=key,
                           source_dependencies=eligible[0]['source_dependencies'])
            if not budget.fits(receipt):
                raise Failure('interest_result_capacity', 'The complete change receipt does not fit; nothing changed.')
            self.control(args['id'],args['action'],args['revision'],key,now)
            return receipt
        except Failure as error:
            if error.code in ('timeout','history_work_limit','memory_source_changed','memory_unavailable'):
                raise
            return dict(result,status='rejected',reason=error.code,recovery=error.detail)

    def _change(self, ident, action, now):
        if action == 'stop':
            self.db.execute('INSERT OR IGNORE INTO interest_stops SELECT topic_key,note_id,source_id,? FROM interests WHERE id=?', (now, ident))
            self.db.execute('UPDATE interests SET stopped=1,revision=revision+1 WHERE topic_key=(SELECT topic_key FROM interests WHERE id=?)', (ident,))
        else:
            self.db.execute('UPDATE interests SET next_at=?,mentioned_at=?,revision=revision+1 WHERE id=?',
                ((instant(now) + TOPIC_GAP).isoformat(), now, ident))


    def control(self, ident, action, expected_revision, mutation_key, now):
        from .jobs import identity
        identity(ident); identity(mutation_key)
        if action not in ('stop', 'later') or type(expected_revision) is not int:
            raise Failure('interest_control', 'Choose Stop or Not now for the current interest.')
        intent = hashlib.sha256(encoded([ident, action, expected_revision]).encode()).hexdigest()
        old = self.db.execute('SELECT * FROM interest_controls WHERE mutation_key=?', (mutation_key,)).fetchone()
        if old:
            if old['intent_sha256'] != intent:
                raise Failure('mutation_conflict', 'This key belongs to another interest change.')
            return json.loads(old['receipt'])
        row = self.db.execute('SELECT revision FROM interests WHERE id=?', (ident,)).fetchone()
        if row is None or row['revision'] != expected_revision:
            raise Failure('interest_conflict', 'The interest changed; reload before choosing again.')
        with self.db:
            self._change(ident, action, now)
            receipt = dict(status='committed', interest_id=ident, action=action, mutation_key=mutation_key)
            self.db.execute('INSERT INTO interest_controls VALUES(?,?,?)', (mutation_key, intent, encoded(receipt)))
        return receipt

    def list(self):
        # Local owner inspection, never a model grant. Only current accessible
        # support is shown; revoked source text is not retained in UI results.
        rows = self.db.execute('''SELECT i.* FROM interests i JOIN memory_records r ON r.id=i.note_id
            JOIN memory_revisions v ON v.id=i.revision_id JOIN recall_turns t ON t.user_id=i.source_id
            JOIN memory_grants g ON g.conversation_id=t.conversation_id
            WHERE r.readable=1 AND v.readable=1 AND g.enabled=1 AND t.status='completed'
            AND r.validity='current' AND g.bank_id=r.bank_id
            AND NOT EXISTS(SELECT 1 FROM excluded_memory_revisions e WHERE e.revision_id=i.revision_id)
            AND NOT EXISTS(SELECT 1 FROM notebook_care_retired n WHERE n.record_id=i.note_id)
            AND NOT EXISTS(SELECT 1 FROM note_metadata m,json_each(m.body,'$.flags') flags
                WHERE m.revision_id=i.revision_id AND flags.value='user-rejected')
            ORDER BY i.stopped,julianday(i.created_at) DESC LIMIT 32''').fetchall()
        return dict(items=[dict(id=r['id'], revision=r['revision'], topic=r['topic'], quote=r['quote'],
            stopped=bool(r['stopped']), next_at=r['next_at'], checked_at=r['checked_at'], mentioned_at=r['mentioned_at']) for r in rows])
