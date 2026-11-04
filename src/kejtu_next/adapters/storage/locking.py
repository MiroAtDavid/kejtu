"""Cooperating archive ownership shared by startup and explicit upgrade.

Never unlink the lock: another owner could otherwise hold a different inode.
This lock does not replace directory ACLs or confine noncooperating processes.
"""
import os
import errno
from pathlib import Path
from .files import open_regular
from ...core.records import Failure


def acquire_owner_lock(path, *, create=False):
    """Acquire immediately or fail; OS close/crash releases ownership.

    Unix retains historical flock interoperability. Both Windows owners use
    the same exclusive no-share handle; no existence or chmod emulation.
    """
    if Path(path).parent.is_symlink():
        raise Failure('foreign_store', 'Archive directory must not be a symlink.')
    lock = None
    try:
        lock = open_regular(path, writable=True, create=create, exclusive=True)
        if os.name != 'nt':
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock
    except OSError as error:
        if lock is not None:
            lock.close()
        if isinstance(error, BlockingIOError) or getattr(error, 'winerror', None) in (32, 33):
            raise Failure('store_busy', 'Another process owns this archive; close it before opening or upgrading.') from None
        if error.errno in (errno.EINVAL, errno.ELOOP):
            raise Failure('foreign_store', 'Archive ownership requires a regular non-alias file.') from None
        raise
    except BaseException:
        if lock is not None:
            lock.close()
        raise
