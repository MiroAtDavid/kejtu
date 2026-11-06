"""Async owner of one disposable SDK process and its sandboxed server tree."""
import asyncio
import os
from pathlib import Path
import signal
import shutil
import tempfile
import sys
import threading

from ...core.mcp import MAX_RESULT, encode, parse
from ...core.records import Failure


class Connection:
    def __init__(self, row, *, runtime=None):
        self.row = row
        self.root = Path(__file__).resolve().parents[4]
        self.runtime = Path(runtime) if runtime else self.root / '.state/mcp-runtime/python'
        if sys.platform == 'win32' and runtime is None:
            self.runtime = self.root / 'package-runtime/mcp/python'
        self.process = None
        self.scratch = None

    async def request(self, payload, *, deadline, check):
        from .windows_runtime import is_builtin
        if sys.platform == 'win32' and not is_builtin(self.row):
            raise Failure('mcp_not_executed', 'Local connected tools are not available in this Windows test build yet. No tool was started.')
        check()
        try:
            async with asyncio.timeout_at(deadline):
                if self.process is None:
                    if sys.platform == 'linux':
                        from .runtime_protection import validate_runtime_writes
                        # No worker imports until preflight completes. Filesystem
                        # calls run off the event loop so cancellation/revocation
                        # remain responsive even when an individual call stalls.
                        cancelled = threading.Event()
                        try:
                            await asyncio.to_thread(validate_runtime_writes, self.row,
                                self.root, runtime=self.runtime, _cancelled=cancelled.is_set)
                        finally:
                            cancelled.set()
                        check()
                        if asyncio.get_running_loop().time() >= deadline:
                            raise TimeoutError()
                    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': '/tmp',
                        'PYTHONPATH': os.pathsep.join([str(self.runtime), str(self.root/'src')]),
                        'PYTHONDONTWRITEBYTECODE': '1'}
                    options = {'start_new_session': True}
                    if sys.platform == 'win32':
                        from .windows_runtime import environment
                        cache = self.row['cache']
                        if self.row.get('command') == '@kejtu/browser':
                            self.scratch = Path(tempfile.mkdtemp(prefix='browser-', dir=cache))
                            cache = str(self.scratch)
                        env = dict(environment(cache),
                            PYTHONPATH=os.pathsep.join([str(self.runtime), str(self.root/'src')]),
                            KEJTU_MCP_RUNTIME=str(self.runtime))
                        options = {'creationflags': getattr(__import__('subprocess'), 'CREATE_NO_WINDOW', 0)}
                    flags = ['-B']
                    if sys.platform == 'linux':
                        # Only the explicit SDK/source paths may supply imports;
                        # writable CWD, sitecustomize and .pth files are not code.
                        flags += ['-S', '-P']
                        options['cwd'] = '/'
                    self.process = await asyncio.create_subprocess_exec(sys.executable, *flags, '-m',
                        'kejtu_next.adapters.mcp.worker', stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                        **options, env=env, limit=MAX_RESULT+2)
                    row = dict(self.row, cache=str(self.scratch)) if self.scratch else self.row
                    self.process.stdin.write((encode({'server': row})+'\n').encode())
                self.process.stdin.write((encode(payload)+'\n').encode())
                await self.process.stdin.drain()
                read = asyncio.create_task(self.process.stdout.readline())
                try:
                    while not read.done():
                        await asyncio.wait({read}, timeout=.1)
                        check()
                    raw = read.result()
                finally:
                    if not read.done():
                        read.cancel()
                    await asyncio.gather(read, return_exceptions=True)
                check()
                if not raw or len(raw) > MAX_RESULT+1:
                    raise Failure('mcp_disconnected', 'The MCP connection ended or exceeded its result limit. Effects may have occurred; check before repeating an action.')
                value = parse(raw)
                if not isinstance(value, dict) or type(value.get('ok')) is not bool:
                    raise ValueError()
                if not value['ok']:
                    if value.get('fatal'):
                        await self.close()
                    if value.get('not_executed'):
                        raise Failure('mcp_not_executed', 'No tool action ran. Discover its current schema with mcp_tools and correct the arguments before trying again.')
                    # Server exceptions can echo arbitrary private input. Keep
                    # worker errors local; original CallToolResult errors remain
                    # source data and are archived as such by the caller.
                    raise Failure('mcp_request_failed', 'The connection or tool request failed. Check the server, input schema and credentials. No automatic retry was sent; effects may have occurred.')
                return value
        except (asyncio.CancelledError, TimeoutError):
            await self.close()
            raise
        except Failure:
            # Validation errors answered by a live worker need not destroy page
            # state, but authority changes always do.
            try: check()
            except BaseException:
                await self.close()
                raise
            raise
        except (OSError, ValueError, TypeError, UnicodeError):
            await self.close()
            raise Failure('mcp_unavailable', 'MCP could not complete this request. No automatic retry was sent; external completion is unknown.') from None

    async def close(self):
        process = self.process
        if process is None:
            await self._clean_scratch()
            return
        if sys.platform != 'win32':
            pid = process.pid
            # Never coerce a PID-like object: a mock's __index__ can return 1,
            # and libc killpg(1, ...) becomes the broadcast kill(-1, ...).
            # Keep ownership on refusal so cleanup failure cannot look complete.
            if type(pid) is not int or pid <= 1 or pid == os.getpgrp():
                raise Failure('mcp_cleanup', 'The MCP worker has no safe owned process group. No signal was sent.')
            try:
                group = os.getpgid(pid)
            except ProcessLookupError:
                # The leader may exit before its descendants; its original
                # new-session process group still needs to be reaped below.
                group = pid
            if group != pid:
                raise Failure('mcp_cleanup', 'The MCP worker does not own its process group. No signal was sent.')
        self.process = None
        # Each worker owns a new process group; bwrap's PID namespace and
        # --die-with-parent also reap descendants that start separate groups.
        try:
            if sys.platform == 'win32':
                if process.returncode is None: process.kill()
            else: os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError: pass
        await process.wait()
        if process.stdin:
            process.stdin.close()

        await self._clean_scratch()

    async def _clean_scratch(self):
        scratch = self.scratch
        if scratch is None:
            return
        # The Job can release profile handles just after the worker is reaped.
        # Remove only this invocation's generated directory, never shared cache.
        # Stop and revocation can close concurrently while a retry is sleeping;
        # retain the exact old path, even if another close already cleared it.
        for attempt in range(40):
            try:
                shutil.rmtree(scratch)
                if self.scratch == scratch:
                    self.scratch = None
                return
            except FileNotFoundError:
                if self.scratch == scratch:
                    self.scratch = None
                return
            except OSError:
                if attempt == 39:
                    raise Failure('mcp_cleanup', 'The temporary browser session could not be removed.') from None
                await asyncio.sleep(.05)
