"""Foreground-only workspace authority inside the shared operation ledger."""
from dataclasses import asdict, fields
import hashlib
import json
from pathlib import Path

from ..core.planner import Course, Event, Semester, KINDS
from ..core.records import Failure
from ..core.vision import supported
from ..core.workspace import READS
from .workspace import FIELDS, decode


def enabled(controls):
    if controls is None:return False
    settings=controls.store.settings()['settings']
    return settings['model_read'] and any(settings[k] for k in ('university_enabled','radio_enabled','video_enabled'))


class WorkspaceSession:
    def __init__(self,controls,conversation_id,turn_id,ledger,*,tasks=None,skills=None):
        self.controls,self.store,self.cid,self.tid,self.ledger=controls,controls.store,conversation_id,turn_id,ledger
        self.tasks=tasks
        self.skills=skills
        self.revision=self.store.epoch()
        self.files_revision=self._files_epoch()
        self.reads=set()
        self.sources=set()
        self.user=self.store.store.turn(turn_id).user

    def check(self):
        if self.store.epoch()!=self.revision or not enabled(self.controls):
            raise Failure('workspace_changed','Workspace data or permissions changed outside this turn; use current state in a new message.')
        if not self.store.store.conversation(self.cid).history_enabled:
            raise Failure('workspace_forbidden','Conversation execution permission was revoked.')
        if self._files_epoch()!=self.files_revision:
            raise Failure('workspace_changed','Files permissions changed; previously delivered workspace sources cannot be reused in this turn.')

    def _files_epoch(self):
        files=self.controls.materials.files
        if files is None:return None
        try:
            return files.host.connections.read()['revision']
        except Failure as error:
            if error.code!='mcp_unavailable':raise
            # Unavailable local tools must not block chat or archive-only project
            # operations. This is distinct from every real permissions revision
            # and from a missing adapter, so availability changes invalidate the
            # turn. Material operations still consult their own Files authority;
            # no saved configuration is treated as empty or rewritten here.
            return 'mcp_unavailable'

    def clear_reads(self):
        self.reads.clear();self.sources.clear()

    def _quote(self,quote):
        if len(quote.strip())<3 or quote not in self.user:
            raise Failure('workspace_source_required','Use an exact quote from the current user request, not from a document, tool result or older message.')
        start=len(self.user[:self.user.index(quote)].encode())
        source=self.store.db.execute('SELECT user_id FROM turns WHERE id=?',(self.tid,)).fetchone()
        return dict(turn_id=self.tid,source_id=source['user_id'],offset=start,end=start+len(quote.encode()),
                    quote_sha256=hashlib.sha256(quote.encode()).hexdigest())

    def _record_access(self,row):
        data=row['data']
        if row['kind']=='task_link':self._task(data['task_id'])
        if row['kind']=='practice':
            self.store._live('course',data['course_id'])
            for source in data['sources']:
                self.controls.materials.source_path(source['path'])

    def _task(self,ident):
        if self.tasks is None:raise Failure('task_forbidden','Enable the existing personal-task permission to link or read original tasks.')
        self.tasks.check()
        record=self.store._task(ident)
        if record['source_status']=='unavailable':raise Failure('task_forbidden','The original task sources are no longer available.')
        if record['read_ref'] not in self.tasks.reads:
            raise Failure('task_read_required','Read this task through personal_tasks in a separate call before linking it.')

    def preflight(self,name,args):
        self.check()
        if name=='media':
            if self.controls.media is None:raise Failure('media_unavailable','Media controls are unavailable.')
            effect=self.controls.media.preflight(args['command'])
            if effect:self._quote(args['source_quote'])
            return
        settings=self.store.settings()['settings']
        if not settings['university_enabled']:raise Failure('university_disabled','Enable University in the local workspace settings.')
        action=args['action']
        if action not in READS|{'describe','record.save','operation.lookup'}:
            raise Failure('workspace_forbidden','This operation is a local control, not a conversation tool.')
        payload={} if action=='describe' else decode(action,args['payload_json'])
        if action=='describe' and args['payload_json'].strip()!='{}':
            raise Failure('workspace_arguments','describe takes an empty JSON object.')
        if action=='record.save':
            if not settings['model_write']:raise Failure('workspace_forbidden','Conversational workspace writing is disabled in local settings.')
            self._quote(args['source_quote'])
            if payload.get('allow_conflicts',False):
                raise Failure('planner_overlap','Review and explicitly allow an overlap in the local calendar controls.')
            kind,data=payload['kind'],payload['data']
            if not isinstance(data,dict):raise Failure('workspace_arguments','Use structured record data.')
            if payload.get('id') and (kind,payload['id'],payload.get('expected_revision')) not in self.reads:
                raise Failure('workspace_read_required','Read the complete current record in a separate call before editing it.')
            parent=('semester',data.get('semester_id')) if kind=='course' or kind=='task_link' and not data.get('course_id') else ('event',data.get('event_id')) if kind=='exception' else ('course',data.get('course_id'))
            if parent[1] and not any(item[:2]==parent for item in self.reads):
                raise Failure('workspace_read_required','Read the linked parent record in a separate call first.')
            if data.get('task_id'):self._task(data['task_id'])
            if kind=='practice' and any(source.get('artifact_id') not in self.sources for source in data.get('sources',[])):
                raise Failure('material_read_required','Read the exact practice sources in this turn before saving an attempt.')
        # Tool intent supplies a precise workflow boundary without guessing from
        # keywords. Refuse before effects until the model has the full procedure.
        if self.skills:
            procedure=None
            if action=='calendar.suggest' or action=='record.save' and payload['kind']=='event' and payload['data'].get('kind')=='study':
                procedure='kejtu-study-planning'
            elif action=='record.save' and payload['kind'] in ('semester','course'):
                procedure='kejtu-semester-organization'
            elif action=='record.save' and payload['kind']=='practice':procedure='kejtu-study-support'
            if procedure:self.skills.required(procedure)

    def observe(self,name,args,result):
        if name!='university' or result.get('status') in ('rejected','not_delivered','not_executed'):return
        action=args['action']
        rows=[result] if action=='record.read' else result.get('items',[]) if action=='record.page' else []
        for row in rows:
            if {'kind','id','revision','data'}<=row.keys():self.reads.add((row['kind'],row['id'],row['revision']))
        if action=='snapshot':
            for row in result.get('semesters',[])+result.get('courses',[]):
                self.reads.add((row['kind'],row['id'],row['revision']))
        if action in ('materials.read','materials.snapshot') and result.get('source'):
            self.sources.add(result['source']['artifact_id'])

    def describe(self):
        return dict(actions={key:dict(required=sorted(required),optional=sorted(optional))
                    for key,(required,optional) in FIELDS.items() if key in READS|{'record.save','operation.lookup'}},
            records={name:[field.name for field in fields(record)] for name,record in
                     [('semester',Semester),('course',Course),('event',Event)]},
            record_labels=dict(semester='project',course='section/module'),
            optional_grouping='course is a section/module, optionally under a semester (project). Project starts_on and ends_on may be null. Repeating events need a last date when the project has none.',
            examples=dict(semester=asdict(Semester('Winter','2026-10-01','2027-01-31','Europe/Vienna')),
                course=asdict(Course('Statistics','0'*32)),
                event=asdict(Event('Study','2026-10-20T10:00:00+02:00','2026-10-20T10:45:00+02:00','Europe/Vienna',kind='study'))),
            exception_fields=['event_id','occurrence_at','cancelled','starts_at','ends_at'],
            practice_fields=['course_id','topic','question','answer','feedback','confidence','basis','sources'],
            practice_basis=['user_report','exercise_attempt','model_feedback'],
            task_link_fields=['course_id','task_id','estimate_minutes','material'],
            task_link_project='For a task linked directly to a project, use course_id=null and add semester_id with that project identity. For a section, use course_id and omit semester_id.',
            event_types=list(KINDS),event_repeat=['none','daily','weekly'],
            meeting_types=['lecture','tutorial','seminar','lab'],
            fortnightly=dict(repeat='weekly',interval=2),
            source_fields=['path','sha256','page','artifact_id'],
            paging='Read material in exact pages of at most 32768 UTF-8 bytes; continue with next_offset. PDF page numbers start at 1.',
            notice='All date/time offsets must match timezone, including DST. first/last are an exclusive-end instant range. Task creation/completion and reminders use their existing tools. Settings, grants and layouts are local controls only.')

    async def operation(self,name,args,maximum,budget):
        self.check()
        def fit(result):
            if not budget.fits(result):raise Failure('workspace_result_capacity','The complete result does not fit; no workspace mutation committed. Request a smaller exact page.')
            return result
        try:
            self.preflight(name,args)
            if name=='media':
                # Reserve a complete bounded receipt before any player effect.
                fit(dict(domain='workspace',status='observed',detail='x'*2048))
                result=await self.controls.media.command(args['command'],intent_key='media:'+self.tid+':'+hashlib.sha256(args['command'].encode()).hexdigest())
                result=dict(domain='workspace',**result)
                if budget.fits(result):return result
                # Metadata is optional presentation. Keep the complete effect
                # outcome and actual readiness after execution, never mislabel a
                # committed player command as rejected because its title is long.
                data=result.get('data',{})
                compact=dict(domain='workspace',status=result['status'],player=result.get('player'),
                    data={k:v for k,v in data.items() if k in ('state','audio_output_ready','video_output_ready','volume','position_seconds')},
                    reason=result.get('reason'),replayed=result.get('replayed',False),
                    notice='Optional metadata omitted for context fit; status can be observed separately.')
                return fit(compact)
            action=args['action']
            if action=='describe':return fit(dict(domain='workspace',**self.describe()))
            payload=decode(action,args['payload_json'])
            origin=None
            if action=='record.save':
                origin=self._quote(args['source_quote'])
                payload['mutation_key']='workspace:'+self.tid+':'+hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            if action in ('materials.read','materials.snapshot'):
                payload['maximum']=min(payload.get('maximum',16384),max(1,maximum-2048))
            result=await self.controls.execute(action,payload,origin=origin,fit=fit)
            self.revision=self.store.epoch()
            if action=='snapshot':
                # University authority does not expose bookmark URLs, pinned-note
                # identities or selected external calendars from the local layout.
                result=dict(result,settings={k:v for k,v in result['settings'].items()
                    if k in ('university_enabled','model_write','radio_enabled','video_enabled','availability')})
                result.pop('layout_revision',None)
                # This is a settings revision, not a workspace-wide change
                # counter. Missing record collections are not empty collections.
                result['settings_revision']=result.pop('revision')
                result['coverage']=dict(included_records=['semester','course'],
                    other_records='not_loaded',
                    notice='Only active period and project records are included. '
                    'Events, occurrence changes, practice and task links are not loaded. '
                    'Use record.page for those records and calendar.view for dated occurrences. '
                    'settings_revision changes only with settings; it cannot prove that records are unchanged.')
            if action=='calendar.view':
                if self.tasks is None:
                    result=dict(result,deadlines=[],deadline_next_offset=None,task_notice='Original task access is not granted.')
                else:
                    self.tasks.check()
                    result['deadlines']=[r for r in result['deadlines'] if r['source_status']!='unavailable']
            if action=='record.read':self._record_access(result)
            if action=='record.page':
                for row in result['items']:self._record_access(row)
            if 'image' in result:
                image=result['image']
                result={k:v for k,v in result.items() if k!='image'}
                included=supported(self.ledger.profile.provider,self.ledger.profile.model)
                result.update(encoding='Workspace visual result v1',visual_inputs=[image] if included else [],
                    visual_status='included' if included else 'not_included',
                    visual_reason=None if included else 'model_image_input_unavailable')
            return fit(dict(domain='workspace',**result))
        except (ValueError,TypeError,KeyError) as error:
            return dict(domain='workspace',status='rejected',reason='workspace_arguments',recovery=str(error))
        except Failure as error:
            if error.code in ('workspace_changed','workspace_forbidden'):raise
            return dict(domain='workspace',status='rejected',reason=error.code,recovery=error.detail)
