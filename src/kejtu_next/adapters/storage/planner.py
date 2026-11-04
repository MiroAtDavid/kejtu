"""Private course/calendar/layout records; tasks and source bodies retain owners."""
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid

from ...core.planner import Course, Event, Semester, availability, conflicts, day, expand, identity, text
from ...core.records import Failure
from ...core.schedules import instant, utc

PLANNER_SCHEMA = '''
CREATE TABLE planner_records(kind TEXT NOT NULL,id TEXT PRIMARY KEY,revision INTEGER NOT NULL,
 body TEXT NOT NULL,cancelled INTEGER NOT NULL CHECK(cancelled IN (0,1)),created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX planner_kind ON planner_records(kind,cancelled,id);
CREATE TABLE planner_operations(mutation_key TEXT PRIMARY KEY,intent TEXT NOT NULL,receipt TEXT NOT NULL);
CREATE TABLE planner_settings(singleton INTEGER PRIMARY KEY CHECK(singleton=1),revision INTEGER NOT NULL,body TEXT NOT NULL);
CREATE TABLE planner_layout(singleton INTEGER PRIMARY KEY CHECK(singleton=1),revision INTEGER NOT NULL,widgets TEXT NOT NULL);
CREATE TABLE planner_material(artifact_id TEXT PRIMARY KEY REFERENCES artifacts(id),course_id TEXT NOT NULL REFERENCES planner_records(id),
 path TEXT NOT NULL,source_sha256 TEXT NOT NULL,page INTEGER NOT NULL,representation TEXT NOT NULL,
 UNIQUE(course_id,path,source_sha256,page,representation));
CREATE TABLE media_tracks(id INTEGER PRIMARY KEY,session_id TEXT NOT NULL,station TEXT NOT NULL,
 title TEXT NOT NULL,observed_at TEXT NOT NULL);
CREATE INDEX media_track_session ON media_tracks(session_id,id);
'''
KINDS = ('semester','course','event','exception','practice','task_link')
WIDGETS = ('radio','note','calendar','bookmarks','university','tasks')
MAX_RECORDS = 10000


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def defaults():
    return dict(university_enabled=False,model_read=False,model_write=False,widgets=[],
                radio_enabled=False,video_enabled=False,calendar_enabled=False,calendars=[],
                availability=dict(timezone='UTC',windows=[],buffer_minutes=15))


def validate_settings(value):
    if not isinstance(value,dict) or set(value) != set(defaults()):
        raise ValueError('Use complete workspace settings.')
    for key in ('university_enabled','model_read','model_write','radio_enabled','video_enabled','calendar_enabled'):
        if type(value[key]) is not bool:
            raise ValueError('Workspace permissions must be explicit booleans.')
    if value['model_write'] and not value['model_read']:
        raise ValueError('Model writing requires model reading.')
    availability(value['availability'])
    if not isinstance(value['calendars'],list) or len(value['calendars']) > 32:
        raise ValueError('Select at most 32 external calendars.')
    for name in value['calendars']:
        text(name,'Calendar identity',512)
    if not isinstance(value['widgets'],list) or len(value['widgets']) > 12:
        raise ValueError('Choose at most twelve widgets.')
    seen=set()
    for widget in value['widgets']:
        if (not isinstance(widget,dict) or set(widget) != {'id','kind','side','size','collapsed','config'}
                or widget['kind'] not in WIDGETS or widget['side'] not in ('left','right')
                or widget['size'] not in ('compact','comfortable') or type(widget['collapsed']) is not bool
                or not isinstance(widget['config'],dict)):
            raise ValueError('Invalid widget arrangement.')
        identity(widget['id'])
        if widget['id'] in seen:
            raise ValueError('Each widget needs a unique identity.')
        seen.add(widget['id'])
        config=widget['config']
        if widget['kind']=='note':
            if set(config) != {'note_id'} or config['note_id'] is not None and not isinstance(config['note_id'],str):
                raise ValueError('A note widget references one original note.')
            if config['note_id'] is not None:identity(config['note_id'])
        elif widget['kind']=='bookmarks':
            from urllib.parse import urlsplit
            if set(config) != {'links'} or not isinstance(config['links'],list) or len(config['links'])>20:
                raise ValueError('A bookmark widget holds at most twenty links.')
            for link in config['links']:
                if not isinstance(link,dict) or set(link) != {'title','url'}:
                    raise ValueError('Each bookmark needs a title and URL.')
                text(link['title'],'Bookmark title',160)
                text(link['url'],'Bookmark URL',2048)
                parsed=urlsplit(link['url'])
                if (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username is not None
                        or parsed.password is not None or any(ord(c)<33 or ord(c)==127 for c in link['url'])
                        or '\\' in link['url']):
                    raise ValueError('Use an HTTP(S) bookmark without embedded credentials.')
        elif config:
            raise ValueError('This widget has no extra configuration.')
    return value


class PlannerStore:
    def __init__(self,store):
        self.store,self.db=store,store.db

    def epoch(self):
        row=self.db.execute('SELECT count(*),coalesce(sum(revision),0) FROM planner_records').fetchone()
        return (self.settings()['revision'],row[0],row[1])

    def settings(self):
        row=self.db.execute('SELECT revision,body FROM planner_settings WHERE singleton=1').fetchone()
        layout=self.layout()
        value=json.loads(row['body']) if row else defaults()
        return dict(revision=row['revision'] if row else 0,layout_revision=layout['revision'],
                    settings=dict(value,widgets=layout['widgets']))

    def layout(self):
        row=self.db.execute('SELECT revision,widgets FROM planner_layout WHERE singleton=1').fetchone()
        return dict(revision=row['revision'],widgets=json.loads(row['widgets'])) if row else dict(revision=0,widgets=[])

    def lookup(self,key):
        text(key,'Operation identity',256)
        row=self.db.execute('SELECT receipt FROM planner_operations WHERE mutation_key=?',(key,)).fetchone()
        return dict(json.loads(row['receipt']),replayed=True) if row else dict(status='absent')

    def material(self,course_id,path,digest,page,representation,body):
        """Save an immutable delivered projection; originals stay in their folder.

        Callers must enforce current Files grants before and after external IO.
        This method does not grant access merely because a snapshot exists.
        """
        self._live('course',course_id)
        row=self.db.execute('SELECT artifact_id FROM planner_material WHERE course_id=? AND path=? AND source_sha256=? AND page=? AND representation=?',
                            (course_id,path,digest,page,representation)).fetchone()
        if row:return row['artifact_id']
        if len(body.encode())>2*1024*1024:
            raise Failure('material_size','This complete source projection exceeds the supported size.')
        with self.db:
            artifact=self.store._put('course_material',body)
            self.db.execute('INSERT INTO planner_material VALUES(?,?,?,?,?,?)',(artifact,course_id,path,digest,page,representation))
        return artifact

    def _prior(self,key,intent):
        text(key,'Operation identity',256)
        row=self.db.execute('SELECT intent,receipt FROM planner_operations WHERE mutation_key=?',(key,)).fetchone()
        if row:
            if row['intent'] != intent:
                raise Failure('planner_conflict','This operation identity belongs to another intent; retain the draft.')
            return dict(json.loads(row['receipt']),replayed=True)

    def _receipt(self,key,intent,result):
        result=dict(result,mutation_key=key,intent_sha256=intent)
        self.db.execute('INSERT INTO planner_operations VALUES(?,?,?)',(key,intent,encoded(result)))
        return result

    def save_settings(self,value,revision,key,*,layout_revision=None):
        try:
            validate_settings(value)
            if type(revision) is not int or revision<0:
                raise ValueError('Supply the current settings revision.')
            if len(encoded(value).encode())>32768:
                raise ValueError('Workspace settings exceed the supported size.')
        except (ValueError,TypeError,KeyError) as error:
            raise Failure('planner_settings',str(error)) from None
        layout_revision=self.layout()['revision'] if layout_revision is None else layout_revision
        if type(layout_revision) is not int or layout_revision<0:raise Failure('planner_settings','Supply the current layout revision.')
        intent=hashlib.sha256(encoded(['settings',value,revision,layout_revision]).encode()).hexdigest()
        with self.db:
            prior=self._prior(key,intent)
            if prior:return prior
            current=self.settings()
            if current['revision'] != revision or current['layout_revision']!=layout_revision:
                raise Failure('planner_conflict','Workspace settings changed; refresh without discarding your draft.')
            permissions={k:v for k,v in value.items() if k!='widgets'}
            old_permissions={k:v for k,v in current['settings'].items() if k!='widgets'}
            next_revision=revision+int(permissions!=old_permissions)
            next_layout=layout_revision+int(value['widgets']!=current['settings']['widgets'])
            if next_revision!=revision:
                self.db.execute('INSERT OR REPLACE INTO planner_settings VALUES(1,?,?)',(next_revision,encoded(permissions)))
            if next_layout!=layout_revision:
                self.db.execute('INSERT OR REPLACE INTO planner_layout VALUES(1,?,?)',(next_layout,encoded(value['widgets'])))
            return self._receipt(key,intent,dict(status='saved',kind='settings',revision=next_revision,layout_revision=next_layout,replayed=False))

    def save_layout(self,widgets,revision,key):
        """Presentation never changes a permission epoch or a course record."""
        try:
            validate_settings(dict(defaults(),widgets=widgets))
            if len(encoded(widgets).encode())>32768:raise ValueError('The widget arrangement exceeds the supported size.')
            if type(revision) is not int or revision<0:raise ValueError('Supply the current layout revision.')
        except (ValueError,TypeError,KeyError) as error:raise Failure('planner_layout',str(error)) from None
        intent=hashlib.sha256(encoded(['layout',widgets,revision]).encode()).hexdigest()
        with self.db:
            prior=self._prior(key,intent)
            if prior:return prior
            current=self.layout()
            if current['revision']!=revision:raise Failure('planner_conflict','The widget arrangement changed; refresh while retaining your draft.')
            next_revision=revision+int(widgets!=current['widgets'])
            if next_revision!=revision:
                self.db.execute('INSERT OR REPLACE INTO planner_layout VALUES(1,?,?)',(next_revision,encoded(widgets)))
            return self._receipt(key,intent,dict(status='saved',kind='layout',revision=next_revision,replayed=False))

    def read(self,kind,ident):
        if kind not in KINDS:
            raise Failure('planner_kind','Choose a supported workspace record.')
        row=self.db.execute('SELECT * FROM planner_records WHERE kind=? AND id=?',(kind,ident)).fetchone()
        if row is None:
            raise Failure('planner_missing','This workspace record is unavailable.')
        return dict(id=row['id'],kind=row['kind'],revision=row['revision'],cancelled=bool(row['cancelled']),
                    created_at=row['created_at'],updated_at=row['updated_at'],data=json.loads(row['body']))

    def page(self,kind,*,offset=0,limit=50,include_cancelled=False):
        if kind not in KINDS or type(offset) is not int or not 0<=offset<=MAX_RECORDS or type(limit) is not int or not 1<=limit<=100:
            raise Failure('planner_page','Use a supported record kind and bounded page.')
        rows=self.db.execute('SELECT id FROM planner_records WHERE kind=? AND (? OR cancelled=0) ORDER BY created_at,id LIMIT ? OFFSET ?',
                             (kind,int(include_cancelled),limit+1,offset)).fetchall()
        return dict(items=[self.read(kind,row['id']) for row in rows[:limit]],next_offset=offset+limit if len(rows)>limit else None)

    def _all(self,kind):
        count=self.db.execute('SELECT count(*) FROM planner_records WHERE kind=? AND cancelled=0',(kind,)).fetchone()[0]
        if count>1000:
            raise Failure('planner_capacity','This view exceeds its 1,000-record bound; use paged records.')
        return [self.read(kind,row['id']) for row in self.db.execute(
            'SELECT id FROM planner_records WHERE kind=? AND cancelled=0 ORDER BY id',(kind,))]

    def _live(self,kind,ident):
        row=self.read(kind,ident)
        if row['cancelled']:
            raise Failure('planner_cancelled','The linked workspace record is cancelled.')
        return row

    def _task(self,ident):
        row=self.db.execute('SELECT * FROM personal_tasks WHERE id=?',(ident,)).fetchone()
        if row is None:
            raise Failure('planner_task_missing','The original personal task is unavailable.')
        return self.store.personal_tasks._record(row)

    def _task_links(self,task_id):
        rows=self.db.execute("SELECT id FROM planner_records WHERE kind='task_link' AND cancelled=0 AND json_extract(body,'$.task_id')=? ORDER BY id LIMIT 101",(task_id,)).fetchall()
        if len(rows)>100:
            raise Failure('planner_capacity','This task has too many links to edit together; manage its links individually.')
        return [self.read('task_link',row['id']) for row in rows]

    def task_placements(self,task_id):
        result=[]
        for row in self._task_links(task_id):
            section=row['data']['course_id']
            project=(self.read('course',section)['data']['semester_id'] if section else row['data'].get('semester_id'))
            result.append(dict(link_id=row['id'],revision=row['revision'],project_id=project,section_id=section))
        return result

    def placement_changed(self,task_id,placement):
        current=self.task_placements(task_id) if task_id else []
        expected=sorted(placement['expected_links'],key=lambda row:row['link_id'])
        actual=[{k:row[k] for k in ('link_id','revision')} for row in current]
        if expected!=actual:
            raise Failure('task_conflict','Task placement changed. Load the current task while keeping your draft.')
        project,section=placement['project_id'],placement['section_id']
        if project:self._live('semester',project)
        if section:
            course=self._live('course',section)
            if course['data']['semester_id']!=project:
                raise Failure('task_placement','Choose a section belonging to the selected project.')
        if not project and not section:return bool(current)
        return len(current)!=1 or any(current[0][k]!=placement[k] for k in ('project_id','section_id'))

    def set_task_placement(self,task_id,placement,key):
        if not self.db.in_transaction:
            raise Failure('task_transaction','Task placement requires the task save transaction.')
        for row in self._task_links(task_id):
            self.save('task_link',row['data'],hashlib.sha256((key+':unlink:'+row['id']).encode()).hexdigest()[:32],
                      ident=row['id'],revision=row['revision'],cancelled=True,transaction=False)
        if placement['project_id'] or placement['section_id']:
            data=dict(course_id=placement['section_id'],task_id=task_id,estimate_minutes=45,material=[])
            if not placement['section_id']:data['semester_id']=placement['project_id']
            self.save('task_link',data,hashlib.sha256((key+':placement').encode()).hexdigest()[:32],transaction=False)

    def _semester(self,course_id):
        course=self._live('course',course_id)
        parent=course['data']['semester_id']
        return Semester(**self._live('semester',parent)['data']) if parent else None

    def _validate(self,kind,value):
        if not isinstance(value,dict):
            raise ValueError('Use a structured workspace record.')
        if kind=='semester':
            return asdict(Semester(**value))
        if kind=='course':
            result=asdict(Course(**value))
            if result['semester_id'] is not None:self._live('semester',result['semester_id'])
            return result
        if kind=='event':
            item=Event(**value)
            semester=self._semester(item.course_id) if item.course_id else None
            if semester:
                local=datetime.fromisoformat(item.starts_at).date()
                if not semester.contains(local):
                    raise ValueError('The first project event must lie inside its period.')
                if item.until is None and item.repeat!='none' and semester.ends_on is not None:
                    item=Event(**dict(asdict(item),until=semester.ends_on))
                if item.until and semester.ends_on is not None and day(item.until)>day(semester.ends_on):
                    raise ValueError('A project recurrence must end within its period.')
            if item.repeat!='none' and item.until is None:
                raise ValueError('Choose an end date for the repeating commitment.')
            if item.until and (day(item.until)-datetime.fromisoformat(item.starts_at).date()).days>730:
                raise ValueError('Repeating commitments have a maximum two-year range.')
            if item.task_id:self._task(item.task_id)
            return asdict(item)
        if kind=='exception':
            if set(value) != {'event_id','occurrence_at','cancelled','starts_at','ends_at'} or type(value['cancelled']) is not bool:
                raise ValueError('A single-occurrence change needs exact event/time/cancellation fields.')
            item=Event(**self._live('event',identity(value['event_id']))['data'])
            original=instant(value['occurrence_at'])
            if original not in item.schedule.between(utc(original-timedelta(seconds=1)),utc(original+timedelta(seconds=1)),limit=1):
                raise ValueError('Choose an actual occurrence of this series.')
            if item.until and original.astimezone(__import__('zoneinfo').ZoneInfo(item.timezone)).date()>day(item.until):
                raise ValueError('This occurrence is beyond the end of the series.')
            if not value['cancelled']:
                Event(**dict(asdict(item),starts_at=value['starts_at'],ends_at=value['ends_at'],repeat='none',interval=1,until=None))
                semester=self._semester(item.course_id) if item.course_id else None
                if semester:
                    original_day=original.astimezone(__import__('zoneinfo').ZoneInfo(item.timezone)).date()
                    if not semester.contains(original_day) or item.kind=='class' and not semester.teaching(original_day):
                        raise ValueError('Choose an actual scheduled occurrence within the period.')
                    moved=instant(value['starts_at']).astimezone(__import__('zoneinfo').ZoneInfo(item.timezone)).date()
                    if not semester.contains(moved) or item.kind=='class' and not semester.teaching(moved):
                        raise ValueError('Move a project occurrence within its period and outside its breaks.')
            elif value['starts_at'] is not None or value['ends_at'] is not None:
                raise ValueError('A cancelled occurrence has no replacement times.')
            return dict(value,occurrence_at=utc(original))
        if kind=='task_link':
            if set(value) - {'semester_id'} != {'course_id','task_id','estimate_minutes','material'}:
                raise ValueError('A task link needs a project or section, task, estimate and material references.')
            if value['course_id'] is not None:
                self._live('course',identity(value['course_id']))
                if value.get('semester_id') is not None:
                    raise ValueError('Link either to a section or directly to a project.')
            else:
                self._live('semester',identity(value.get('semester_id')))
            self._task(identity(value['task_id']))
            if type(value['estimate_minutes']) is not int or not 5<=value['estimate_minutes']<=480:
                raise ValueError('Estimate 5–480 minutes, adjustable independently of bookings.')
            Event('Validation','2026-01-01T00:00:00Z','2026-01-01T00:05:00Z','UTC',material=value['material'])
            return value
        if kind=='practice':
            fields={'course_id','topic','question','answer','feedback','confidence','basis','sources'}
            if set(value)!=fields or value['basis'] not in ('user_report','exercise_attempt','model_feedback'):
                raise ValueError('Practice needs a course, topic, question, answer, feedback, confidence, basis and sources.')
            self._live('course',identity(value['course_id']))
            for name in ('topic','question','answer','feedback'):
                text(value[name],name,8192,empty=name in ('answer','feedback'))
            if value['confidence'] is not None and (type(value['confidence']) is not int or not 0<=value['confidence']<=5):
                raise ValueError('Confidence is an optional user report from 0–5.')
            if not isinstance(value['sources'],list) or not 1<=len(value['sources'])<=16:
                raise ValueError('Practice needs 1–16 exact source references.')
            for source in value['sources']:
                if not isinstance(source,dict) or set(source)-{'path','sha256','page','offset','artifact_id'}:
                    raise ValueError('Use delivered source references.')
                text(source.get('path'),'Source path',4096)
                digest=source.get('sha256')
                if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
                    raise ValueError('Practice sources need their exact SHA-256 identity.')
                saved=self.db.execute('SELECT * FROM planner_material WHERE artifact_id=? AND course_id=?',
                    (source.get('artifact_id'),value['course_id'])).fetchone()
                if (saved is None or saved['path']!=source['path'] or saved['source_sha256']!=digest
                        or saved['page']!=source.get('page',0)):
                    raise ValueError('Practice must reference a delivered original material projection from this course.')
            return value
        raise ValueError('Unknown workspace record kind.')

    def save(self,kind,value,key,*,ident=None,revision=None,cancelled=False,allow_conflicts=False,origin=None,fit=None,transaction=True):
        if kind not in KINDS or type(cancelled) is not bool or type(allow_conflicts) is not bool:
            raise Failure('planner_arguments','Invalid workspace save.')
        if ident is not None:
            try:identity(ident)
            except ValueError as error:raise Failure('planner_arguments',str(error)) from None
        intent=hashlib.sha256(encoded(['save',kind,ident,revision,value,cancelled,allow_conflicts,origin]).encode()).hexdigest()
        # Composite task saves use the task owner's existing transaction; nested
        # connection contexts would commit a placement before the task receipt.
        with self.db if transaction else nullcontext():
            prior=self._prior(key,intent)
            if prior:return prior
            old=self.read(kind,ident) if ident else None
            try:
                # Removing an unchanged existing task link must still work after
                # its project/section was removed. No new parent is authorized.
                removing_link=kind=='task_link' and cancelled and old is not None and value==old['data']
                if not removing_link:value=self._validate(kind,value)
                if len(encoded(value).encode())>32768:
                    raise ValueError('The complete workspace record exceeds 32 KB.')
            except (ValueError,TypeError,KeyError) as error:
                raise Failure('planner_arguments',str(error)) from None
            if old is not None and (type(revision) is not int or revision!=old['revision']):
                raise Failure('planner_conflict','This record changed; refresh without discarding your draft.')
            if old is None and revision is not None:
                raise Failure('planner_conflict','A new record has no previous revision.')
            if old is not None and kind=='exception' and any(
                    value[field]!=old['data'][field] for field in ('event_id','occurrence_at')):
                # Retargeting also restores the old occurrence, which is a
                # separate scheduling effect. Never hide it inside an edit.
                raise Failure('planner_conflict','An occurrence change keeps its original event and occurrence. Restore it separately before changing another occurrence.')
            if old is None and self.db.execute('SELECT count(*) FROM planner_records').fetchone()[0]>=MAX_RECORDS:
                raise Failure('planner_capacity','Workspace record limit reached; existing records remain available.')
            ident=ident or uuid.uuid4().hex
            if kind=='task_link' and not cancelled:
                duplicate=self.db.execute('SELECT 1 FROM planner_records WHERE kind=\'task_link\' AND cancelled=0 AND id<>? AND json_extract(body,\'$.task_id\')=? AND json_extract(body,\'$.course_id\') IS ? AND json_extract(body,\'$.semester_id\') IS ?',
                    (ident,value['task_id'],value['course_id'],value.get('semester_id'))).fetchone()
                if duplicate:raise Failure('planner_conflict','This original task is already linked to the course; edit its existing estimate.')
            if kind=='exception':
                for other in self._all('exception'):
                    if (other['id']!=ident and other['data']['event_id']==value['event_id']
                            and other['data']['occurrence_at']==value['occurrence_at']):
                        raise Failure('planner_conflict','This occurrence already has an exception; edit its existing record.')
            clashes=[]
            if kind in ('event','exception') and not cancelled:
                clashes=self.preview(kind,value,exclude=ident)
                if clashes and not allow_conflicts:
                    raise Failure('planner_overlap','The proposed time overlaps a commitment; preview conflicts or explicitly allow the overlap.')
            now=datetime.now(timezone.utc).isoformat()
            version=old['revision']+1 if old else 1
            affected=[]
            if old and kind in ('semester','course') and not cancelled:
                courses={ident} if kind=='course' else {row['id'] for row in self._all('course') if row['data']['semester_id']==ident}
                affected=[row for row in self._all('event') if row['data']['course_id'] in courses]
            before={}
            for row in affected:
                before[row['id']]=set() if old['cancelled'] else {encoded(item) for item in self.preview('event',row['data'],exclude=row['id'])}
            result=dict(status='saved',kind=kind,id=ident,revision=version,replayed=False,conflicts=clashes,
                        mutation_key=key,intent_sha256=intent)
            if origin is not None:result['origin']=origin
            # Admit the entire effect receipt before touching either record or
            # operation log. A small supported context is not a partial write.
            if fit is not None:fit(dict(domain='workspace',**result))
            self.db.execute('INSERT OR REPLACE INTO planner_records VALUES(?,?,?,?,?,?,?)',
                (kind,ident,version,encoded(value),int(cancelled),old['created_at'] if old else now,now))
            if kind=='event' and not cancelled:
                # A series edit must preserve the identity/bounds of every
                # retained occurrence change. Roll back this row and its receipt
                # if a shortened or reparented series invalidates a child.
                for change in self._all('exception'):
                    if change['data']['event_id']==ident:
                        try:self._validate('exception',change['data'])
                        except (ValueError,TypeError,KeyError) as error:
                            raise Failure('planner_parent_conflict','This series change invalidates a saved occurrence change: '+str(error)) from None
            # Parent edits can restore teaching days or move a course between
            # semesters. Validate their actual impact in the SAME transaction;
            # failure rolls back both the tentative row and receipt.
            for row in affected:
                try:self._validate('event',row['data'])
                except (ValueError,TypeError,KeyError) as error:raise Failure('planner_parent_conflict',str(error)) from None
                for change in self._all('exception'):
                    if change['data']['event_id']==row['id']:
                        try:self._validate('exception',change['data'])
                        except (ValueError,TypeError,KeyError) as error:raise Failure('planner_parent_conflict',str(error)) from None
                added=[item for item in self.preview('event',row['data'],exclude=row['id']) if encoded(item) not in before[row['id']]]
                if added:
                    raise Failure('planner_parent_conflict','This period/project change introduces schedule overlaps; adjust the affected commitments first.')
            if kind=='exception' and cancelled:
                event=self._live('event',value['event_id'])
                anchor=instant(value['occurrence_at'])
                events=self.calendar(utc(anchor-timedelta(days=1)),utc(anchor+timedelta(days=7)))
                restored=[row for row in events if row['event_id']==event['id'] and row['occurrence_at']==value['occurrence_at']]
                if not allow_conflicts and any(conflicts(row,events) for row in restored):
                    raise Failure('planner_overlap','Restoring the original occurrence would overlap a commitment; preview it or explicitly allow the overlap.')
            return self._receipt(key,intent,result)

    def calendar(self,first,last,*,exclude=None):
        exceptions=self._all('exception')
        result=[]
        for row in self._all('event'):
            if row['id']==exclude:continue
            item=Event(**row['data'])
            semester=None
            if item.course_id:
                course=self.read('course',item.course_id)
                if course['cancelled']:continue
                if course['data']['semester_id'] is not None:
                    parent=self.read('semester',course['data']['semester_id'])
                    if parent['cancelled']:continue
                    semester=Semester(**parent['data'])
            changes=[e['data'] for e in exceptions if e['data']['event_id']==row['id'] and e['id']!=exclude]
            result.extend(expand(row['id'],item,first,last,semester=semester,exceptions=changes,revision=row['revision']))
            if len(result)>2000:
                raise Failure('planner_capacity','Too many events for this view; choose a smaller range.')
        return sorted(result,key=lambda item:(instant(item['starts_at']),item['id']))

    def preview(self,kind,value,*,exclude=None):
        if kind=='exception':
            if value['cancelled']:return []
            events=self.calendar(value['starts_at'],value['ends_at'])
            # The occurrence being replaced cannot conflict with its replacement.
            events=[r for r in events if not (r['event_id']==value['event_id'] and r['occurrence_at']==value['occurrence_at'])]
            return [dict(event_id=r['event_id'],title=r['title'],starts_at=r['starts_at'],ends_at=r['ends_at'])
                    for r in conflicts(value,events)]
        item=Event(**value)
        start=instant(item.starts_at)
        if item.kind=='deadline':return []
        last=(datetime.combine(day(item.until)+timedelta(days=2),datetime.min.time(),timezone.utc)
              if item.until else instant(item.ends_at))
        result=[]
        seen=set()
        semester=None
        if item.course_id:semester=self._semester(item.course_id)
        while start<last:
            end=min(start+timedelta(days=90),last)
            existing=self.calendar(utc(start),utc(end),exclude=exclude)
            changes=[row['data'] for row in self._all('exception') if row['data']['event_id']==exclude] if exclude else []
            proposed=expand(exclude or '0'*32,item,utc(start),utc(end),semester=semester,exceptions=changes)
            for candidate in proposed:
                # Exceptions can move an occurrence next to another occurrence
                # of this same series. Validate that overlap as well as others.
                for row in conflicts(candidate,[*existing,*proposed]):
                    signature=(candidate['occurrence_at'],row['id'])
                    if signature not in seen:
                        result.append(dict(event_id=row['event_id'],title=row['title'],starts_at=row['starts_at'],ends_at=row['ends_at'],proposed_at=candidate['occurrence_at']))
                        seen.add(signature)
                        if len(result)>100:
                            raise Failure('planner_capacity','More than 100 conflicts; shorten the proposed series.')
            start=end
        return result
