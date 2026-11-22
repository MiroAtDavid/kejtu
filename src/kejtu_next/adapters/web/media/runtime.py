"""Optional read-only desktop calendar child runtime, wired at bootstrap."""
import asyncio
import json
import os
from pathlib import Path

from ....core.records import Failure
from ....core.workspace import encoded


class CalendarRuntime:
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
