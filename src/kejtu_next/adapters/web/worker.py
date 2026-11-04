"""Join cancellable blocking web IO before releasing the foreground owner."""
import asyncio
import threading
from ...core.records import Failure


class Cancelled(Exception):
    pass


async def run(function, *, deadline):
    loop = asyncio.get_running_loop()
    remaining = min(25, deadline-loop.time())
    if remaining <= 0: raise Failure('timeout','The web request deadline expired.')
    cancelled = threading.Event()
    def invoke():
        # Return errors as data until the awaiting task joins. Python 3.14 reports an
        # exception on an abandoned shield even if another await consumes it.
        try: return True, function(remaining, cancelled)
        except Exception as error: return False, error
    future = loop.run_in_executor(None, invoke)
    try:
        async with asyncio.timeout(remaining):
            ok, value = await asyncio.shield(future)
            if not ok: raise value
            return value
    except (asyncio.CancelledError, TimeoutError) as error:
        cancelled.set()
        # A cancelled await must not leave its browser/connection running after
        # the user sees Stop complete. Keep the original outcome while joining.
        while not future.done():
            try: await asyncio.shield(future)
            except asyncio.CancelledError: cancelled.set()
            except Exception: break
        if future.done() and not future.cancelled():
            try: future.result()
            except Exception: pass
        if isinstance(error, asyncio.CancelledError): raise
        raise Failure('web_timeout','The web request timed out; content is unknown.') from None
    except Cancelled:
        raise Failure('web_cancelled','The web request stopped without a confirmed result.') from None
