"""Cooperative, in-session research suspension; never replay a tool action."""
import asyncio

from ..core.records import Failure


class ResearchPause:
    def __init__(self, clock, waiting):
        self.clock, self.waiting = clock, waiting
        self.requested = self.suspended = False
        self.reached = asyncio.Event()
        self.released = asyncio.Event()
        self.provider = None

    async def request(self, task):
        self.requested = True
        self.released.clear()
        # Only a model request is safe to abandon. An executing tool must settle
        # and retain its exact receipt before foreground work is admitted.
        if self.provider is not None:
            self.provider.cancel()
        arrived = asyncio.create_task(self.reached.wait())
        try:
            await asyncio.wait((task, arrived), return_when=asyncio.FIRST_COMPLETED)
        finally:
            arrived.cancel()
            await asyncio.gather(arrived, return_exceptions=True)

    def resume(self):
        self.requested = False
        self.released.set()

    async def checkpoint(self, ledger, timeout):
        if not self.requested:
            return
        started = self.clock()
        self.waiting(True)
        timeout.reschedule(None)
        self.suspended = True
        self.reached.set()
        try:
            while self.requested:
                await self.released.wait()
        finally:
            ledger.exclude_research_pause(max(0, self.clock() - started))
            timeout.reschedule(ledger.deadline)
            self.suspended = False
            self.reached.clear()
        self.waiting(False)

    async def complete(self, model, request, ledger):
        started = self.clock()
        self.provider = asyncio.create_task(model.complete(request))
        try:
            return await self.provider
        except asyncio.CancelledError:
            # Owner close, explicit cancellation and permission loss still stop
            # the whole procedure. Only our joined provider cancellation yields.
            if self.requested and not asyncio.current_task().cancelling():
                ledger.yield_research_request(max(0, self.clock() - started))
                raise Failure('research_paused', 'Research yielded to foreground work.') from None
            raise
        finally:
            self.provider = None
