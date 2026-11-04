"""Foreground web IO and exact source projection under the shared turn ledger."""
import json
import asyncio
import time
from ..core.records import Failure
from ..core.reference import exact_page


class WebSession:
    def __init__(self, sources, backends, conversation_id, turn_id, ledger):
        self.sources,self.cid,self.tid,self.ledger=sources,conversation_id,turn_id,ledger
        self.settings=sources.settings(); self.revision=self.settings['revision']
        if backends is None: raise Failure('web_provider_unavailable','Web adapters are not available in this session.')
        self.provider,self.reader=backends.open(self.settings['provider'])
        self.cache={}
        self.blocked=set()
        self.check()

    def check(self): self.sources.guard(self.cid,self.revision)
    def history_actions(self,turn_id):
        self.check()
        return self.sources.history_actions(turn_id)

    async def operation(self,name,args,maximum,budget):
        self.check()
        kind='search' if name=='web_search' else 'page'
        key=json.dumps([kind,{k:v for k,v in args.items() if k not in ('offset','limit','max_bytes','refresh')}],sort_keys=True)
        source_id=args.get('source_id')
        if not source_id:
            source_id=None if args.get('refresh') else self.cache.get(key)
        try:
            if not source_id:
                if key in self.blocked:
                    return dict(domain='web',status='not_delivered',reason='web_blocked',recovery='That request already failed in this turn; use another source without retrying access checks.')
                try:
                    if kind=='search':
                        if args.get('category','general') not in self.provider.categories:
                            return dict(domain='web',status='not_delivered',reason='unsupported_category',categories=list(self.provider.categories))
                        # Validation/key availability do not count as outbound attempts.
                        if hasattr(self.provider, 'prepare'): self.provider.prepare(args)
                        attempt = self.sources.usage.begin(self.provider.name)
                        started = time.monotonic()
                        try:
                            data=await self.provider.search(args,deadline=self.ledger.deadline)
                        except BaseException as error:
                            outcome = 'cancelled' if isinstance(error, asyncio.CancelledError) else 'failed'
                            self.sources.usage.finish(attempt, outcome, round((time.monotonic()-started)*1000))
                            raise
                        else:
                            ok = data.get('status') in ('ok','partial','empty')
                            self.sources.usage.finish(attempt, 'success' if ok else 'failed',
                                round((time.monotonic()-started)*1000), len(data.get('results',[])) if ok else 0)
                        data=dict(data);results=data.pop('results',[])
                        if not isinstance(results,list) or len(results)>500: raise ValueError('Invalid search results')
                        content=json.dumps(results,ensure_ascii=False,separators=(',',':'))
                        provider=self.provider.name
                        data['title']='Search: '+args.get('query', args.get('location', 'Places around supplied coordinates'))[:250]
                    else:
                        data=await self.reader.read(args['url'],deadline=self.ledger.deadline)
                        data=dict(data);content=data.pop('text','');provider='public_page'
                    self.check()
                    source_id=self.sources.save(self.tid,provider,kind,args,data,content)
                    self.cache[key]=source_id
                    if data.get('status') not in ('ok','partial','empty'): self.blocked.add(key)
                except Failure: raise
                except (ValueError,OSError,KeyError,TypeError) as error:
                    self.blocked.add(key)
                    # No raw exception/environment text is copied to model/audit.
                    return dict(domain='web',status='not_delivered',reason='web_unavailable',recovery='The web request failed; no page content was confirmed.')
            source=self.sources.load(source_id,self.cid)
            if source['kind']!=kind: raise Failure('web_source_kind','Use web_search for search snapshots and web_read for page snapshots.')
            self.check()
            base=dict(domain='web',source_id=source_id,**({'source_run_id':source['run_id']} if 'run_id' in source else {'source_turn_id':source['turn_id']}),provider=source['provider'],
                      kind=kind,sha256=source['sha256'],fetched_at=source['metadata'].get('fetched_at',source['created_at']),
                      source_status=source['metadata'].get('status','unknown'))
            meta=source['metadata']
            for field in ('url','requested_url','title','page_dates','warnings','engine_errors','notice','error','message','code','engines_requested','links','snapshot_truncated','extraction','http_status','structured_search','search_kind','rate_limits','query_info','applied_options'):
                if field in meta: base[field]=meta[field]
            if kind=='search':
                rows=json.loads(source['content']); offset=args.get('offset',0);limit=args.get('limit',8)
                if offset>len(rows): raise Failure('web_offset','Result offset is outside this saved search page.')
                result=dict(base,status='complete',offset=offset,total_results=len(rows),results=[],next_offset=None,
                            next_page_candidate=meta.get('next_page_candidate'),requested_limit=limit,delivered_count=0,
                            delivery_limited=None)
                wanted=min(limit,len(rows)-offset)
                for row in rows[offset:offset+limit]:
                    candidate=dict(result,results=result['results']+[row])
                    end=offset+len(candidate['results']); candidate['next_offset']=end if end<len(rows) else None
                    candidate['status']='page' if offset or candidate['next_offset'] is not None else 'complete'
                    candidate['delivered_count']=len(candidate['results'])
                    candidate['delivery_limited']='context' if len(candidate['results'])<wanted else None
                    if not budget.fits(candidate): break
                    result=candidate
                if offset<len(rows) and not result['results']:
                    return dict(base,status='not_delivered',reason='result_context_limit',offset=offset,next_offset=offset,
                                recovery='Search saved; no hit fits this delivery. Continue this source_id/offset with a smaller limit; do not repeat the search.')
                return result
            page=exact_page(source_id,source['content'],args.get('offset',0),min(args.get('max_bytes',8192),maximum)).envelope()
            page.pop('reference_id')
            return dict(base,**page)
        except Failure as error:
            if error.code in ('timeout','history_work_limit','web_forbidden'): raise
            return dict(domain='web',status='not_delivered',reason=error.code,recovery=error.detail)
