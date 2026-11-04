"""Private Windows desktop stop capability; no wire operation or signal broadcast."""
from __future__ import annotations

import os


class LifecycleEvent:
    """Own only the inherited wait handle; the desktop retains modify authority.

    Handle information is not a kernel-object type check. Provenance is the
    parent's CreateEvent + explicit CreateProcess handle list. Wait failures
    refuse startup/stop honestly rather than silently running unmanaged.
    """
    def __init__(self, value, loop, stop):
        if os.name != 'nt':
            raise ValueError('Windows lifecycle handle is unavailable')
        import ctypes
        from ctypes import wintypes
        if (not isinstance(value, str) or not value.isascii() or not value.isdecimal()
                or not 0 < int(value) < (1 << (8 * ctypes.sizeof(ctypes.c_void_p))) - 1):
            raise ValueError('Invalid lifecycle handle')
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetHandleInformation.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetHandleInformation.restype = wintypes.BOOL
        kernel.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
        kernel.SetHandleInformation.restype = wintypes.BOOL
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel, self.handle = kernel, int(value)
        self.loop, self.stop, self.timer, self.error = loop, stop, None, None
        flags = wintypes.DWORD()
        # Ownership of a malformed/unverified numeric handle is not assumed.
        if not kernel.GetHandleInformation(self.handle, ctypes.byref(flags)):
            raise OSError('Invalid lifecycle handle')
        try:
            if not kernel.SetHandleInformation(self.handle, 1, 0):
                raise OSError('Lifecycle inheritance could not be cleared')
            self._poll(startup=True)
        except BaseException:
            self.close()
            raise

    def _poll(self, *, startup=False):
        self.timer = None
        status = self.kernel.WaitForSingleObject(self.handle, 0)
        if status == 0:
            self.stop.set()
        elif status == 258:
            self.timer = self.loop.call_later(.1, self._poll)
        else:
            if startup:
                raise OSError('Lifecycle wait unavailable')
            self.error = OSError('Lifecycle wait failed')
            self.stop.set()

    def close(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
