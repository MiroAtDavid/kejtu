"""Safe regular-file opens for archive ownership and startup metadata.

No ACL or permission policy is emulated. Windows inspects the opened handle and
holds real parent directories against write/rename/delete for the handle lifetime.
"""
from contextlib import AbstractContextManager, contextmanager
import errno
import os
from pathlib import Path
import stat
import tempfile
import uuid


class RegularFile(AbstractContextManager):
    def __init__(self, stream, parents=()):
        self.stream = stream
        self.parents = parents

    def fileno(self):
        return self.stream.fileno()

    def read(self, size=-1):
        return self.stream.read(size)

    def write(self, raw):
        return self.stream.write(raw)

    def flush(self):
        self.stream.flush()

    def close(self):
        try:
            self.stream.close()
        finally:
            for handle in reversed(self.parents):
                _kernel.CloseHandle(handle)
            self.parents = ()

    def __exit__(self, *exc):
        self.close()


def open_regular(path, *, writable=False, create=False, exclusive=False):
    """Open a non-inheritable regular non-symlink file, or fail closed.

    exclusive is native Windows no-sharing ownership; Unix flock remains the
    separate locking protocol. An absent reader path remains FileNotFoundError.
    """
    path = Path(path).absolute()
    if create and not writable:
        raise ValueError('Creation requires a writable owner.')
    if os.name == 'nt':
        return _windows_regular(path, writable, create, exclusive)
    flags = (os.O_RDWR if writable else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK
    if create:
        flags |= os.O_CREAT
    fd = os.open(path, flags, 0o600)
    try:
        os.set_inheritable(fd, False)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, 'Expected a regular file.', str(path))
        stream = os.fdopen(fd, 'r+b' if writable else 'rb')
    except BaseException:
        os.close(fd)
        raise
    return RegularFile(stream)



@contextmanager
def private_directory(path):
    """Create only the final directory privately, or verify without repairing it.

    Checked directory/parent handles survive for the context lifetime. Creation
    does not establish a universal directory durability/power-loss guarantee.
    """
    path = Path(path).absolute()
    if os.name == 'nt':
        parents = _parent_handles(path)
        handle = None
        try:
            _require_local_ntfs_handle(parents[-1], path)
            with _private_security() as security:
                created = bool(_kernel.CreateDirectoryW(str(path), ctypes.byref(security)))
                error = ctypes.get_last_error()
            if not created and error != 183:
                raise ctypes.WinError(error)
            handle = _open(path, 0x20000, 1, 3, 0x02200000)  # READ_CONTROL
            attrs = _attributes(handle)
            if attrs & 0x400 or not attrs & 0x10:
                raise OSError(errno.EINVAL, 'Expected a non-reparse directory.')
            _require_local_ntfs_handle(handle, path)
            _verify_private(handle)
            yield created
        finally:
            if handle is not None:
                _kernel.CloseHandle(handle)
            for parent in reversed(parents):
                _kernel.CloseHandle(parent)
    else:
        try:
            path.mkdir(mode=0o700)
            created = True
        except FileExistsError:
            created = False
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise OSError(errno.EACCES, 'Expected an owner-only directory.')
            yield created
        finally:
            os.close(fd)


def open_private_regular(path, *, lock=False):
    """Opt-in credential policy, checked on the actual opened handle."""
    path = Path(path).absolute()
    if os.name == 'nt' and lock:
        try:
            file = _windows_regular(path, True, True, True, new=True, private=True)
        except OSError as error:
            if getattr(error, 'winerror', None) not in (80, 183):
                raise
            file = open_regular(path, writable=True, exclusive=True)
    else:
        file = open_regular(path, writable=lock, create=lock, exclusive=lock)
    try:
        if os.name == 'nt':
            handle = msvcrt.get_osfhandle(file.fileno())
            _require_local_ntfs_handle(handle, path)
            _verify_private(handle)
        else:
            info = os.fstat(file.fileno())
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise OSError(errno.EACCES, 'Expected an owner-only file.')
        return file
    except BaseException:
        file.close()
        raise

def _flush_file(file):
    if os.name == 'nt':
        handle = msvcrt.get_osfhandle(file.fileno())
        if not _kernel.FlushFileBuffers(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        os.fsync(file.fileno())


def _sync_parent(directory):
    # Windows file-specific write-through rename is handled separately. There
    # is no generic successful directory-fsync substitute in this helper.
    if os.name == 'nt':
        raise OSError(errno.ENOTSUP, 'Generic Windows directory durability is not established.')
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path, raw, *, prefix, published):
    """Replace one bounded settings file; expose visibility before final flush.

    Validation/revision policy remains the caller's. published() runs exactly
    after successful rename, before any later acknowledgement can fail. No
    retry or fallback publishes another revision. Windows is local NTFS only:
    the same write-through file handle performs rename and final file flush.
    This does not implement directory fsync or upgrade-directory durability.
    """
    path = Path(path).absolute()
    temporary = None
    try:
        if os.name == 'nt':
            candidate = path.parent / (prefix + uuid.uuid4().hex)
            with _windows_regular(candidate, True, True, True,
                                  new=True, delete=True, write_through=True, private=True) as target:
                # Cleanup authority starts only after CREATE_NEW plus setup
                # returned our handle. A collision/refusal owns no pathname.
                temporary = candidate
                _require_local_ntfs(target, path)
                # Refuse preexisting aliases/nonfiles without following them.
                try:
                    with open_regular(path):
                        pass
                except FileNotFoundError:
                    pass
                target.write(raw)
                target.flush()
                _flush_file(target)
                _rename_file(target, path)
                temporary = None
                published()
                _flush_file(target)
        else:
            fd, temporary = tempfile.mkstemp(prefix=prefix, dir=path.parent)
            with os.fdopen(fd, 'wb') as target:
                target.write(raw)
                target.flush()
                _flush_file(target)
            os.replace(temporary, path)
            temporary = None
            published()
            _sync_parent(path.parent)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


if os.name == 'nt':
    import ctypes
    from ctypes import wintypes
    import msvcrt

    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    _security = ctypes.WinDLL('advapi32', use_last_error=True)
    _kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.HANDLE)
    _kernel.CreateFileW.restype = wintypes.HANDLE
    _kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel.CloseHandle.restype = wintypes.BOOL
    _kernel.GetFileInformationByHandleEx.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                                     wintypes.LPVOID, wintypes.DWORD)
    _kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _kernel.GetFileType.argtypes = (wintypes.HANDLE,)
    _kernel.GetFileType.restype = wintypes.DWORD
    _kernel.FlushFileBuffers.argtypes = (wintypes.HANDLE,)
    _kernel.FlushFileBuffers.restype = wintypes.BOOL
    _kernel.SetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                                  wintypes.LPVOID, wintypes.DWORD)
    _kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    _kernel.GetVolumeInformationByHandleW.argtypes = (wintypes.HANDLE, wintypes.LPWSTR,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD)
    _kernel.GetVolumeInformationByHandleW.restype = wintypes.BOOL
    _kernel.GetDriveTypeW.argtypes = (wintypes.LPCWSTR,)
    _kernel.GetDriveTypeW.restype = wintypes.UINT
    _kernel.GetCurrentProcess.argtypes = ()
    _kernel.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel.LocalFree.argtypes = (wintypes.HLOCAL,)
    _kernel.LocalFree.restype = wintypes.HLOCAL
    _security.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD,
                                           ctypes.POINTER(wintypes.HANDLE))
    _security.OpenProcessToken.restype = wintypes.BOOL
    _security.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                              wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    _security.GetTokenInformation.restype = wintypes.BOOL
    _security.ConvertSidToStringSidW.argtypes = (wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR))
    _security.ConvertSidToStringSidW.restype = wintypes.BOOL
    _security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD))
    _security.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    _kernel.CreateDirectoryW.argtypes = (wintypes.LPCWSTR, wintypes.LPVOID)
    _kernel.CreateDirectoryW.restype = wintypes.BOOL
    _security.GetSecurityInfo.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID), wintypes.LPVOID, ctypes.POINTER(wintypes.LPVOID),
        wintypes.LPVOID, ctypes.POINTER(wintypes.LPVOID))
    _security.GetSecurityInfo.restype = wintypes.DWORD
    _security.GetSecurityDescriptorControl.argtypes = (wintypes.LPVOID,
        ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD))
    _security.GetSecurityDescriptorControl.restype = wintypes.BOOL
    _security.GetAclInformation.argtypes = (wintypes.LPVOID, wintypes.LPVOID,
        wintypes.DWORD, ctypes.c_int)
    _security.GetAclInformation.restype = wintypes.BOOL
    _security.GetAce.argtypes = (wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID))
    _security.GetAce.restype = wintypes.BOOL
    _INVALID = wintypes.HANDLE(-1).value

    class _AttributeTag(ctypes.Structure):
        _fields_ = [('attributes', wintypes.DWORD), ('tag', wintypes.DWORD)]

    def _open(path, access, sharing, disposition, flags, security=None):
        if '\0' in str(path):
            raise OSError(errno.EINVAL, 'NUL is not a file path.', str(path))
        # NULL attributes or explicit inherit=False keep this native handle private.
        handle = _kernel.CreateFileW(str(path), access, sharing, security,
                                     disposition, flags, None)
        if handle == _INVALID:
            error = ctypes.get_last_error()
            raise ctypes.WinError(error)
        return handle

    def _attributes(handle):
        info = _AttributeTag()
        if not _kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.attributes

    def _parent_handles(path):
        parents = []
        try:
            for directory in reversed((path.parent, *path.parent.parents)):
                parent = _open(directory, 0, 1, 3, 0x02200000)
                parents.append(parent)
                attrs = _attributes(parent)
                if attrs & 0x400 or not attrs & 0x10:
                    raise OSError(errno.EINVAL, 'Directory is a reparse point or non-directory.', str(directory))
            return parents
        except BaseException:
            for parent in reversed(parents):
                _kernel.CloseHandle(parent)
            raise

    def _windows_regular(path, writable, create, exclusive, *, new=False,
                         delete=False, write_through=False, private=False):
        if private and not new:
            raise ValueError('Private descriptor applies only to a newly created file.')
        parents = []
        handle = None
        fd = None
        try:
            # OPEN_REPARSE_POINT applies to the leaf, so inspect/hold every
            # directory root-to-leaf as well. Excluding SHARE_WRITE/DELETE prevents
            # reparse mutation or replacement of the checked components.
            parents = _parent_handles(path)
            # OPEN_ALWAYS or OPEN_EXISTING, read/write as requested,
            # FILE_FLAG_OPEN_REPARSE_POINT|BACKUP_SEMANTICS. Backup semantics
            # permits inspecting/refusing directory/reparse handles safely.
            access = (0xC0000000 if writable else 0x80000000) | (0x10000 if delete else 0)
            flags = 0x02200000 | (0x80000000 if write_through else 0)
            if private:
                with _private_security() as security:
                    handle = _open(path, access, 0 if exclusive else 1, 1, flags,
                                   ctypes.byref(security))
            else:
                handle = _open(path, access, 0 if exclusive else 1,
                               1 if new else 4 if create else 3, flags)
            attrs = _attributes(handle)
            if attrs & (0x400 | 0x10) or _kernel.GetFileType(handle) != 1:
                raise OSError(errno.EINVAL, 'Expected a regular non-reparse disk file.', str(path))
            fd = msvcrt.open_osfhandle(handle, (os.O_RDWR if writable else os.O_RDONLY) | os.O_BINARY | os.O_NOINHERIT)
            handle = None  # descriptor now owns the native handle
            os.set_inheritable(fd, False)
            stream = os.fdopen(fd, 'r+b' if writable else 'rb')
            fd = None
            return RegularFile(stream, parents)
        except BaseException:
            if fd is not None:
                os.close(fd)
            if handle is not None:
                _kernel.CloseHandle(handle)
            for parent in reversed(parents):
                _kernel.CloseHandle(parent)
            raise


    def _require_local_ntfs(file, path):
        # Do not carry the documented NTFS metadata claim to other filesystems
        # or mapped/network drives. This is a refusal, never a flush no-op.
        _require_local_ntfs_handle(msvcrt.get_osfhandle(file.fileno()), path)

    def _require_local_ntfs_handle(handle, path):
        name = ctypes.create_unicode_buffer(64)
        if not _kernel.GetVolumeInformationByHandleW(handle, None, 0, None, None,
                                                     None, name, len(name)):
            raise ctypes.WinError(ctypes.get_last_error())
        if name.value != 'NTFS' or _kernel.GetDriveTypeW(path.anchor) not in (2, 3):
            raise OSError(errno.ENOTSUP, 'Settings write-through requires a local NTFS volume.')

    class _RenameInfo(ctypes.Structure):
        _fields_ = [('ReplaceIfExists', ctypes.c_ubyte), ('RootDirectory', wintypes.HANDLE),
                    ('FileNameLength', wintypes.DWORD), ('FileName', wintypes.WCHAR * 1)]

    def _rename_file(file, path):
        # Same-directory, same-volume rename through the write-through source
        # handle. Do not substitute MoveFileEx copy/delete or ReplaceFile flags.
        raw = str(path).encode('utf-16le')
        size = max(ctypes.sizeof(_RenameInfo), _RenameInfo.FileName.offset + len(raw) + 2)
        buffer = ctypes.create_string_buffer(size)
        info = _RenameInfo.from_buffer(buffer)
        info.ReplaceIfExists = 1
        info.RootDirectory = None
        # Win32's FileName is a NUL-terminated WCHAR string even though
        # FileNameLength excludes that terminator. Keep both representations.
        info.FileNameLength = len(raw)
        ctypes.memmove(ctypes.addressof(buffer) + _RenameInfo.FileName.offset, raw, len(raw))
        handle = msvcrt.get_osfhandle(file.fileno())
        if not _kernel.SetFileInformationByHandle(handle, 3, buffer, size):
            raise ctypes.WinError(ctypes.get_last_error())


    class _SidAndAttributes(ctypes.Structure):
        _fields_ = [('sid', wintypes.LPVOID), ('attributes', wintypes.DWORD)]

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [('length', wintypes.DWORD), ('descriptor', wintypes.LPVOID),
                    ('inherit', wintypes.BOOL)]

    def _current_user_sid():
        token = wintypes.HANDLE()
        if not _security.OpenProcessToken(_kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            size = wintypes.DWORD()
            _security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            if ctypes.get_last_error() != 122 or not size.value:  # insufficient buffer
                raise ctypes.WinError(ctypes.get_last_error())
            buffer = ctypes.create_string_buffer(size.value)
            if not _security.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            user = _SidAndAttributes.from_buffer(buffer)
            text = wintypes.LPWSTR()
            if not _security.ConvertSidToStringSidW(user.sid, ctypes.byref(text)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                return text.value
            finally:
                _kernel.LocalFree(ctypes.cast(text, wintypes.HLOCAL))
        finally:
            _kernel.CloseHandle(token)

    @contextmanager
    def _private_security():
        # Same contract as native private records: current process TokenUser
        # owns the new file; protected DACL grants only that user and SYSTEM.
        # No existing file/directory ACL or machine policy is ever changed.
        sid = _current_user_sid()
        descriptor = wintypes.LPVOID()
        sddl = f'O:{sid}D:P(A;;FA;;;{sid})(A;;FA;;;SY)'
        if not _security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
        finally:
            _kernel.LocalFree(descriptor)


    def _sid_string(sid):
        text = wintypes.LPWSTR()
        if not _security.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return text.value
        finally:
            _kernel.LocalFree(ctypes.cast(text, wintypes.HLOCAL))

    def _verify_private(handle):
        owner, acl, descriptor = wintypes.LPVOID(), wintypes.LPVOID(), wintypes.LPVOID()
        error = _security.GetSecurityInfo(handle, 1, 5, ctypes.byref(owner), None,
                                          ctypes.byref(acl), None, ctypes.byref(descriptor))
        if error:
            raise ctypes.WinError(error)
        try:
            control, revision = wintypes.WORD(), wintypes.DWORD()
            if not _security.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
                raise ctypes.WinError(ctypes.get_last_error())
            user = _current_user_sid()
            if not owner or _sid_string(owner) != user or control.value & 1 or not control.value & 0x1000 or not acl:
                raise OSError(errno.EACCES, 'Expected explicit current-user ownership and protected DACL.')
            # ACL_SIZE_INFORMATION contains AceCount, AclBytesInUse, AclBytesFree.
            size = (wintypes.DWORD * 3)()
            if not _security.GetAclInformation(acl, size, ctypes.sizeof(size), 2):
                raise ctypes.WinError(ctypes.get_last_error())
            if size[0] != 2:
                raise OSError(errno.EACCES, 'Expected two private allow entries.')
            seen = set()
            for index in range(size[0]):
                ace = wintypes.LPVOID()
                if not _security.GetAce(acl, index, ctypes.byref(ace)):
                    raise ctypes.WinError(ctypes.get_last_error())
                header = (ctypes.c_ubyte * 4).from_address(ace.value)
                mask = wintypes.DWORD.from_address(ace.value + 4).value
                if (header[0] != 0 or header[1] != 0 or mask != 0x1F01FF
                        or header[2] + 256 * header[3] < 16):
                    raise OSError(errno.EACCES, 'Expected explicit private full-control allow entries.')
                sid = _sid_string(ace.value + 8)
                if sid in seen:
                    raise OSError(errno.EACCES, 'Duplicate private access entry.')
                seen.add(sid)
            if seen != {user, 'S-1-5-18'}:
                raise OSError(errno.EACCES, 'Private access belongs only to current user and SYSTEM.')
        finally:
            _kernel.LocalFree(descriptor)
