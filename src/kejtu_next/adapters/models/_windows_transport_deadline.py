"""Independent wall-clock watchdog for one Windows provider worker.

Only restricted inherited object handles cross into the watcher, never a PID,
request or credential. A separate process keeps its own GIL while exchange runs.
Win32 contracts: DuplicateHandle, CreateEventW, WaitForMultipleObjects,
SetEvent and TerminateProcess in the Microsoft Windows API documentation.
"""
import ctypes
import math
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import time

from ...core.records import Failure


TIMEOUT_EXIT = 124
WATCHDOG_FAILURE_EXIT = 125
_SYNCHRONIZE = 0x00100000
_PROCESS_TERMINATE = 0x0001
_EVENT_MODIFY_STATE = 0x0002
_WAIT_TIMEOUT = 258


def _failure():
    return Failure('provider_unavailable',
        'The local provider deadline could not be protected; no retry was sent.')


class _API:
    def __init__(self):
        dll = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x800)
        handle = ctypes.c_void_p
        dword = ctypes.c_uint32
        boolean = ctypes.c_int32
        bindings = {
            'GetCurrentProcess': ([], handle),
            'GetWindowsDirectoryW': ([ctypes.POINTER(ctypes.c_wchar), ctypes.c_uint32], ctypes.c_uint32),
            'DuplicateHandle': ([handle, handle, handle, ctypes.POINTER(handle), dword, boolean, dword], boolean),
            'CreateEventW': ([ctypes.c_void_p, boolean, boolean, ctypes.c_wchar_p], handle),
            'SetEvent': ([handle], boolean),
            'WaitForMultipleObjects': ([dword, ctypes.POINTER(handle), boolean, dword], dword),
            'TerminateProcess': ([handle, dword], boolean),
            'CloseHandle': ([handle], boolean),
        }
        for name, (arguments, result) in bindings.items():
            function = getattr(dll, name)
            function.argtypes, function.restype = arguments, result
            setattr(self, name, function)

    def duplicate(self, original, access):
        current = self.GetCurrentProcess()
        result = ctypes.c_void_p()
        if not self.DuplicateHandle(current, original, current, ctypes.byref(result), access, True, 0):
            raise _failure()
        return result.value

    def wait(self, handles, milliseconds):
        values = (ctypes.c_void_p * len(handles))(*handles)
        return self.WaitForMultipleObjects(len(values), values, False, milliseconds)


def system_environment():
    # Winsock's provider paths depend on SystemRoot even though credentials and
    # ambient user configuration must never be inherited into provider children.
    # Resolve the Windows directory from the OS, never from caller environment.
    try:
        api = _API()
        buffer = ctypes.create_unicode_buffer(32768)
        size = api.GetWindowsDirectoryW(buffer, len(buffer))
        if not 0 < size < len(buffer):
            raise _failure()
        value = buffer.value
        path = PureWindowsPath(value)
        if not path.is_absolute() or len(path.drive) != 2 or path.drive[1] != ':':
            raise _failure()
        return {'SystemRoot': value}
    except (OSError, AttributeError, ValueError, ctypes.ArgumentError):
        raise _failure() from None


def _milliseconds(deadline, *, maximum=250):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return 0
    return maximum if remaining >= maximum / 1000 else max(1, math.ceil(remaining * 1000))


class Deadline:
    def __init__(self, deadline):
        self.api = None
        self.process = None
        self.handles = []
        self.cancel = None
        if type(deadline) not in (float, int) or not math.isfinite(deadline):
            raise _failure()
        try:
            if deadline <= time.monotonic():
                raise TimeoutError
            self.api = _API()
            worker = self.api.duplicate(self.api.GetCurrentProcess(), _SYNCHRONIZE | _PROCESS_TERMINATE)
            self.handles.append(worker)
            for _ in range(2):
                event = self.api.CreateEventW(None, True, False, None)
                if not event:
                    raise _failure()
                self.handles.append(event)
            self.cancel, ready = self.handles[1:]
            inherited = []
            try:
                # Retain noninheritable original events in the worker; the watcher
                # receives only wait permission for cancel and set permission for ready.
                inherited.append(self.api.duplicate(self.cancel, _SYNCHRONIZE))
                inherited.append(self.api.duplicate(ready, _EVENT_MODIFY_STATE))
                startup = subprocess.STARTUPINFO()
                startup.lpAttributeList = {'handle_list': [worker, *inherited]}
                self.process = subprocess.Popen(
                    [sys.executable, '-B', '-S', '-P', '-m', __name__, '--watch',
                     str(worker), str(inherited[0]), str(inherited[1]), repr(deadline)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    close_fds=True, startupinfo=startup,
                    env={'PYTHONPATH': str(Path(__file__).resolve().parents[3]),
                         'PYTHONDONTWRITEBYTECODE': '1', **system_environment()})
            finally:
                for handle in inherited:
                    self.api.CloseHandle(handle)
            # Popen owns this exact process HANDLE; no OpenProcess/PID lookup.
            # Check process exit first if ready and exit become signalled together.
            status = self.api.wait([int(self.process._handle), ready], _milliseconds(deadline, maximum=5000))
            if status != 1:
                if deadline <= time.monotonic():
                    raise TimeoutError
                raise _failure()
        except BaseException as error:
            try:
                self.close()
            except Failure:
                # No exchange has begun: cleanup failure cannot turn an expired
                # absolute readiness deadline into a different reported outcome.
                if not isinstance(error, TimeoutError):
                    raise
            if isinstance(error, (Failure, TimeoutError, KeyboardInterrupt, SystemExit)):
                raise
            raise _failure() from None

    def close(self):
        failed = False
        try:
            if self.process is not None:
                if self.process.poll() is None:
                    signalled = self.api.SetEvent(self.cancel)
                    if not signalled:
                        failed = True
                        self.process.kill()
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        failed = True
                        self.process.kill()
                        self.process.wait(timeout=2)
                if self.process.returncode != 0:
                    failed = True
        except (OSError, subprocess.SubprocessError):
            failed = True
        finally:
            if self.api is not None:
                for handle in self.handles:
                    if not self.api.CloseHandle(handle):
                        failed = True
                self.handles.clear()
        if failed:
            raise _failure() from None


def watch(worker, cancel, ready, deadline, *, _api=None):
    """Watch only already inherited handles. Unexpected wait errors fail closed."""
    api = _api or _API()
    handles = (worker, cancel, ready)
    try:
        # Validate wait handles before acknowledging protection. With zero wait,
        # a live worker and unset cancel event must return WAIT_TIMEOUT.
        if api.wait([worker, cancel], 0) != _WAIT_TIMEOUT:
            return 0 if api.wait([worker], 0) == 0 else WATCHDOG_FAILURE_EXIT
        if not api.SetEvent(ready):
            if api.TerminateProcess(worker, WATCHDOG_FAILURE_EXIT):
                api.wait([worker], 2000)
            return WATCHDOG_FAILURE_EXIT
        while True:
            milliseconds = _milliseconds(deadline)
            status = api.wait([worker, cancel], milliseconds)
            if status in (0, 1):
                return 0
            if status == _WAIT_TIMEOUT and deadline > time.monotonic():
                # Windows8+ relative waits exclude suspend time. Short slices
                # recheck the original clock after resume instead of resetting it.
                continue
            code = TIMEOUT_EXIT if status == _WAIT_TIMEOUT else WATCHDOG_FAILURE_EXIT
            if api.TerminateProcess(worker, code):
                # TerminateProcess is asynchronous: join this exact object.
                return 0 if api.wait([worker], 2000) == 0 else WATCHDOG_FAILURE_EXIT
            # The worker may have exited between timeout and termination.
            return 0 if api.wait([worker], 0) == 0 else WATCHDOG_FAILURE_EXIT
    except BaseException:
        if api.TerminateProcess(worker, WATCHDOG_FAILURE_EXIT):
            api.wait([worker], 2000)
        return WATCHDOG_FAILURE_EXIT
    finally:
        for handle in handles:
            api.CloseHandle(handle)


def main():
    if len(sys.argv) != 6 or sys.argv[1] != '--watch':
        return WATCHDOG_FAILURE_EXIT
    try:
        worker, cancel, ready = (int(value) for value in sys.argv[2:5])
        deadline = float(sys.argv[5])
        if (not math.isfinite(deadline) or any(not 0 < h < (1 << (ctypes.sizeof(ctypes.c_void_p)*8))
                                             for h in (worker, cancel, ready))
                or len({worker, cancel, ready}) != 3):
            return WATCHDOG_FAILURE_EXIT
        return watch(worker, cancel, ready, deadline)
    except Exception:
        # No raw OS text, request body or credentials are written by this process.
        return WATCHDOG_FAILURE_EXIT


if __name__ == '__main__':
    raise SystemExit(main())
