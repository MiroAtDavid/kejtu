"""User-initiated, compare-before-save edits to an existing project text file."""
import base64
import hashlib
import os
import re
import stat
import time
import uuid

from ...core.records import Failure

MAX_TEXT_BYTES = 2 * 1024 * 1024
CODE_SUFFIXES = frozenset((
    '.py', '.pyi', '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.svelte',
    '.json', '.yaml', '.yml', '.toml', '.sh', '.bash', '.zsh', '.css', '.html',
    '.htm', '.rs', '.go', '.c', '.h', '.cpp', '.hpp', '.java', '.sql', '.r', '.txt',
))


def validate_text(raw):
    if len(raw) > MAX_TEXT_BYTES:
        raise Failure('material_size', 'The text editor supports files up to 2 MiB.')
    try:
        raw.decode('utf-8')
    except UnicodeError:
        raise Failure('material_encoding', 'Choose a UTF-8 text file.') from None
    if b'\0' in raw:
        raise Failure('material_encoding', 'This file contains binary data.')


def changed():
    return Failure('material_changed', 'The file changed on disk. Your draft is still open. Reload to use the disk version.')


def version(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode, info.st_nlink)


def replace_checked(materials, root, target, original, raw, authorize):
    # Stage independently of the original. A short write or interruption before
    # publication leaves the original intact. Unix operations use a held parent.
    with materials._open(root, target.parent, directory=True) as (parent, parent_info):
        windows = os.name == 'nt'
        directory = {} if windows else {'dir_fd': parent}
        temporary = '.kejtu-edit-' + uuid.uuid4().hex
        stage = target.parent / temporary if windows else temporary
        leaf = target if windows else target.name
        fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600, **directory)
        published = False
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(raw)
                out.flush()
                if not windows:
                    if os.fstat(fd).st_gid != original.st_gid:
                        os.fchown(fd, -1, original.st_gid)
                    os.fchmod(fd, stat.S_IMODE(original.st_mode))
                    # Preserve user metadata and access ACLs of the original.
                    with materials._open(root, target) as (source, current):
                        if version(current) != version(original):
                            raise changed()
                        if hasattr(os, 'listxattr'):
                            for name in os.listxattr(source):
                                if name.startswith('user.') or name == 'system.posix_acl_access':
                                    os.setxattr(fd, name, os.getxattr(source, name))
                os.fsync(fd)
                staged = os.fstat(fd)
            authorize()
            current = os.stat(leaf, follow_symlinks=False, **directory)
            if version(current) != version(original) or not os.path.samestat(target.parent.stat(), parent_info):
                raise changed()
            check = os.stat(stage, follow_symlinks=False, **directory)
            if not os.path.samestat(check, staged) or check.st_size != staged.st_size or check.st_nlink != 1:
                raise changed()
            if windows:
                # ReplaceFile preserves the existing file's ACL and attributes.
                import ctypes
                replace = ctypes.WinDLL('kernel32', use_last_error=True).ReplaceFileW
                replace.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32,
                                    ctypes.c_void_p, ctypes.c_void_p]
                if not replace(str(target), str(stage), None, 0, None, None):
                    raise ctypes.WinError(ctypes.get_last_error())
            else:
                os.replace(stage, leaf, src_dir_fd=parent, dst_dir_fd=parent)
            published = True
            if not windows:
                os.fsync(parent)
        finally:
            if not published:
                try:
                    os.unlink(stage, **directory)
                except FileNotFoundError:
                    pass


class CodeSaves:
    """Assemble bounded native request frames in RAM, publish only a complete verified draft."""
    def __init__(self):
        self.pending = {}

    def receive(self, materials, course_id, path, opened_path, sha256, draft_sha256, upload_id, offset, total, chunk_b64):
        if (not isinstance(upload_id, str) or not re.fullmatch('[0-9a-f]{32}', upload_id)
                or not all(isinstance(v, str) and re.fullmatch('[0-9a-f]{64}', v) for v in (sha256, draft_sha256))
                or type(total) is not int or type(offset) is not int or not 0 <= offset <= total <= MAX_TEXT_BYTES
                or not isinstance(chunk_b64, str) or len(chunk_b64) > 48 * 1024):
            raise Failure('material_save', 'Invalid text save frame.')
        try:
            chunk = base64.b64decode(chunk_b64, validate=True)
        except ValueError:
            raise Failure('material_save', 'Invalid text save data.') from None
        end = offset + len(chunk)
        if end > total or (not chunk and total):
            raise Failure('material_save', 'Incomplete text save data.')
        def authorize():
            _, root, target = materials.path(course_id, path)
            if str(target) != opened_path or target.suffix.lower() not in CODE_SUFFIXES:
                raise Failure('material_path', 'Reopen the original code file before saving.')
            if not materials.code_writable(target):
                raise Failure('files_forbidden', 'This file is read-only. Your draft is still open.')
            return root, target

        root, target = authorize()
        key = (course_id, opened_path, sha256, draft_sha256, total)
        now = time.monotonic()
        self.pending = {k:v for k,v in self.pending.items() if now-v[2] < 120}
        if offset == 0:
            if upload_id not in self.pending and len(self.pending) >= 8:
                raise Failure('material_save', 'Other code saves are in progress. Try saving again shortly.')
            self.pending[upload_id] = (key, bytearray(), now)
        state = self.pending.get(upload_id)
        if state is None or key != state[0] or offset != len(state[1]):
            raise Failure('material_save', 'The save was interrupted. Your draft is still open.')
        body = state[1]
        body.extend(chunk)
        if end < total:
            self.pending[upload_id] = (key, body, now)
            return dict(next_offset=end)
        del self.pending[upload_id]
        if hashlib.sha256(body).hexdigest() != draft_sha256:
            raise Failure('material_save', 'Draft verification failed. Your draft is still open.')
        validate_text(body)
        previous, info = materials._read(root, target)
        validate_text(previous)
        digest = hashlib.sha256(previous).hexdigest()
        if digest != draft_sha256:
            if digest != sha256:
                raise changed()
            try:
                replace_checked(materials, root, target, info, body, authorize)
            except OSError:
                raise Failure('material_save', 'Could not confirm the save. Your draft is still open. Check the file before trying again.') from None
        return dict(next_offset=None, sha256=draft_sha256)
