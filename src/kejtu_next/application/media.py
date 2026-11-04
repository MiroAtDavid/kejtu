"""Explicit private media effects and silent local observation, without a shell."""
import asyncio
from datetime import datetime,timedelta,timezone
import hashlib
import json
import shlex
import time
from zoneinfo import ZoneInfo

from ..core.workspace import encoded
from ..core.records import Failure

READS={'help','-h','--help','sites','stations','status','now','history','search'}


class MediaControls:
    def __init__(self,app,runtime):
        self.app,self.store=app,app.store
        self.runtime=runtime
        self.lock=asyncio.Lock()
        self.poller=None
        self.calendar_cache=None
        self.warning=None

    @property
    def radio(self):return self.runtime.radio

    @property
    def video(self):return self.runtime.video

    def parse(self,command):
        if not isinstance(command,str) or len(command.encode())>4096 or '\x00' in command:
            raise Failure('media_arguments','Use one bounded standalone radio or video command.')
        return self.runtime.parse(command)

    def preflight(self,command):
        player,args=self.parse(command)
        settings=self.store.planner.settings()['settings']
        if args[0] not in ('help','-h','--help','sites','stations') and not settings[player+'_enabled']:
            raise Failure('media_forbidden','Enable '+player+' in local workspace settings first.')
        if args[0]=='search' and not self.store.web.settings()['enabled']:
            raise Failure('web_forbidden','Video search requires the current Web permission.')
        return args[0] not in READS

    def start(self):
        if self.poller is None:self.poller=asyncio.create_task(self._poll(),name='observed-radio-history')

    async def close(self):
        if self.poller is not None:
            self.poller.cancel();await asyncio.gather(self.poller,return_exceptions=True);self.poller=None

    async def _poll(self):
        while True:
            try:
                if self.store.planner.settings()['settings']['radio_enabled']:
                    await self.command('radio status')
            except Failure as error:self.warning=error.detail
            await asyncio.sleep(4)

    def _observe(self,data):
        if data.get('state') not in ('playing','buffering') or not data.get('audio_output_ready'):return
        metadata=data.get('metadata',{})
        title=metadata.get('icy-title') or metadata.get('title')
        if not title or not data.get('session_id') or not data.get('station'):return
        artist=metadata.get('artist')
        title=((artist+' · ') if artist else '')+str(title)
        key=(data['session_id'],data['station'],title[:400])
        previous=self.store.db.execute('SELECT session_id,station,title FROM media_tracks ORDER BY id DESC LIMIT 1').fetchone()
        if previous is not None and tuple(previous)==key:return
        with self.store.db:
            self.store.db.execute('INSERT INTO media_tracks(session_id,station,title,observed_at) VALUES(?,?,?,?)',
                                  (*key,datetime.now(timezone.utc).isoformat()))

    def history(self,limit):
        return dict(tracks=[dict(row) for row in self.store.db.execute(
            'SELECT station,title,observed_at FROM media_tracks ORDER BY id DESC LIMIT ?',(limit,))],
            notice='Observed station metadata while Kejtu was open; observation times are not exact song starts. Gaps are possible.')

    async def _invoke(self,player,args):
        result=await self.runtime.invoke(player,args)
        if not isinstance(result,dict) or len(encoded(result).encode())>16000:
            raise Failure('media_observation','The player observation was invalid or too large.')
        if player=='radio':self._observe(result)
        return dict(player=player,data=result,observed_at=datetime.now(timezone.utc).isoformat())

    async def command(self,command,*,intent_key=None):
        effect=self.preflight(command)
        player,args=self.parse(command)
        async with self.lock:
            self.preflight(command)
            if args[0]=='history':return dict(status='observed',player=player,data=self.history(int(args[2]) if len(args)==3 else 10))
            if args[0]=='search' and not self.runtime.sites[args[1]][3]:return await self.search(args)
            intent=hashlib.sha256(shlex.join([player,*args]).encode()).hexdigest()
            if effect:
                if not isinstance(intent_key,str) or not 1<=len(intent_key)<=256:
                    raise Failure('media_intent','An explicit effect identity is required; no player command ran.')
                key='media:'+intent_key
                row=self.store.db.execute('SELECT intent,receipt FROM planner_operations WHERE mutation_key=?',(key,)).fetchone()
                if row:
                    if row['intent']!=intent:raise Failure('media_conflict','This effect identity belongs to another command; no command ran.')
                    return dict(json.loads(row['receipt']),replayed=True)
                # Unsupported adapters cannot have started an effect. A retained
                # receipt above remains authoritative even after platform change.
                self.runtime.available(player)
                with self.store.db:
                    self.store.db.execute('INSERT INTO planner_operations VALUES(?,?,?)',
                        (key,intent,encoded(dict(status='unknown',player=player,reason='effect_started',
                            recovery='The command may have run. Observe status; this effect identity will never replay it.'))))
            try:
                result=dict(status='observed',**await self._invoke(player,args))
                if effect:result.update(mutation_key=key,intent_sha256=intent)
            except BaseException:
                # An attempt receipt survives uncertainty/cancellation. Neither a
                # disconnected client nor the poller can repeat this effect.
                raise
            if effect:
                with self.store.db:self.store.db.execute('UPDATE planner_operations SET receipt=? WHERE mutation_key=?',(encoded(result),key))
            return result

    async def search(self,args):
        settings=self.store.web.settings()
        if not settings['enabled'] or self.app.web_backends is None:
            raise Failure('web_forbidden','Configure and enable the existing Web search service.')
        site,query=args[1:3];limit=int(args[4]) if len(args)==5 else 5
        provider,_=self.app.web_backends.open(settings['provider'])
        arguments=dict(query=query,site=self.runtime.sites[site][2][0])
        provider.prepare(arguments)
        attempt=self.store.web.usage.begin(provider.name);started=time.monotonic()
        try:
            result=await provider.search(arguments,deadline=asyncio.get_running_loop().time()+25)
        except BaseException as error:
            self.store.web.usage.finish(attempt,'cancelled' if isinstance(error,asyncio.CancelledError) else 'failed',round((time.monotonic()-started)*1000));raise
        self.store.web.usage.finish(attempt,'success',round((time.monotonic()-started)*1000),len(result.get('results',[])))
        if self.store.web.settings()['revision']!=settings['revision']:raise Failure('web_forbidden','Search permissions changed during this request.')
        rows=[]
        for entry in result.get('results',[]):
            item=self.runtime.compact_entry(dict(entry,webpage_url=entry.get('url')),site=site)
            if item:rows.append(item)
        return dict(status='observed',player='video',data=dict(site=site,results=rows[:limit],
            provider=provider.name,notice='Search candidates; playability is checked only on opening.'))

    async def permissions_changed(self,previous,current):
        self.calendar_cache=None
        warnings=[]
        async with self.lock:
            for player in ('radio','video'):
                if previous[player+'_enabled'] and not current[player+'_enabled']:
                    try:await self._invoke(player,['stop'])
                    except Failure as error:warnings.append(error.detail)
        return warnings

    async def _calendar(self,payload):
        return await self.runtime.calendar(payload)

    async def calendars(self):
        if not self.store.planner.settings()['settings']['calendar_enabled']:
            raise Failure('calendar_forbidden','Enable external calendar reading before listing desktop calendars.')
        return await self._calendar(dict(action='calendars'))

    async def agenda(self,offset=0,refresh=False):
        settings=self.store.planner.settings();value=settings['settings']
        if not value['calendar_enabled']:return dict(status='disabled',kind='calendar',data=None)
        if type(offset) is not int or not 0<=offset<=1000:raise Failure('calendar_arguments','Use a bounded agenda offset.')
        if not value['calendars']:return dict(status='empty',kind='calendar',data=dict(events=[],notice='Select calendars in workspace settings.'))
        now=datetime.now(ZoneInfo(value['availability']['timezone']))
        first=now.replace(hour=0,minute=0,second=0,microsecond=0);last=first+timedelta(days=7)
        key=(settings['revision'],first.date().isoformat(),offset)
        if not refresh and self.calendar_cache and self.calendar_cache[0]==key and time.monotonic()-self.calendar_cache[1]<60:
            return self.calendar_cache[2]
        try:
            data=await self._calendar(dict(action='agenda',calendar_ids=value['calendars'],first=first.isoformat(),
                last=last.isoformat(),timezone=value['availability']['timezone'],offset=offset,limit=20))
            result=dict(status='partial' if data.get('incomplete') else 'ready',kind='calendar',data=data)
        except Failure as error:result=dict(status='unavailable',kind='calendar',data=None,reason=error.detail)
        if self.store.planner.settings()['revision']!=settings['revision']:
            raise Failure('calendar_forbidden','Calendar permissions changed during reading.')
        self.calendar_cache=(key,time.monotonic(),result)
        return result
