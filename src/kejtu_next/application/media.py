"""Read-only external calendar controls (the historical module path is retained)."""
from datetime import datetime,timedelta
import time
from zoneinfo import ZoneInfo

from ..core.records import Failure


class CalendarControls:
    def __init__(self,app,runtime):
        self.app,self.store=app,app.store
        self.runtime=runtime
        self.calendar_cache=None

    # Compatibility for native/update lifecycle callers outside this assignment.
    # No polling, player discovery, process adoption or playback remains.
    def start(self):pass
    async def close(self):pass
    def hold_observation(self):pass
    def release_observation(self):pass
    @property
    def settled(self):return True

    async def permissions_changed(self,previous,current):
        self.calendar_cache=None
        return []

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
