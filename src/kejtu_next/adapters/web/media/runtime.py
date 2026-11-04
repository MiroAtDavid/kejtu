"""Owned media processes and optional child runtimes; wired only at bootstrap."""
import asyncio
import json
import os
from pathlib import Path
import shlex
import sys

from ....core.records import Failure
from ....core.workspace import encoded
from ..worker import run
from .errors import WorkspaceError,Cancelled
from .radio import RadioController,radio_arguments,catalogue as radio_catalogue
from .video import VideoController,arguments as video_arguments,catalogue as video_catalogue
from .video_sites import SITES,compact_entry


class MediaRuntime:
    sites=SITES
    compact_entry=staticmethod(compact_entry)

    def __init__(self,store,*,runtime=None,radio=None,video=None):
        self.store,self.runtime=store,runtime
        self.namespace='kejtu-next-'+store.state_id[:12]
        self._radio,self._video=radio,video
        for player,controller in (('radio',radio),('video',video)):
            if controller is not None:controller.unit=self.namespace+'-'+player+'.service'

    def available(self,player):
        if getattr(self,'_'+player) is None and sys.platform!='linux':
            raise Failure('media_unavailable','Verified '+player+' playback is currently supported on Linux desktop sessions only; no player command ran.')

    def _controller(self,player):
        self.available(player)
        controller=getattr(self,'_'+player)
        if controller is None:
            # Resolve Linux paths only on an actual supported player request.
            directory=Path(self.runtime or os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}')
            if player=='radio':controller=RadioController(directory/(self.namespace+'-radio'))
            else:
                private=self.store.path.parent.parent/'video-tools/bin/python'
                controller=VideoController(directory/(self.namespace+'-video'),interpreter=private if private.is_file() else None)
            controller.unit=self.namespace+'-'+player+'.service'
            setattr(self,'_'+player,controller)
        return controller

    @property
    def radio(self):return self._controller('radio')

    @property
    def video(self):return self._controller('video')

    def parse(self,command):
        try:
            parts=shlex.split(command)
            if not parts or parts[0] not in ('radio','video'):raise ValueError('Use radio or video help for commands.')
            args=(radio_arguments if parts[0]=='radio' else video_arguments)(command)
            return parts[0],args
        except (ValueError,WorkspaceError) as error:raise Failure('media_arguments',str(error)) from None

    async def invoke(self,player,args):
        data=(radio_catalogue if player=='radio' else video_catalogue)(args)
        if data is not None:return data
        controller=self.radio if player=='radio' else self.video
        try:
            receipt=await run(lambda seconds,cancelled:controller.run(args,timeout=seconds,cancelled=cancelled),
                              deadline=asyncio.get_running_loop().time()+25)
            return json.loads(receipt['output'])
        except (WorkspaceError,OSError,ValueError,Cancelled) as error:
            raise Failure('media_observation',str(error)[:800] or 'Player outcome is unconfirmed; observe status before another effect.') from None

    async def calendar(self,payload):
        process=None
        try:
            process=await asyncio.create_subprocess_exec('/usr/bin/python3','-m','kejtu_next.adapters.web.media.calendars',
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
                env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[4])))
            async with asyncio.timeout(20):
                process.stdin.write(encoded(payload).encode());await process.stdin.drain();process.stdin.close()
                chunks=[];size=0
                while chunk:=await process.stdout.read(65536):
                    size+=len(chunk)
                    if size>2_000_000:raise Failure('calendar_capacity','Calendar results exceed the bounded page.')
                    chunks.append(chunk)
                await process.wait()
            data=json.loads(b''.join(chunks))
            if process.returncode or data.get('error'):raise Failure('calendar_unavailable',data.get('error','Calendar bridge is unavailable.'))
            return data
        except (OSError,ValueError,TimeoutError):raise Failure('calendar_unavailable','The read-only desktop calendar service is unavailable or timed out.') from None
        finally:
            if process is not None and process.returncode is None:
                process.kill();await process.wait()
