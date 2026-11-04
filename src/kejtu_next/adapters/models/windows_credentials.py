"""Bounded, per-user Windows Credential Manager storage; no credential files.

Win32 contracts: learn.microsoft.com/windows/win32/api/wincred/
ns-wincred-credentialw and nf-wincred-{credreadw,credwritew,creddeletew,credfree}.
"""
import ctypes
import hashlib
import json
import threading

from ...core.credentials import validate_key
from ...core.records import Failure


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
MAX_BLOB_BYTES = 5 * 512
ERROR_NOT_FOUND = 1168
_LOCK = threading.RLock()


class FILETIME(ctypes.Structure):
    _layout_ = 'ms'
    _fields_ = [('low', ctypes.c_uint32), ('high', ctypes.c_uint32)]


class CREDENTIALW(ctypes.Structure):
    # Windows DWORD is always 32-bit; native alignment adds pointer padding.
    # Do not use packing=1 or Unix-sized c_ulong for this Windows ABI.
    _layout_ = 'ms'
    _fields_ = [
        ('Flags', ctypes.c_uint32), ('Type', ctypes.c_uint32),
        ('TargetName', ctypes.c_wchar_p), ('Comment', ctypes.c_wchar_p),
        ('LastWritten', FILETIME), ('CredentialBlobSize', ctypes.c_uint32),
        ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)),
        ('Persist', ctypes.c_uint32), ('AttributeCount', ctypes.c_uint32),
        ('Attributes', ctypes.c_void_p), ('TargetAlias', ctypes.c_wchar_p),
        ('UserName', ctypes.c_wchar_p),
    ]


PCREDENTIALW = ctypes.POINTER(CREDENTIALW)


class _NativeAPI:
    def __init__(self):
        # Resolve only the system DLL, never a DLL beside an imported archive.
        library = ctypes.WinDLL('advapi32.dll', use_last_error=True, winmode=0x800)
        self.read = library.CredReadW
        self.read.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                              ctypes.POINTER(PCREDENTIALW)]
        self.read.restype = ctypes.c_int32
        self.write = library.CredWriteW
        self.write.argtypes = [PCREDENTIALW, ctypes.c_uint32]
        self.write.restype = ctypes.c_int32
        self.delete = library.CredDeleteW
        self.delete.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        self.delete.restype = ctypes.c_int32
        self.free = library.CredFree
        self.free.argtypes = [ctypes.c_void_p]
        self.free.restype = None
        self.last_error = ctypes.get_last_error


def _failure():
    return Failure('credential_store',
        'Windows Credential Manager could not complete or verify the operation. '
        'Reload the saved key status before trying again; the stored key may have changed.')


class WindowsCredentials:
    def __init__(self, attributes, *, _api=None):
        # Hash the complete existing identity tuple, including field names. This
        # avoids separator ambiguity and case-insensitive Windows target aliases.
        if (len(attributes) != 6 or attributes[::2] != ('application', 'provider', 'endpoint')
                or any(not isinstance(v, str) or not 1 <= len(v) <= 2048
                       or '\0' in v for v in attributes)):
            raise _failure()
        encoded = json.dumps(attributes, ensure_ascii=True, separators=(',', ':')).encode('ascii')
        self.target = 'Kejtu/api-key/v1/' + hashlib.sha256(encoded).hexdigest()
        self._api = _api

    def _native(self):
        if self._api is None:
            try:
                self._api = _NativeAPI()
            except (OSError, AttributeError):
                raise _failure() from None
        return self._api

    def _read(self, api):
        result = PCREDENTIALW()
        if not api.read(self.target, CRED_TYPE_GENERIC, 0, ctypes.byref(result)):
            if api.last_error() == ERROR_NOT_FOUND:
                return None
            raise _failure()
        try:
            if not result:
                raise _failure()
            credential = result.contents
            size = credential.CredentialBlobSize
            if (credential.Type != CRED_TYPE_GENERIC or not 1 <= size <= MAX_BLOB_BYTES
                    or not credential.CredentialBlob):
                raise _failure()
            # CredentialBlob is application-defined bytes, not a NUL-terminated
            # Windows password. Bound the copy before dereferencing it.
            value = ctypes.string_at(credential.CredentialBlob, size).decode('ascii')
            try:
                return validate_key(value)
            except Failure:
                raise _failure() from None
        finally:
            if result:
                api.free(result)

    def read(self):
        try:
            with _LOCK:
                return self._read(self._native())
        except (OSError, ValueError, ctypes.ArgumentError):
            raise _failure() from None

    def save(self, key):
        key = validate_key(key)
        if len(key) > MAX_BLOB_BYTES:
            raise Failure('credential_required',
                'Windows Credential Manager supports API keys up to 2560 ASCII characters.')
        try:
            with _LOCK:
                api = self._native()
                blob = (ctypes.c_ubyte * len(key)).from_buffer_copy(key.encode('ascii'))
                credential = CREDENTIALW(Type=CRED_TYPE_GENERIC, TargetName=self.target,
                    CredentialBlobSize=len(key), CredentialBlob=blob,
                    # Persists across logons of this SAME user on this computer.
                    Persist=CRED_PERSIST_LOCAL_MACHINE)
                try:
                    if not api.write(ctypes.byref(credential), 0):
                        raise _failure()
                    if self._read(api) != key:
                        raise _failure()
                finally:
                    ctypes.memset(blob, 0, len(blob))
        except (OSError, ValueError, ctypes.ArgumentError):
            raise _failure() from None

    def remove(self):
        try:
            with _LOCK:
                api = self._native()
                # Delete directly so a malformed stored blob can also be removed.
                if not api.delete(self.target, CRED_TYPE_GENERIC, 0):
                    if api.last_error() != ERROR_NOT_FOUND:
                        raise _failure()
                if self._read(api) is not None:
                    raise _failure()
        except (OSError, ValueError, ctypes.ArgumentError):
            raise _failure() from None
