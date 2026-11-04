"""Quiet assignment results and job-owned web evidence; no fabricated chat turns."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import uuid
from urllib.parse import urljoin

from ...core.jobs import JobDefinition, JobPermissions
from ...core.records import Failure
from ...core.reference import exact_page
from ...core.research import ResearchSpec, SERIALIZED_BYTES, canonical_url, report, validate
from ...core.schedules import Schedule, instant
from .web import MAX_SOURCE_BYTES, digest, encode

RESEARCH_SCHEMA = '''
CREATE TABLE research_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1),epoch INTEGER NOT NULL);
INSERT INTO research_state VALUES(1,0);
CREATE TABLE research_web_sources(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES background_runs(id),
 provider TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('search','page')),request TEXT NOT NULL,
 metadata TEXT NOT NULL,content TEXT NOT NULL,sha256 TEXT NOT NULL,binding TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX research_web_run ON research_web_sources(run_id,id);
CREATE TABLE research_findings(id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES background_jobs(id),
 url TEXT NOT NULL,revision INTEGER NOT NULL,seen_revision INTEGER NOT NULL DEFAULT 0,
 disposition TEXT NOT NULL DEFAULT 'open' CHECK(disposition IN ('open','saved','dismissed')),
 first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,UNIQUE(job_id,url));
CREATE TABLE research_finding_versions(finding_id TEXT NOT NULL REFERENCES research_findings(id),
 revision INTEGER NOT NULL,run_id TEXT NOT NULL REFERENCES background_runs(id),body TEXT NOT NULL,
 PRIMARY KEY(finding_id,revision));
CREATE TABLE research_presentations(job_id TEXT PRIMARY KEY REFERENCES background_jobs(id),seen_run_id TEXT);
CREATE TABLE research_operations(mutation_key TEXT PRIMARY KEY,intent TEXT NOT NULL,receipt TEXT NOT NULL);
'''


COMPLETE_REPORT = "r.status='completed' AND json_extract(p.body,'$.coverage')='complete'"
# Publication and search coverage are separate: a settled, validated report may
# honestly have partial coverage. Drafts and unfinished/failed runs stay quiet.
PUBLISHED_REPORT = "r.status='completed' AND json_extract(p.body,'$.coverage') IN ('complete','partial')"


def now():
    return datetime.now(timezone.utc).isoformat()


class ResearchStore:
    def __init__(self, store):
        self.store, self.db = store, store.db

    def retry_context(self, ident):
        row = self.db.execute("SELECT payload FROM background_run_events WHERE run_id=? AND kind='research_retry'", (ident,)).fetchone()
        return json.loads(row[0]) if row else dict(attempt=0, spent=dict(calls=0, operations=0, seconds=0))

    def allowance(self, run, definition):
        # A safe restart gets one saved attempt allowance. Cumulative actual
        # expenditure remains in immutable retry receipts, never erased/refunded.
        return {key: getattr(definition.limits, key) for key in ('calls', 'operations', 'seconds')}

    def continuation(self, when):
        """Keep a safely interrupted occurrence queued until it can finish.

        Each attempt is bounded, with ordinary idle/cooldown admission. No attempt
        count or age silently drops interrupted work. Technical failures and
        explicit cancellation stay terminal; uncertain effects are never replayed.
        Changed memory starts a fresh transcript, with newly granted reads; no
        previous memory bodies, handles or search results are carried forward.
        """
        rows = self.db.execute("""SELECT r.* FROM background_jobs j JOIN background_runs r ON r.sequence=(
            SELECT max(sequence) FROM background_runs WHERE job_id=j.id)
            WHERE j.kind='research' AND j.enabled=1 AND j.cancelled=0 AND j.revision=r.revision
            AND r.status IN ('cancelled','interrupted')
            AND r.error_code IN ('job_activity','job_foreground','job_owner_closed','owner_exit_unknown','memory_source_changed')""").fetchall()
        for run in rows:
            if run['finished_at'] and instant(when) < instant(run['finished_at']):
                continue
            # MCP calls may have external effects; an automatic fresh run must
            # never replay completed or uncertain actions after interruption.
            if self.db.execute("SELECT 1 FROM background_mcp_calls WHERE run_id=? AND status!='not_executed' LIMIT 1", (run['id'],)).fetchone():
                continue
            context = self.retry_context(run['id'])
            original = context.get('scheduled_at', run['scheduled_at'])
            definition = self.store.jobs.definition(run['job_id'], run['revision'])
            try:
                self.guard(definition)
            except Failure:
                continue
            counts = dict(self.db.execute("""SELECT kind,max(json_extract(payload,'$.ordinal'))
                FROM background_run_events WHERE run_id=? AND kind IN ('model_started','operation_started') GROUP BY kind""", (run['id'],)))
            # Charge in-flight requests and operations, not just successful ones.
            # Unknown crash duration is conservatively charged through recovery.
            usage = self.db.execute("SELECT json_extract(payload,'$.active_seconds') FROM background_run_events WHERE run_id=? AND kind='cancelled' ORDER BY sequence DESC LIMIT 1", (run['id'],)).fetchone()
            duration = usage[0] if usage and type(usage[0]) is int else max(0, math.ceil((instant(run['finished_at'])-instant(run['started_at'])).total_seconds())) if run['started_at'] else 0
            spent = dict(calls=context['spent']['calls'] + counts.get('model_started', 0),
                operations=context['spent']['operations'] + counts.get('operation_started', 0),
                seconds=context['spent']['seconds'] + duration)
            ident = uuid.uuid4().hex
            # Scheduler timestamps are unique per job/revision. Repeated polls
            # see this pending row and cannot enqueue another continuation.
            if self.db.execute('SELECT 1 FROM background_runs WHERE job_id=? AND revision=? AND scheduled_at=?',
                    (run['job_id'], run['revision'], when)).fetchone():
                continue
            self.db.execute("""INSERT INTO background_runs(id,job_id,revision,scheduled_at,covered_through,created_at,status,error_code)
                VALUES(?,?,?,?,?,?,'pending','research_retry')""", (ident,run['job_id'],run['revision'],when,run['covered_through'],when))
            receipt = dict(parent_run=run['id'], root_run=context.get('root_run',run['id']),
                scheduled_at=original, attempt=context['attempt']+1, spent=spent)
            self.db.execute('INSERT INTO background_run_events(run_id,kind,payload,created_at) VALUES(?,?,?,?)',
                (ident,'research_retry',encode(receipt),when))

    def mcp_receipts(self):
        from .mcp import McpStore
        return McpStore(self.store,background=True)

    def web_sources(self,run_id):
        return ResearchWebSources(self,run_id)

    def epoch(self):
        return self.db.execute('SELECT epoch FROM research_state WHERE singleton=1').fetchone()[0]

    def bump(self):
        self.db.execute('UPDATE research_state SET epoch=epoch+1 WHERE singleton=1')

    def guard(self, definition, *, execution=True):
        """Recheck source and current access before IO and atomic publication."""
        if definition.kind != 'research' or definition.research is None:
            raise Failure('research_missing', 'This job is not a research assignment.')
        conv = self.store.conversation(definition.conversation_id)
        web = self.store.web.settings()
        grant = self.db.execute('SELECT enabled FROM memory_grants WHERE conversation_id=?', (definition.conversation_id,)).fetchone()
        if not conv.history_enabled or not conv.tasks_enabled or not conv.memory_bank_id or grant is None or not grant[0] or execution and not conv.notes_writable:
            raise Failure('research_forbidden', 'Task permission is no longer available; this assignment is paused by permission loss.')
        source = self.db.execute('SELECT user_id FROM recall_turns WHERE id=? AND conversation_id=?',
            (definition.research.source_turn_id, definition.conversation_id)).fetchone()
        if source is None or definition.research.source_quote not in self.store._body(source['user_id']):
            raise Failure('research_source', 'The original assignment request is no longer available.')

    def _definition(self, ident):
        definition = self.store.jobs.definition(ident)
        if definition.kind != 'research':
            raise Failure('research_missing', 'This is not a research assignment.')
        return definition

    def _ref(self, job):
        return 'research:' + job['id'] + ':' + str(job['revision']) + ':' + digest([self.store.state_id,job['id'],job['revision']])

    def _from_ref(self, value):
        try:
            ident = value.split(':')[1]
            job = self.store.jobs._job(ident)
            if self._ref(job) != value or job['kind'] != 'research':
                raise ValueError
            return dict(job)
        except (ValueError, IndexError):
            raise Failure('research_conflict', 'The assignment changed; read its current definition.') from None

    def item(self, ident):
        job = dict(self.store.jobs._job(ident)); definition = self._definition(ident)
        last = self.db.execute('SELECT id,status,finished_at,error_code FROM background_runs WHERE job_id=? ORDER BY sequence DESC LIMIT 1',(ident,)).fetchone()
        checked = self.db.execute("SELECT max(r.finished_at) FROM background_runs r JOIN artifact_payloads p ON p.artifact_id=r.result_id WHERE r.job_id=? AND " + COMPLETE_REPORT,(ident,)).fetchone()[0]
        unseen = self.db.execute("SELECT count(*) FROM research_findings WHERE job_id=? AND revision>seen_revision AND disposition!='dismissed'",(ident,)).fetchone()[0]
        seen = self.db.execute('SELECT seen_run_id FROM research_presentations WHERE job_id=?',(ident,)).fetchone()
        settled = last is not None and self.db.execute("SELECT 1 FROM background_runs r JOIN artifact_payloads p ON p.artifact_id=r.result_id WHERE r.id=? AND " + PUBLISHED_REPORT,(last['id'],)).fetchone() is not None
        return dict(id=ident,read_ref=self._ref(job),revision=job['revision'],title=definition.title,
            instructions=definition.research.instructions,schedule=definition.record()['schedule'],
            enabled=bool(job['enabled']) and not job['cancelled'],next_at=job['next_at'],
            conversation_id=definition.conversation_id,last_checked=checked,last_run=dict(last) if last else None,
            new_findings=unseen,new_result=bool(settled and (seen is None or seen[0]!=last['id'])))

    def listing(self, offset=0, limit=20, *, model=False):
        rows=self.db.execute("SELECT id FROM background_jobs WHERE kind='research' ORDER BY updated_at DESC,id LIMIT ? OFFSET ?",(limit+1,offset)).fetchall()
        items=[];consumed=0;size=0
        for row in rows[:limit]:
            if model:
                try: self.guard(self._definition(row['id']),execution=False)
                except Failure:
                    consumed+=1
                    continue
            item=self.item(row['id']);cost=len(json.dumps(item,ensure_ascii=True,separators=(',',':')).encode())+1
            # Whole records and a continuation, never shortened instructions.
            # Reserve envelope room within the existing native response bound.
            if items and size+cost>SERIALIZED_BYTES-8192:break
            items.append(item);size+=cost;consumed+=1
        total=self.db.execute("SELECT count(*) FROM background_jobs WHERE kind='research'").fetchone()[0]
        # Only the latest settled report gets a badge. Never substitute an older
        # success for a pending, paused, failed or otherwise unfinished latest run.
        unread=self.db.execute('''SELECT count(*) FROM background_jobs j
            JOIN background_runs r ON r.sequence=(SELECT max(sequence) FROM background_runs WHERE job_id=j.id)
            JOIN artifact_payloads p ON p.artifact_id=r.result_id
            LEFT JOIN research_presentations seen ON seen.job_id=j.id
            WHERE j.kind='research' AND ''' + PUBLISHED_REPORT + '''
            AND (seen.seen_run_id IS NULL OR seen.seen_run_id!=r.id)''').fetchone()[0]
        return dict(domain='research',status='complete',items=items,total=total,new_results=unread,
            next_offset=offset+consumed if len(rows)>consumed else None,now=now())

    def counts(self, ident=None):
        """Review state comes from delivered revisions, independently of Save/Dismiss."""
        where=' WHERE job_id=?' if ident else ''
        row=self.db.execute("""SELECT
            count(*) FILTER (WHERE disposition!='dismissed') AS total,
            count(*) FILTER (WHERE disposition!='dismissed' AND revision>seen_revision AND seen_revision=0) AS new,
            count(*) FILTER (WHERE disposition!='dismissed' AND revision>seen_revision AND seen_revision>0) AS changed,
            count(*) FILTER (WHERE disposition!='dismissed' AND revision<=seen_revision) AS reviewed,
            count(*) FILTER (WHERE disposition='saved') AS saved,
            count(*) FILTER (WHERE disposition='dismissed') AS dismissed
            FROM research_findings"""+where,(ident,) if ident else ()).fetchone()
        return dict(row)

    def overview(self, offset=0, limit=20):
        # The report badge remains bound to the latest published run. Older
        # unreviewed findings are counted separately, even after a failed check.
        report_sql="""EXISTS(SELECT 1 FROM background_runs r
            JOIN artifact_payloads p ON p.artifact_id=r.result_id
            LEFT JOIN research_presentations seen ON seen.job_id=r.job_id
            WHERE r.job_id=j.id AND r.sequence=(SELECT max(sequence) FROM background_runs WHERE job_id=j.id)
            AND """+PUBLISHED_REPORT+" AND (seen.seen_run_id IS NULL OR seen.seen_run_id!=r.id))"
        finding_sql="EXISTS(SELECT 1 FROM research_findings f WHERE f.job_id=j.id AND f.disposition!='dismissed' AND f.revision>f.seen_revision)"
        attention='('+report_sql+' OR '+finding_sql+')'
        totals=self.db.execute("SELECT count(*) AS total,coalesce(sum("+report_sql+"),0) AS unread_reports,coalesce(sum("+attention+"),0) AS attention_searches FROM background_jobs j WHERE kind='research'").fetchone()
        rows=self.db.execute("SELECT j.id FROM background_jobs j WHERE kind='research' ORDER BY "+attention+" DESC,j.updated_at DESC,j.id LIMIT ? OFFSET ?",(limit+1,offset)).fetchall()
        items=[]
        for row in rows[:limit]:
            task=self.item(row['id'])
            items.append(dict(id=task['id'],title=task['title'],enabled=task['enabled'],
                next_at=task['next_at'],timezone=task['schedule']['timezone'],last_run=task['last_run'],
                new_report=task['new_result'],counts=self.counts(task['id'])))
        return dict(items=items,counts=self.counts(),revision=self.epoch(),**dict(totals),
            next_offset=offset+limit if len(rows)>limit else None)

    def browse(self, ident, offset=0, limit=12, view='inbox', query=''):
        self._definition(ident)
        clauses={
            'inbox':"f.disposition!='dismissed' AND f.revision>f.seen_revision",
            'new':"f.disposition!='dismissed' AND f.revision>f.seen_revision AND f.seen_revision=0",
            'changed':"f.disposition!='dismissed' AND f.revision>f.seen_revision AND f.seen_revision>0",
            'all':"f.disposition!='dismissed'", 'saved':"f.disposition='saved'",
            'reviewed':"f.disposition!='dismissed' AND f.revision<=f.seen_revision",
            'dismissed':"f.disposition='dismissed'"}
        if view not in clauses:raise Failure('research_arguments','Unknown findings view.')
        where='f.job_id=? AND '+clauses[view];args=[ident]
        if query.strip():
            where+=" AND instr(lower(json_extract(v.body,'$.title')||' '||f.url||' '||json_extract(v.body,'$.details')),lower(?))>0"
            args.append(query.strip())
        source=' FROM research_findings f JOIN research_finding_versions v ON v.finding_id=f.id AND v.revision=f.revision JOIN background_runs r ON r.id=v.run_id WHERE '+where
        total=self.db.execute('SELECT count(*)'+source,args).fetchone()[0]
        rows=self.db.execute('SELECT f.*,v.run_id,v.body,r.finished_at AS updated_at'+source+
            ' ORDER BY (f.revision>f.seen_revision) DESC,r.sequence DESC,f.last_seen DESC,f.id LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
        items=[]
        for row in rows:
            attention=('dismissed' if row['disposition']=='dismissed' else
                'reviewed' if row['revision']<=row['seen_revision'] else
                'new' if row['seen_revision']==0 else 'changed')
            items.append(dict(id=row['id'],revision=row['revision'],disposition=row['disposition'],
                new=row['revision']>row['seen_revision'],first_seen=row['first_seen'],last_seen=row['last_seen'],
                run_id=row['run_id'],**json.loads(row['body']),attention=attention,updated_at=row['updated_at']))
        return dict(items=items,total=total,counts=self.counts(ident),
            next_offset=offset+len(items) if offset+len(items)<total else None)

    def _finding_ref(self, row):
        # Include current attention as well as source revision: a saved finding
        # must not be dismissed using a read from before it was saved.
        job=self.store.jobs._job(row['job_id'])
        return 'finding:'+row['id']+':'+str(row['revision'])+':'+digest([
            self.store.state_id,row['job_id'],job['revision'],row['id'],row['revision'],row['disposition']])

    def findings(self, ident, offset=0, limit=20, *, model=False):
        self._definition(ident)
        rows=self.db.execute('''SELECT f.*,v.run_id,v.body FROM research_findings f
            JOIN research_finding_versions v ON v.finding_id=f.id AND v.revision=f.revision
            WHERE f.job_id=? ORDER BY f.last_seen DESC,f.id LIMIT ? OFFSET ?''',(ident,limit+1,offset)).fetchall()
        items=[]
        for row in rows[:limit]:
            body=json.loads(row['body'])
            items.append(dict(id=row['id'],revision=row['revision'],disposition=row['disposition'],
                new=row['revision']>row['seen_revision'],first_seen=row['first_seen'],last_seen=row['last_seen'],
                run_id=row['run_id'],**body))
            # Model read authority is delivered with the complete finding, while
            # the native panel keeps its existing contract and revision owner.
            if model:items[-1]['read_ref']=self._finding_ref(row)
        return dict(domain='research',status='complete',items=items,next_offset=offset+limit if len(rows)>limit else None)

    def _finding_disposition(self,args,*,operation_key,ledger,maximum,budget,read_refs):
        matched=re.fullmatch(r'finding:([0-9a-f]{32}):([1-9][0-9]*):[0-9a-f]{64}',args['read_ref'])
        if matched is None:
            raise Failure('research_read','Read the finding with findings(job_id), then use its exact read_ref.')
        ident,revision=matched.group(1),int(matched.group(2))
        def current():
            if args['read_ref'] not in read_refs:
                raise Failure('research_read','Read this finding in an earlier result this turn before changing it.')
            row=self.db.execute('SELECT * FROM research_findings WHERE id=?',(ident,)).fetchone()
            if row is None or self._finding_ref(row)!=args['read_ref']:
                raise Failure('research_conflict','The finding or its attention changed; read it again before changing it.')
            self.guard(self._definition(row['job_id']),execution=False)
        admitted=None
        def fitted(receipt):
            nonlocal admitted
            admitted=self._fit(dict(domain='research',finding_id=ident,revision=revision,
                disposition=args['disposition'],**receipt),maximum,budget)
            ledger.spend_payload(len(encode(admitted).encode()))
        # The panel and chat share this transaction/receipt owner. Admission must
        # succeed before commit so a small result budget cannot hide a mutation.
        self.local(dict(action='disposition',finding_id=ident,expected_revision=revision,
            disposition=args['disposition'],mutation_key=operation_key),
            precondition=current,result_check=fitted)
        return admitted

    def runs(self, ident, offset=0, limit=5):
        rows=self.db.execute('SELECT id,revision,scheduled_at,started_at,finished_at,status,error_code,result_id '
            'FROM background_runs WHERE job_id=? ORDER BY sequence DESC LIMIT ? OFFSET ?',
            (ident,limit+1,offset)).fetchall()
        return dict(domain='research',status='complete',job_id=ident,items=[dict(r) for r in rows[:limit]],
            next_offset=offset+limit if len(rows)>limit else None)

    def result(self, ident, offset=0, maximum=8192):
        run=self.store.jobs.run(ident)
        definition=self._definition(run['job_id']);self.guard(definition,execution=False)
        value=dict(domain='research',run_id=ident,job_id=run['job_id'],run_status=run['status'],
            run_revision=run['revision'],current_revision=self.store.jobs._job(run['job_id'])['revision'],
            started_at=run['started_at'],finished_at=run['finished_at'],error_code=run['error_code'])
        error=self.db.execute("SELECT payload FROM background_run_events WHERE run_id=? AND kind='error' ORDER BY sequence DESC LIMIT 1",(ident,)).fetchone()
        value['diagnostic']=json.loads(error[0]) if error else None
        artifact=run['result_id']
        value['report_status']='published' if artifact else 'none'
        if artifact is None:
            draft=self.db.execute("SELECT payload FROM background_run_events WHERE run_id=? AND kind='report_draft' ORDER BY sequence DESC LIMIT 1",(ident,)).fetchone()
            if draft:
                artifact=json.loads(draft[0])['artifact_id'];value['report_status']='unvalidated_draft'
        if artifact:
            value.update(exact_page(artifact,self.store._body(artifact),offset,maximum).envelope())
        else:
            value.update(status='complete',content='',next_offset=None,
                notice='No report was published. This status does not establish no matches or a completed search.')
        return value

    def save_draft(self, ident, reply, ordinal):
        """Run-owned diagnostic body, never a published result or recall source."""
        self.guard(self.store.jobs.check_run(ident))
        self.store.jobs.event(ident,'report_draft',dict(ordinal=ordinal,
            finish_reason=reply.finish_reason),now=now(),draft_text=reply.text)

    def _fit(self, value, maximum, budget):
        if len(encode(value).encode())>maximum or budget is not None and not budget.fits(value):
            raise Failure('research_capacity','The complete result cannot fit; request a smaller page. No change ran.')
        return value

    def execute(self,args,*,conversation_id,turn_id,operation_key,ledger,maximum,budget,read_refs=frozenset()):
        args=validate(args);action=args['action'];conv=self.store.conversation(conversation_id)
        if not conv.tasks_enabled:
            raise Failure('research_forbidden','Research assignments require this conversation\'s task access.')
        if action=='list': value=self.listing(args.get('offset',0),args.get('limit',5),model=True)
        elif action in ('read','findings','runs','result'):
            ident=args.get('job_id')
            run_id=args.get('run_id')
            if action=='result' and run_id: ident=self.store.jobs.run(run_id)['job_id']
            self.guard(self._definition(ident),execution=False)
            if action=='read': value=dict(domain='research',status='complete',task=self.item(ident))
            elif action=='findings': value=self.findings(ident,args.get('offset',0),args.get('limit',5),model=True)
            elif action=='runs': value=self.runs(ident,args.get('offset',0),args.get('limit',5))
            else:
                if run_id is None:
                    row=self.db.execute('SELECT id FROM background_runs WHERE job_id=? ORDER BY sequence DESC LIMIT 1',(ident,)).fetchone()
                    if row is None:raise Failure('research_unrun','This assignment has not run yet. No saved result exists.')
                    run_id=row[0]
                page_bytes=args.get('max_bytes',8192)
                while True:
                    value=self.result(run_id,args.get('offset',0),page_bytes)
                    if page_bytes<=1 or len(encode(value).encode())<=maximum and (budget is None or budget.fits(value)):break
                    page_bytes=max(1,page_bytes//2)
        else:
            if not conv.notes_writable:
                raise Failure('research_forbidden','Task writing is not enabled for this conversation.')
            turn=self.store._row(turn_id)
            if turn['conversation_id']!=conversation_id or turn['status']!='running' or args['source_quote'] not in self.store._body(turn['user_id']):
                raise Failure('research_source','Changes need an exact quote from the current original user request.')
            if action=='disposition':
                return self._finding_disposition(args,operation_key=operation_key,ledger=ledger,
                    maximum=maximum,budget=budget,read_refs=read_refs)
            prior=self.store.jobs.lookup(operation_key)
            if prior['status']!='absent':
                value=dict(domain='research',**prior)
                ledger.spend_payload(len(encode(value).encode()))
                return self._fit(value,maximum,budget)
            existing=None
            if 'read_ref' in args:
                if args['read_ref'] not in read_refs:
                    raise Failure('research_read','Read this assignment in the current turn before editing it.')
                existing=self._from_ref(args['read_ref'])
            if action=='save':
                provider=self.store.web.settings()
                if existing:
                    original=self._definition(existing['id'])
                    definition=replace(original,title=args['title'],schedule=Schedule(**args['schedule']),
                        conversation_id=conversation_id,research=ResearchSpec(args['instructions'],turn_id,args['source_quote'],provider['provider']))
                else:
                    definition=JobDefinition('research',args['title'],conversation_id,Schedule(**args['schedule']),conv.profile,
                        permissions=JobPermissions(read_memory=True),enabled=True,
                        research=ResearchSpec(args['instructions'],turn_id,args['source_quote'],provider['provider']))
            else:
                if existing is None: raise Failure('research_read','Read the assignment before changing it.')
                definition=replace(self._definition(existing['id']),enabled=action=='resume')
            def fitted(receipt):
                fitted=self._fit(dict(domain='research',**receipt),maximum,budget)
                ledger.spend_payload(len(encode(fitted).encode()))
            value=dict(domain='research',**self.store.jobs.save(definition,mutation_key=operation_key,now=now(),
                job_id=existing['id'] if existing else None,expected_revision=existing['revision'] if existing else None,result_check=fitted))
        if action not in ('save','pause','resume'):
            ledger.spend_payload(len(encode(value).encode()))
        return self._fit(value,maximum,budget)

    def validate_report(self, run, value):
        """Same source checks before model repair and again at atomic publication."""
        try: report(value)
        except ValueError as error: raise Failure('research_report',str(error)) from None
        definition=self._definition(run['job_id']);self.guard(definition)
        for index,item in enumerate(value['findings'],1):
            url=canonical_url(item['url'])
            supported=False
            for ident in item['source_ids']:
                if self.db.execute('SELECT 1 FROM background_mcp_calls WHERE run_id=? AND result_id=?', (run['id'],ident)).fetchone():
                    saved=self.mcp_receipts().load(ident,run['id']);result=json.loads(saved['body'])
                    if result.get('isError'):raise Failure('research_source',f'Finding {index}: a failed connected-tool result cannot support a finding.')
                    # Only exact links in retained results from this run count.
                    texts=[part['text'] for part in result.get('content',[]) if isinstance(part,dict) and part.get('type')=='text' and isinstance(part.get('text'),str)]
                    if 'structuredContent' in result:texts.append(json.dumps(result['structuredContent'],ensure_ascii=False))
                    urls=[u.rstrip('.,);]') for text in texts for u in re.findall(r"https?://[^\s<>\"']+",text)]
                    # Playwright preserves literal relative hrefs. Resolve only
                    # a snapshot's explicit hrefs against its own page header,
                    # never an origin guessed from the candidate or task text.
                    if saved['tool'].startswith('browser_'):
                        for text in texts:
                            header=text.split('### Snapshot',1)[0]
                            origins=re.findall(r'(?m)^- Page URL: (https?://\S+)\s*$',header)
                            if len(origins)==1:
                                for href in re.findall(r'(?m)^\s*- /url: (\S+)\s*$',text):
                                    urls.append(urljoin(origins[0],href))
                else:
                    try: source=self.web_sources(run['id']).load(ident, definition.conversation_id)
                    except Failure as error:raise Failure('research_source',f'Finding {index}: {error.detail}') from None
                    metadata=source['metadata']
                    if metadata.get('status') not in ('ok','partial'):
                        raise Failure('research_source',f'Finding {index}: a failed fetch cannot support a finding.')
                    urls=[metadata.get('url'),metadata.get('requested_url')]
                    if source['kind']=='search': urls += [r.get('url') for r in json.loads(source['content']) if isinstance(r,dict)]
                    else: urls += [r.get('url') for r in metadata.get('links',[]) if isinstance(r,dict)]
                for candidate in urls:
                    try: supported = supported or canonical_url(candidate)==url
                    except (ValueError,TypeError): pass
            if not supported:
                raise Failure('research_source',f'Finding {index}: the URL is absent from its cited evidence. Use a source from this run containing this link, or omit the unverified candidate and disclose the gap.')

    def publish(self, run, value, when):
        """Called inside JobStore.finish's transaction: findings and success commit together."""
        if not self.db.in_transaction:
            raise Failure('research_transaction','Publication belongs to the terminal job transaction.')
        self.validate_report(run,value)
        for item in value['findings']:
            url=canonical_url(item['url'])
            previous=self.db.execute('SELECT * FROM research_findings WHERE job_id=? AND url=?',(run['job_id'],url)).fetchone()
            body=dict(item,url=url)
            if previous is None:
                ident=uuid.uuid4().hex;revision=1
                self.db.execute("INSERT INTO research_findings VALUES(?,?,?,1,0,'open',?,?)",(ident,run['job_id'],url,when,when))
            else:
                ident=previous['id'];revision=previous['revision']
                old=json.loads(self.db.execute('SELECT body FROM research_finding_versions WHERE finding_id=? AND revision=?',(ident,revision)).fetchone()[0])
                changed=item['change']=='changed' and any(item[k]!=old[k] for k in ('title','details','unknowns'))
                self.db.execute('UPDATE research_findings SET last_seen=? WHERE id=?',(when,ident))
                if not changed: continue
                revision+=1
                self.db.execute('UPDATE research_findings SET revision=? WHERE id=?',(revision,ident))
            self.db.execute('INSERT INTO research_finding_versions VALUES(?,?,?,?)',(ident,revision,run['id'],encode(body)))
        self.bump()

    def delete_check(self, args):
        """Resolve retries before looking up a job that may already be gone."""
        previous = self.db.execute('SELECT intent,receipt FROM research_operations WHERE mutation_key=?',
            (args['mutation_key'],)).fetchone()
        if previous:
            if previous['intent'] != digest(args):
                raise Failure('research_conflict', 'This change key belongs to another action.')
            return json.loads(previous['receipt'])
        job = self.store.jobs._job(args['job_id'])
        if job['kind'] != 'research':
            raise Failure('research_missing', 'Only research tasks can be deleted here.')
        if job['revision'] != args['expected_revision']:
            raise Failure('research_conflict', 'This task changed. Refresh and confirm deletion again.')
        # Local removal remains available after permission loss or source removal.
        # It does not require execution grants or parse an obsolete definition.
        return None

    def delete(self, args):
        """Purge one research task atomically after its worker has been joined.

        Small intent receipts survive so a lost response cannot repeat a change.
        Other job kinds own notebook/delivery history and cannot use this purge.
        """
        with self.store.jobs._transaction():
            prior = self.delete_check(args)
            if prior is not None:
                return prior
            ident = args['job_id']
            runs = 'SELECT id FROM background_runs WHERE job_id=?'
            if self.db.execute("SELECT 1 FROM background_runs WHERE job_id=? AND status IN ('running','cancel_requested')", (ident,)).fetchone():
                raise Failure('native_busy', 'The running check must finish stopping before deletion.')
            artifacts = self.db.execute(f'''SELECT result_id FROM background_runs WHERE job_id=? AND result_id IS NOT NULL
                UNION SELECT result_id FROM background_mcp_calls WHERE run_id IN ({runs}) AND result_id IS NOT NULL
                UNION SELECT json_extract(payload,'$.artifact_id') FROM background_run_events
                WHERE run_id IN ({runs}) AND kind='report_draft' ''', (ident, ident, ident)).fetchall()
            self.db.execute('DELETE FROM research_finding_versions WHERE finding_id IN (SELECT id FROM research_findings WHERE job_id=?)', (ident,))
            self.db.execute('DELETE FROM research_findings WHERE job_id=?', (ident,))
            self.db.execute('DELETE FROM research_presentations WHERE job_id=?', (ident,))
            for table in ('research_web_sources', 'background_mcp_calls', 'background_run_events'):
                self.db.execute(f'DELETE FROM {table} WHERE run_id IN ({runs})', (ident,))
            self.db.execute('DELETE FROM background_runs WHERE job_id=?', (ident,))
            self.db.execute('DELETE FROM background_job_versions WHERE job_id=?', (ident,))
            self.db.execute('DELETE FROM background_jobs WHERE id=?', (ident,))
            # Payloads are uniquely created for each result, never content-deduped.
            # FK enforcement rolls back the whole purge if an unexpected owner
            # references one; chat, notebook and filesystem output are not owners
            # this operation is allowed to remove.
            for table, column in (('artifact_chunks', 'artifact_id'), ('artifact_payloads', 'artifact_id'), ('artifacts', 'id')):
                self.db.executemany(f'DELETE FROM {table} WHERE {column}=?', artifacts)
            value = dict(status='deleted', job_id=ident, mutation_key=args['mutation_key'])
            self.db.execute('INSERT INTO research_operations VALUES(?,?,?)', (args['mutation_key'], digest(args), encode(value)))
            self.bump()
            return value

    def local(self,args,*,precondition=None,result_check=None):
        """Local attention controls do not grant execution or rewrite model findings."""
        action=args['action']
        if action=='delete':return self.delete(args)
        if action=='overview':return self.overview(args.get('offset',0),args.get('limit',20))
        if action=='browse':return self.browse(args['job_id'],args.get('offset',0),args.get('limit',12),args.get('view','inbox'),args.get('query',''))
        if action=='status':
            value=self.listing(limit=1)
            return dict(total=value['total'],new_results=value['new_results'])
        if action=='list':return self.listing(args.get('offset',0),args.get('limit',20))
        if action=='read':
            item=self.item(args['job_id']);summary=coverage=None
            if item['last_run']:
                row=self.store.jobs.run(item['last_run']['id'])
                if row['result_id']:
                    value=json.loads(self.store._body(row['result_id']))
                    summary=value['summary'];coverage=value['coverage']
            return dict(task=item,summary=summary,coverage=coverage)
        if action=='findings':return self.findings(args['job_id'],args.get('offset',0),args.get('limit',20))
        key=args['mutation_key'];intent=digest(args)
        if action in ('pause','resume'):
            previous=self.db.execute('SELECT intent,receipt FROM research_operations WHERE mutation_key=?',(key,)).fetchone()
            if previous:
                if previous['intent']!=intent:raise Failure('research_conflict','This change key belongs to another action.')
                return json.loads(previous['receipt'])
            definition=replace(self._definition(args['job_id']),enabled=action=='resume')
            def saved(receipt):
                self.db.execute('INSERT INTO research_operations VALUES(?,?,?)',(key,intent,encode(receipt)))
            return self.store.jobs.save(definition,job_id=args['job_id'],expected_revision=args['expected_revision'],
                mutation_key=key,now=now(),result_check=saved)
        with self.store.jobs._transaction():
            previous=self.db.execute('SELECT intent,receipt FROM research_operations WHERE mutation_key=?',(key,)).fetchone()
            if previous:
                if previous['intent']!=intent:raise Failure('research_conflict','This attention change key belongs to a different action.')
                receipt=json.loads(previous['receipt'])
                if result_check is not None:result_check(receipt)
                return receipt
            if precondition is not None:precondition()
            if action=='seen':
                self._definition(args['job_id'])
                # Bind acknowledgement to exactly the result the user opened.
                run=self.store.jobs.run(args['run_id'])
                if run['status'] not in ('completed','failed','cancelled','interrupted','deferred'):
                    raise Failure('research_conflict','Only a finished result can be marked reviewed.')
                if run['job_id']!=args['job_id']:raise Failure('research_conflict','The result belongs to another assignment.')
                self.db.execute('''INSERT INTO research_presentations VALUES(?,?) ON CONFLICT(job_id) DO UPDATE
                    SET seen_run_id=excluded.seen_run_id WHERE
                    (SELECT sequence FROM background_runs WHERE id=research_presentations.seen_run_id) <= ?''',
                    (args['job_id'],args['run_id'],run['sequence']))
                self.db.execute('''UPDATE research_findings SET seen_revision=max(seen_revision,
                    (SELECT max(v.revision) FROM research_finding_versions v WHERE v.finding_id=research_findings.id AND v.run_id=?))
                    WHERE job_id=? AND id IN (SELECT finding_id FROM research_finding_versions WHERE run_id=?)''',
                    (args['run_id'],args['job_id'],args['run_id']))
            elif action in ('disposition','review'):
                row=self.db.execute('SELECT revision FROM research_findings WHERE id=?',(args['finding_id'],)).fetchone()
                if row is None or row[0]!=args['expected_revision']:
                    raise Failure('research_conflict','The finding changed; reopen it before updating attention.')
                if action=='review':
                    # Acknowledge only the exact version displayed. A later update
                    # or a lost response must never silently clear newer evidence.
                    self.db.execute('UPDATE research_findings SET seen_revision=revision WHERE id=?',(args['finding_id'],))
                else:
                    if args['disposition'] not in ('open','saved','dismissed'):
                        raise Failure('research_arguments','Choose open, saved or dismissed.')
                    self.db.execute('UPDATE research_findings SET disposition=? WHERE id=?',(args['disposition'],args['finding_id']))
            else:raise Failure('research_arguments','Unknown attention action.')
            value=dict(status='committed',mutation_key=key)
            if result_check is not None:result_check(value)
            self.db.execute('INSERT INTO research_operations VALUES(?,?,?)',(key,intent,encode(value)))
            self.bump()
            return value


class ResearchWebSources:
    """WebSession storage adapter; transport, paging and accounting stay shared."""
    def __init__(self, research, run_id):
        self.research=research;self.store=research.store;self.db=research.db;self.run_id=run_id
        self.usage=self.store.web.usage

    def settings(self):return self.store.web.settings()

    def guard(self,cid,revision):
        definition=self.store.jobs.check_run(self.run_id)
        if cid!=definition.conversation_id:raise Failure('web_forbidden','Research source conversation changed.')
        self.research.guard(definition)
        if not self.settings()['enabled'] or self.settings()['revision']!=revision:raise Failure('web_forbidden','Web settings changed during research.')

    def save(self,run_id,provider,kind,request,metadata,content):
        definition=self.store.jobs.check_run(self.run_id);self.research.guard(definition)
        if run_id!=self.run_id or kind not in ('search','page') or not isinstance(content,str):
            raise Failure('web_source','Invalid research source origin.')
        if len(encode([request,metadata,content]).encode())>MAX_SOURCE_BYTES:
            raise Failure('web_source_size','Fetched source exceeds the supported snapshot size.')
        if self.db.execute('SELECT count(*) FROM research_web_sources WHERE run_id=?',(run_id,)).fetchone()[0]>=definition.limits.operations:
            raise Failure('web_source_limit','This research run reached its fetched-source allowance.')
        ident=uuid.uuid4().hex;created=now();binding=digest([ident,run_id,provider,kind,request,metadata,content,created])
        with self.db:
            self.db.execute('INSERT INTO research_web_sources VALUES(?,?,?,?,?,?,?,?,?,?)',
                (ident,run_id,provider,kind,encode(request),encode(metadata),content,hashlib.sha256(content.encode()).hexdigest(),binding,created))
        return ident

    def load(self,ident,cid):
        definition=self.store.jobs.check_run(self.run_id);self.research.guard(definition)
        if cid!=definition.conversation_id:raise Failure('web_forbidden','Source belongs to another assignment.')
        size=self.db.execute('''SELECT length(CAST(request AS BLOB))+length(CAST(metadata AS BLOB))+length(CAST(content AS BLOB))
            FROM research_web_sources WHERE id=? AND run_id=?''',(ident,self.run_id)).fetchone()
        if size and size[0]>MAX_SOURCE_BYTES:
            raise Failure('web_source_integrity','The saved research source exceeds its supported size.')
        row=self.db.execute('SELECT * FROM research_web_sources WHERE id=? AND run_id=?',(ident,self.run_id)).fetchone()
        if row is None:raise Failure('web_source_unavailable','This source is not owned by the current research run.')
        value=dict(row);value['request']=json.loads(value['request']);value['metadata']=json.loads(value['metadata'])
        if (value['binding']!=digest([value[k] for k in ('id','run_id','provider','kind','request','metadata','content','created_at')])
                or value['sha256']!=hashlib.sha256(value['content'].encode()).hexdigest()):
            raise Failure('web_source_integrity','The saved research source could not be verified.')
        return value
