"""Finite native workspace controls; UI layout never grants model authority."""
import json

from ..core.workspace import encoded
from ..core.planner import suggestions, study_preferences
from ..core.records import Failure

FIELDS={
    'snapshot':(set(),set()),
    'settings.read':(set(),set()),
    'settings.save':({'settings','expected_revision','expected_layout_revision','mutation_key'},set()),
    'layout.save':({'widgets','expected_revision','mutation_key'},set()),
    'record.page':({'kind'},{'offset','limit','include_cancelled'}),
    'record.read':({'kind','id'},set()),
    'record.save':({'kind','data','mutation_key'},{'id','expected_revision','cancelled','allow_conflicts'}),
    'operation.lookup':({'mutation_key'},set()),
    'calendar.view':({'first','last'},{'deadline_offset','timezone'}),
    'calendar.preview':({'kind','data'},{'id'}),
    'calendar.suggest':({'first','last','minutes'},{'count','availability'}),
    'materials.document':({'course_id','path'},{'offset','sha256'}),
    'materials.list':({'course_id'},{'path','offset','limit'}),
    'materials.read':({'course_id','path'},{'page','offset','maximum','view'}),
    'materials.snapshot':({'course_id','artifact_id'},{'offset','maximum'}),
    'widget.read':({'id'},{'offset','refresh','conversation_id'}),
    'media.command':({'command'},{'intent_key'}),
    'calendars.list':(set(),set()),
    'widget.sources':({'id','conversation_id'},{'offset'}),
    'widget.source':({'id','conversation_id','read_ref'},{'offset','maximum'}),
}
WRITES={'settings.save','record.save'}


def decode(action,raw):
    def unique(pairs):
        result=dict(pairs)
        if len(result)!=len(pairs):raise ValueError('Duplicate fields are not permitted.')
        return result
    try:
        if action not in FIELDS or not isinstance(raw,str) or len(raw.encode())>65536:
            raise ValueError('Use a supported bounded workspace action.')
        value=json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Non-finite numbers are invalid.')))
        required,optional=FIELDS[action]
        if not isinstance(value,dict) or not required<=set(value) or set(value)-required-optional:
            raise ValueError('Use exactly the supported fields for this workspace action.')
        from ..core.planner import identity
        for field in ('id','course_id','artifact_id','conversation_id'):
            if field in value:identity(value[field])
        for field in ('offset','deadline_offset','limit','maximum','page','expected_revision','expected_layout_revision'):
            if field in value and (type(value[field]) is not int or not 0<=value[field]<2**63):
                raise ValueError('Workspace positions, revisions and sizes must be bounded nonnegative integers.')
        for field in ('include_cancelled','cancelled','allow_conflicts','refresh'):
            if field in value and type(value[field]) is not bool:raise ValueError('Workspace switches must be explicit booleans.')
        for field in ('data','settings','availability'):
            if field in value and not isinstance(value[field],dict):raise ValueError('Use a structured '+field+' object.')
        for field in ('first','last','path','command','mutation_key','intent_key','timezone','read_ref','kind'):
            if field in value and (not isinstance(value[field],str) or '\x00' in value[field] or len(value[field].encode())>4096):
                raise ValueError('Use bounded text fields without NUL.')
        return value
    except (ValueError,TypeError,UnicodeError) as error:
        raise Failure('workspace_arguments',str(error)) from None


class WorkspaceControls:
    def __init__(self,app,*,materials,media):
        self.app=app
        self.store=app.store.planner
        self.materials=materials
        self.media=media

    async def dispatch(self,action,raw):
        args=decode(action,raw)
        effect=action=='media.command' and self.media.preflight(args['command'])
        if action in WRITES or effect:
            if self.app.tasks or self.app.store.active_turn_id() is not None:
                raise Failure('native_busy','Finish the current reply before changing workspace data.')
            await self.app.background.foreground()
            await self.app.titles.cancel()
            self.app.service.working_context.clear()
        try:
            result=await self.execute(action,args)
        except (ValueError,TypeError,KeyError) as error:
            raise Failure('workspace_arguments',str(error)) from None
        body=encoded(result)
        if len(body.encode())>2000000:
            raise Failure('workspace_capacity','This complete result is too large; choose a smaller page.')
        return dict(result_json=body)

    async def execute(self,action,args,*,origin=None,fit=None):
        store=self.store
        if action=='snapshot':
            return dict(**store.settings(),semesters=store._all('semester'),courses=store._all('course'))
        if action=='settings.read':return store.settings()
        if action=='layout.save':return store.save_layout(args['widgets'],args['expected_revision'],args['mutation_key'])
        if action=='settings.save':
            previous=store.settings()['settings']
            receipt=store.save_settings(args['settings'],args['expected_revision'],args['mutation_key'],layout_revision=args['expected_layout_revision'])
            if self.media is not None:
                warnings=await self.media.permissions_changed(previous,args['settings'])
                if warnings:receipt=dict(receipt,warnings=warnings)
            return receipt
        if action=='media.command':return await self.media.command(args['command'],intent_key=args.get('intent_key'))
        if action=='calendars.list':return await self.media.calendars()
        if action in ('widget.sources','widget.source'):
            pinned=await self.widget(dict(id=args['id'],conversation_id=args['conversation_id']))
            if pinned.get('kind')!='note' or pinned.get('status')!='ready':
                raise Failure('widget_source_unavailable','The pinned original note is unavailable under current access.')
            memory=self.app.store.memory
            snapshot,ledger=self.app.store.notebook._scope(args['conversation_id'])
            with memory.bounded(ledger):
                bank=memory._check(snapshot,ledger)
                if action=='widget.sources':
                    row=memory._revision(snapshot,pinned['data']['note_id'],pinned['data']['revision_id'],ledger,manual=True)
                    return memory._provenance(snapshot,row,'current',bank['handle_key'],args.get('offset',0),ledger,16384)
                offset,maximum=args.get('offset',0),args.get('maximum',16384)
                if type(offset) is not int or not 0<=offset<2**63 or type(maximum) is not int or not 1<=maximum<=32768:
                    raise Failure('widget_source_page','Choose an exact bounded source page.')
                return memory.read(snapshot,dict(read_ref=args['read_ref'],offset=offset,max_bytes=maximum),ledger,maximum)
        if action=='operation.lookup':return store.lookup(args['mutation_key'])
        if action=='record.page':
            return store.page(args['kind'],offset=args.get('offset',0),limit=args.get('limit',50),
                              include_cancelled=args.get('include_cancelled',False))
        if action=='record.read':return store.read(args['kind'],args['id'])
        if action=='record.save':
            if args['kind']=='course' and args['data'].get('folder'):
                self.materials.validate_folder(args['data']['folder'])
            if args['kind']=='practice':
                # A saved snapshot does not perpetuate access after Files revocation.
                store._live('course',args['data']['course_id'])
                for source in args['data']['sources']:
                    self.materials.source_path(source['path'])
            return store.save(args['kind'],args['data'],args['mutation_key'],ident=args.get('id'),
                revision=args.get('expected_revision'),cancelled=args.get('cancelled',False),allow_conflicts=args.get('allow_conflicts',False),origin=origin,fit=fit)
        if action=='calendar.preview':
            value=store._validate(args['kind'],args['data'])
            return dict(data=value,conflicts=store.preview(args['kind'],value,exclude=args.get('id')))
        if action=='calendar.view':
            from datetime import timedelta
            from zoneinfo import ZoneInfo
            from ..core.schedules import instant
            first,last=instant(args['first']),instant(args['last'])
            zone=ZoneInfo(args.get('timezone',store.settings()['settings']['availability']['timezone']))
            offset=args.get('deadline_offset',0)
            if type(offset) is not int or not 0<=offset<=10000:
                raise Failure('workspace_arguments','Invalid deadline page.')
            rows=self.app.store.db.execute('SELECT * FROM personal_tasks WHERE outcome=\'open\' AND due>=? AND due<=? ORDER BY due,id LIMIT 101 OFFSET ?',
                (first.astimezone(zone).date().isoformat(),(last-timedelta(microseconds=1)).astimezone(zone).date().isoformat(),offset)).fetchall()
            links={}
            for row in store._all('task_link'):
                links.setdefault(row['data']['task_id'],[]).append(row['data'])
            deadlines=[]
            for row in rows[:100]:
                linked=links.get(row['id'],[])
                deadlines.append(dict(self.app.store.personal_tasks._record(row),course_links=linked,
                    course_link=linked[0] if len(linked)==1 else None))
            return dict(occurrences=store.calendar(args['first'],args['last']),deadlines=deadlines,
                        deadline_next_offset=offset+100 if len(rows)>100 else None,
                        calendar_scope='Kejtu timetable only; external calendars shown in widgets are not included.',
                        reminder_notice='Reminder scheduling runs while Kejtu is open and coalesces missed occurrences on return. Timed alert presentation is not connected yet; saving a reminder does not promise a notification.')
        if action=='calendar.suggest':
            saved=store.settings()['settings']['availability']
            # Retired weekly-window settings must not silently constrain a plan
            # after their editor is removed. Only this request can supply hours.
            preferences=args.get('availability',dict(saved,windows=[]))
            effective=study_preferences(preferences)
            provisional=not preferences['windows']
            result=suggestions(args['first'],args['last'],effective,store.calendar(args['first'],args['last']),
                               minutes=args['minutes'],count=args.get('count',2))
            return dict(suggestions=result,requested=args.get('count',2),found=len(result),reserved=False,
                        availability=effective,availability_source='provisional' if provisional else 'request',
                        calendar_scope='Checked against Kejtu timetable only; external calendar appointments may still conflict.',
                        notice=('Trying 09:00–18:00 each day, including weekends, in '+effective['timezone']+
                                '. These are tentative suggestions; check that the times suit you. Describe different hours in chat.') if provisional else None)
        if action=='materials.document':
            return self.materials.document(args['course_id'],args['path'],offset=args.get('offset',0),sha256=args.get('sha256'))
        if action=='materials.list':
            return self.materials.listing(args['course_id'],args.get('path',''),offset=args.get('offset',0),limit=args.get('limit',50))
        if action=='materials.read':
            return await self.materials.read(args['course_id'],args['path'],page=args.get('page',1),
                offset=args.get('offset',0),maximum=args.get('maximum',16384),view=args.get('view','text'))
        if action=='materials.snapshot':
            return self.materials.snapshot(args['course_id'],args['artifact_id'],offset=args.get('offset',0),maximum=args.get('maximum',16384))
        if action=='widget.read':return await self.widget(args)
        raise Failure('workspace_arguments','Unsupported workspace action.')

    async def widget(self,args):
        settings=self.store.settings()['settings']
        widget=next((row for row in settings['widgets'] if row['id']==args['id']),None)
        if widget is None:raise Failure('widget_missing','This widget is no longer in the saved arrangement.')
        kind=widget['kind']
        if kind=='bookmarks':return dict(status='ready',kind=kind,data=widget['config'])
        if kind=='university':
            return dict(status='ready' if settings['university_enabled'] else 'disabled',kind=kind,
                        data=dict(semesters=self.store._all('semester'),courses=self.store._all('course')) if settings['university_enabled'] else None)
        if kind=='tasks':
            return dict(status='ready',kind=kind,data=self.app.store.personal_tasks.execute(
                dict(action='list',outcome='open',attention='active',offset=args.get('offset',0),limit=5)))
        if kind=='note':
            ident=widget['config']['note_id']
            if ident is None:return dict(status='empty',kind=kind,data=None)
            if not args.get('conversation_id'):
                return dict(status='unavailable',kind=kind,data=None,reason='Choose a conversation with notebook access.')
            row=self.app.store.db.execute('SELECT head_revision FROM memory_records WHERE id=? AND readable=1',(ident,)).fetchone()
            if row is None:return dict(status='unavailable',kind=kind,data=None)
            # Preview, original and sources use the current notebook/grant owner.
            notebook=self.app.store.notebook
            try:
                snapshot,ledger=notebook._scope(args['conversation_id'])
                bank=self.app.store.memory._check(snapshot,ledger)
                token=self.app.store.memory._token(snapshot,bank['handle_key'],['notebook_view'])
                body=notebook.read(args['conversation_id'],ident,row['head_revision'],token,0,512)
            except Failure as error:
                return dict(status='unavailable',kind=kind,data=None,reason=error.detail)
            return dict(status='ready',kind=kind,data=dict(body,view_token=token))
        if kind=='radio':
            if not settings['radio_enabled']:return dict(status='disabled',kind=kind,data=None)
            if self.media is None:return dict(status='unavailable',kind=kind,data=None)
            return dict(status='ready',kind=kind,data=await self.media.command('radio status'))
        if kind=='calendar':
            if not settings['calendar_enabled']:return dict(status='disabled',kind=kind,data=None)
            if self.media is None:return dict(status='unavailable',kind=kind,data=None)
            return await self.media.agenda(args.get('offset',0),args.get('refresh',False))
        raise Failure('widget_kind','Unsupported widget kind.')
