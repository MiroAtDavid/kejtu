"""Trusted Windows packaged process lifetime; this is not an OS sandbox.

Bundled Files enforces folder policy in its request wrapper. Other reviewed
packaged connections run as trusted programs with normal Windows account access.
The Job Object owns descendants so Stop/revocation also stops their children,
including when the Python worker terminates unexpectedly.
"""
import ctypes
import os
from pathlib import Path, PureWindowsPath
import re

from ...core.records import Failure

_job = None


def folder_input(raw):
    """Refuse network/device paths before any Windows filesystem resolution."""
    path = PureWindowsPath(raw)
    if (not isinstance(raw, str) or not re.match(r'^[A-Za-z]:[\\/]', raw)
            or not path.is_absolute() or len(raw.encode()) > 4096
            or any(ord(c) < 32 for c in raw) or ':' in raw[2:]
            or any(part.endswith((' ', '.')) or any(c in part for c in '<>"|?*')
                   or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part)
                   for part in path.parts[1:])):
        raise Failure('files_path', 'Choose a local drive folder using its complete path.')
    return path


def environment(cache):
    kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x800)
    function = kernel.GetWindowsDirectoryW
    function.argtypes = [ctypes.POINTER(ctypes.c_wchar), ctypes.c_uint32]
    function.restype = ctypes.c_uint32
    buffer = ctypes.create_unicode_buffer(32768)
    size = function(buffer, len(buffer))
    if not 0 < size < len(buffer):
        raise OSError('Windows directory unavailable.')
    folder_input(buffer.value)
    return {'SystemRoot': buffer.value, 'TEMP': str(cache), 'TMP': str(cache),
            'HOME': str(cache), 'USERPROFILE': str(cache), 'PYTHONIOENCODING': 'utf-8',
            'PYTHONDONTWRITEBYTECODE': '1'}


def own_process_tree():
    global _job
    import win32api
    import win32job
    if _job is not None:
        raise ValueError('Files worker already owns a process job.')
    # pywin32 312 requires a string name. Use the native NULL-name form so the
    # job cannot collide with or reopen another process's named object.
    kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x800)
    create = kernel.CreateJobObjectW
    create.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    create.restype = ctypes.c_void_p
    job = create(None, None)
    if not job: raise ctypes.WinError(ctypes.get_last_error())
    try:
        os.set_handle_inheritable(int(job), False)
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
        win32job.AssignProcessToJobObject(job, win32api.GetCurrentProcess())
    except BaseException:
        win32api.CloseHandle(job)
        raise
    # Retain until process exit. Closing this job also kills its owning worker.
    _job = job


def is_builtin(row):
    from .windows_connections import known
    return (isinstance(row, dict) and row.get('transport') == 'stdio'
            and (row.get('builtin_files') is True or known(row.get('command'))))


def launch(row):
    """Resolve only installed Files inputs; imported commands cannot select this."""
    if not is_builtin(row):
        raise ValueError('Only reviewed packaged connections have a Windows launcher.')
    root = Path(__file__).resolve().parents[4]
    if not row.get('builtin_files'):
        from .windows_connections import resolve
        command, args = resolve(root, row)
        return command, args, environment(row['cache'])
    from .files import Files
    from types import SimpleNamespace
    runtime, entry, node = Files(SimpleNamespace(root=root)).runtime()
    if row['command'] != node or row['args'][0] != str(entry):
        raise ValueError('Files runtime identity changed.')
    shared = row['shared_folders']
    if not shared or row['args'][1:] != [grant['path'] for grant in shared]:
        raise ValueError('Files grants changed before startup.')
    if row.get('network') is not False or row.get('env') or row.get('write_paths'):
        raise ValueError('Unexpected built-in Files configuration.')
    wrapper = Path(__file__).with_name('windows_files_server.mjs')
    if not wrapper.is_file():
        raise ValueError('Files permission component is missing.')
    import json
    env = environment(row['cache'])
    env['KEJTU_FILES_POLICY'] = json.dumps(dict(runtime=str(runtime),
        shared_folders=shared, private_paths=row['private_paths']), ensure_ascii=True)
    return node, [str(wrapper)], env
