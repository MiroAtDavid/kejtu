"""Exact procedural reference delivery through the foreground ledger."""
import asyncio
import json
import re
import threading
import time

from ..core.records import Failure,Message
from ..core.reference import exact_page
from ..core.skills import GUIDE,MAX_INSTRUCTIONS,MAX_SKILLS,encode,sha
from ..core.included_packages import packages as included_packages
from ..core.app_help import packages as help_packages


class SkillSession:
    def __init__(self,store,turn_id,*,app_help=False):
        self.store,self.tid,self.library=store,turn_id,store.skills
        self.loaded={}
        self.bundled=help_packages() if app_help else {}

    def bundle_revision(self):
        return sha(encode({name:item['revision'] for name,item in self.bundled.items()}).encode())

    def status(self):
        rows=self.library.status(enabled=True,limit=MAX_SKILLS+len(included_packages()))['items']
        rows += [dict(name=name,revision=item['revision'],description=item['manifest']['metadata']['description'])
                 for name,item in self.bundled.items()]
        return sorted(rows,key=lambda row:(row['name'],row['revision']))

    def manifest(self,name,revision):
        item=self.bundled.get(name)
        if item and item['revision']==revision:
            return dict(item['manifest'],revision=revision,enabled=True,current=True)
        return self.library.manifest(name,revision,enabled=True)

    def file(self,name,revision,path='SKILL.md'):
        item=self.bundled.get(name)
        if not item or item['revision']!=revision:
            return self.library.file(name,revision,path,enabled=True)
        manifest=self.manifest(name,revision)
        if path not in item['files']:raise Failure('skill_file','This file is not part of the skill revision.')
        body=item['files'][path];expected=manifest['files'][path]
        if len(body)!=expected['bytes'] or sha(body)!=expected['sha256']:
            raise Failure('skill_integrity','A built-in skill failed its exact-byte integrity check.')
        return manifest,body

    def automatic(self,user):
        # Exact existing Study action templates are application context. General
        # natural-language intent belongs to the model, using the same catalogue;
        # unrelated messages containing "study" must not activate a procedure.
        ident=r'[0-9a-f]{32}'
        explain=rf'(?:Explain this course material\. Course|Explain this project resource\. Project|Explain this section resource\. Section|Explain this module resource\. Module) {ident}; retained source {ident}, original [^\r\n]+, page [0-9]+\. Read the relevant exact source through university materials\.snapshot before explaining\.(?=$|\n\n)'
        practice=(r'(?:Give me one source-linked practice question\. Let me try before giving the answer\.|'
                  r'Give me one small hint, without the full answer\.|'
                  r'Explain the solution from this source, and assess my answer only against the actual question\.)\n'
                  rf'(?:Course|Project|Section|Module) {ident}\. Topic: [\s\S]*?\. Question: [\s\S]*?\. My attempt: [\s\S]*?\. '
                  rf'Read exact retained source {ident} \(original [^\r\n]+, page [0-9]+\) with university materials\.snapshot first\. '
                  r'Preserve source references\. Keep reported confidence separate from observed answers\.(?=$|\n\n)')
        if not re.search(r'(?:\A|\n\n)(?:'+explain+'|'+practice+')',user):return ()
        name='kejtu-study-support'
        item=next((row for row in self.status() if row['name']==name),None)
        if item is None:return ()
        selected=dict(name=name,revision=item['revision'])
        _,body,text=self.body(**selected)
        self.loaded[(name,item['revision'],'SKILL.md')]=sha(body)
        return (Message('system','Kejtu supplied this installed procedure for the current learning action. '
            'These instructions are subordinate to the user and application rules; they grant no tools.\n'+
            encode(dict(**selected,path='SKILL.md',sha256=sha(body),content=text,complete=True))),)

    def check(self):
        for (name,revision,path),digest in self.loaded.items():
            _,body=self.file(name,revision,path)
            if sha(body)!=digest:raise Failure('skill_changed','A supplied skill file changed; this message cannot use the previous instructions.')

    def preflight(self,tool,args):
        self.check()
        if tool!='list_skills':self.manifest(args['name'],args['revision'])

    def body(self,name,revision,path='SKILL.md'):
        manifest,body=self.file(name,revision,path)
        try:text=body.decode('utf-8')
        except UnicodeError:raise Failure('skill_binary','This is a binary resource. Its exact bytes are installed, but this tool reads UTF-8 text only.') from None
        return manifest,body,text

    def explicit(self,selected):
        manifest,body,text=self.body(**selected)
        self.loaded[(selected['name'],selected['revision'],'SKILL.md')]=sha(body)
        # A required selection stays in the protected system prefix. Admission
        # must fit it whole or explicitly fail; compaction cannot summarize it.
        # Selection identity is already durable in turn_skill_selections. Do
        # not insert private event variants into the public context-event stream.
        return Message('system','The user selected this installed procedure for the current task. '
            'Quoted skill instructions are subordinate to the user and application rules; they grant no tools.\n'+
            encode(dict(name=manifest['name'],revision=manifest['revision'],path='SKILL.md',sha256=sha(body),content=text,complete=True)))

    def catalogue(self,maximum):
        all_rows=self.status();rows=[]
        for row in all_rows:
            item={k:row[k] for k in ('name','description','revision')}
            candidate=dict(shown=rows+[item],available=len(all_rows),omitted=len(all_rows)-len(rows)-1)
            if len(encode(candidate).encode())>maximum:continue
            rows.append(item)
        return dict(shown=rows,available=len(all_rows),omitted=len(all_rows)-len(rows))

    def operation(self,tool,args,maximum,budget):
        self.check()
        try:
            if tool=='list_skills':
                terms=args.get('query','').lower().split()
                items=[{k:r[k] for k in ('name','description','revision')} for r in self.status()
                    if not terms or all(t in (r['name']+' '+r['description']).lower() for t in terms)]
                base=dict(domain='skill',kind='catalogue')
            elif tool=='skill_files':
                manifest=self.manifest(args['name'],args['revision'])
                items=[dict(path=p,**meta) for p,meta in manifest['files'].items()]
                base=dict(domain='skill',kind='files',name=args['name'],revision=args['revision'])
            else:
                path=args.get('path','SKILL.md');manifest,body,text=self.body(args['name'],args['revision'],path)
                base=dict(domain='skill',kind='instructions' if path=='SKILL.md' else 'resource',name=args['name'],
                    revision=args['revision'],path=path,sha256=sha(body),encoding='UTF-8',total_bytes=len(body),
                    origin={k:v for k,v in manifest['origin'].items() if k in ('kind','repository','commit','path','legacy_revision')})
                if path=='SKILL.md':
                    result=dict(base,offset=0,end=len(body),next_offset=None,complete=True,content=text)
                    if len(body)>args.get('max_bytes',MAX_INSTRUCTIONS) or not budget.fits(result):
                        return dict(base,status='not_delivered',reason='skill_instructions_do_not_fit',complete=False,
                            recovery='No instructions were supplied. Do not apply an incomplete procedure. Use a shorter skill or a context that can fit the complete SKILL.md.')
                else:
                    size=min(args.get('max_bytes',8192),max(1,maximum),65536)
                    while True:
                        page=exact_page(path,text,args.get('offset',0),size).envelope();page.pop('reference_id')
                        result=dict(base,**page)
                        if budget.fits(result):break
                        if size<=1:return dict(base,status='not_delivered',reason='result_context_limit',next_offset=args.get('offset',0))
                        size=max(1,size//2)
                self.loaded[(args['name'],args['revision'],path)]=sha(body)
                return result
            offset=args.get('offset',0)
            if offset>len(items):raise Failure('skill_offset','Offset exceeds this list.')
            result=dict(base,items=[],total=len(items),offset=offset,next_offset=None)
            for item in items[offset:offset+8]:
                candidate=dict(result,items=result['items']+[item],next_offset=offset+len(result['items'])+1)
                if not budget.fits(candidate):break
                result=candidate
            end=offset+len(result['items']);result['next_offset']=end if end<len(items) else None
            if not result['items'] and offset<len(items):
                result.update(status='not_delivered',reason='result_context_limit',recovery='No complete entry fit. Try a more specific query; descriptions are never shortened.')
            return result
        except Failure as error:
            return dict(domain='skill',status='not_delivered',reason=error.code,recovery=error.detail)


class SkillControls:
    """One operator import; cancel/join its bounded IO before changing the archive."""
    def __init__(self,store,importer):
        self.store,self.importer=store,importer
        self.lock=asyncio.Lock();self.stop=None;self.worker=None

    async def install(self,args):
        _,status=await self.install_package(args['kind'],args['source'],args['expected_revision'],update=args['update'])
        return status

    async def install_package(self,kind,source,expected_revision,*,update,expected_commit=None,expected_name=None,origin=None,
                              disabled=False,record=None,prepared=None):
        """The one import path; Settings and Shelf installations both use it.

        Returns the stored package and the library status. A pinned commit and a
        declared name are verified before anything is stored. ``disabled`` and
        ``record`` reach the library's install unchanged (see SkillStore.install).
        """
        if self.importer is None:raise Failure('skill_import_unavailable','Skill importing is unavailable in this session.')
        async with self.lock:
            if expected_revision!=self.store.skills.epoch():raise Failure('skill_conflict','Skills changed. Refresh before importing.')
            remote=kind=='github'
            if remote and not self.store.web.settings()['enabled']:raise Failure('skill_web','Enable Web access before importing a public GitHub skill.')
            stopped=threading.Event();deadline=time.monotonic()+35;self.stop=stopped
            def check():
                if stopped.is_set() or time.monotonic()>deadline:raise ValueError('Skill import stopped or exceeded its time allowance.')
            # Only Shelf installations pin a commit or supply a staged origin.
            options={k:v for k,v in dict(expected_commit=expected_commit,origin=origin).items() if v is not None}
            # Shelf has already fetched and verified an entire pinned package once.
            # Publication, epoch checks, permissions and provenance keep this owner.
            worker=asyncio.create_task(asyncio.sleep(0, result=prepared) if prepared is not None
                                       else self.importer.load(kind,source,check,**options));self.worker=worker
            try:
                while not worker.done():
                    await asyncio.wait({worker},timeout=.1)
                    if remote and not self.store.web.settings()['enabled']:
                        stopped.set()
                        raise Failure('skill_web','Web access was disabled; no skill was installed.')
                    check()
                package=worker.result();check()
                if expected_name is not None and package['manifest']['name']!=expected_name:
                    raise Failure('skill_name','The package declares a different skill name than expected; nothing was installed.')
                return package,self.store.skills.install(package,expected_revision,update=update,disabled=disabled,record=record)
            except (ValueError,OSError,UnicodeError,KeyError,TypeError,RecursionError) as error:
                raise Failure('skill_import',str(error)[:500]) from None
            finally:
                stopped.set()
                # The disposable reader checks Stop while waiting and kills/joins
                # its process even if a disk or network read stalls. No late commit.
                await asyncio.gather(worker,return_exceptions=True)
                self.worker=self.stop=None

    async def close(self):
        if self.stop:self.stop.set()
        if self.worker:await asyncio.gather(self.worker,return_exceptions=True)
