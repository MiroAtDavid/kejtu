from typing import Callable, Protocol
from ..core.records import ModelProgress, Profile, Reply, Request


class Model(Protocol):
    async def complete(self, request: Request) -> Reply:
        """One attempt. Cancellation must release owned transport before returning."""
        ...


class StreamingModel(Protocol):
    async def stream(self, request: Request, progress: Callable[[ModelProgress], None]) -> Reply:
        """One attempt; progress stays provisional until the returned Reply validates.

        Text is appended, reset retracts a provisional tool preamble, and phase
        contains an application-defined label. Tool arguments/reasoning are never
        display events. Cancellation joins transport before returning.
        """
        ...


class RuntimeContextCapability(Protocol):
    async def context_capability(self, profile: Profile, deadline: float) -> bool:
        """Optional metadata-only verification; strict True permits bounded probing.

        Unknown runtime/profile combinations return False, not a token estimate.
        Use the inherited deadline and release owned IO on cancellation. The
        adapter must reverify requests marked require_context_rejection before
        generation; this observation cannot become durable configuration.
        """
        ...
