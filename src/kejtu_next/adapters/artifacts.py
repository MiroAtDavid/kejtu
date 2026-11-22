"""Pinned artifact download, verification and atomic publication; data only.

Shared by the source installer, the runtime setup tools and later Kejtu's own
package installation. Nothing here executes, imports or interprets the content
it handles: version probes, SDK import checks and package managers stay with
their callers, named as such, so data verification is never mistaken for an
execution check.

Standard library only. The installer loads this file by its checkout path before
any virtual environment exists, so it must not import Kejtu, the archive or an
optional dependency.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile
import tempfile
import urllib.request
import zipfile


class ArtifactError(ValueError):
    """A pinned artifact or its destination failed verification; nothing was published."""


def real_directory(path):
    """Create a private directory, refusing any symlinked component of its path."""
    path = Path(path)
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ArtifactError(f'Installation paths must not be symlinks: {item}')
    if path.exists() and not path.is_dir():
        raise ArtifactError(f'Expected a directory: {path}')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def tree_hash(root):
    """Digest of every regular file's path, size, executable bit and content.

    Symlinks and special files are refused rather than skipped, so a tree that
    passes cannot point outside itself.
    """
    records = []
    for path in sorted(Path(root).rglob('*')):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ArtifactError(f'Unexpected runtime entry: {path}')
        if path.is_file():
            records.append([path.relative_to(root).as_posix(), path.stat().st_size,
                            bool(path.stat().st_mode & 0o111), file_hash(path)])
    return hashlib.sha256(json.dumps(records, separators=(',', ':')).encode()).hexdigest()


def verify_tree(destination, item):
    """Data-only check that an installed tree equals its pinned complete layout."""
    if tree_hash(destination) != item['tree_sha256']:
        raise ArtifactError(f'Installed {item["id"]} differs from the pinned complete runtime; preserved at {destination}. '
                            'Restore it from a backup or move it aside before retrying.')


def download(cache, item, *, user_agent):
    """Return the cached archive, downloading it once at its pinned size and hash.

    A cached copy is reverified before reuse. The download is written beside the
    cache entry and renamed only after its size and hash match.
    """
    path = Path(cache) / item['archive']
    if path.is_symlink():
        raise ArtifactError(f'Archive cache is a symlink: {path}')
    if path.exists():
        if path.stat().st_size != item['bytes'] or file_hash(path) != item['sha256']:
            raise ArtifactError(f'Cached archive failed integrity verification: {path}. Move it aside and retry.')
        return path
    with tempfile.NamedTemporaryFile(dir=cache, prefix='.download-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            request = urllib.request.Request(item['url'], headers={'User-Agent': user_agent})
            with urllib.request.urlopen(request, timeout=60) as response:
                if not response.url.startswith('https://'):
                    raise ArtifactError('Runtime download redirected outside HTTPS.')
                copied = 0
                while chunk := response.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > item['bytes']:
                        raise ArtifactError(f'{item["id"]} download exceeds the pinned size.')
                    stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
            if copied != item['bytes'] or file_hash(temporary) != item['sha256']:
                raise ArtifactError(f'{item["id"]} download failed integrity verification.')
            temporary.rename(path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def extract_archive(archive, staging, item):
    """Extract a gzip or xz tar holding exactly one top directory of regular entries.

    Refuses traversal, absolute names, links and special files, and stops at the
    pinned extracted size. Returns the extracted directory without verifying or
    running anything in it: callers verify with ``verify_tree``.
    """
    # Inspect the bytes: caches need not preserve a publisher's filename suffix.
    with Path(archive).open('rb') as stream:
        magic = stream.read(6)
    mode = 'r:xz' if magic == b'\xfd7zXZ\x00' else 'r:gz'
    with tarfile.open(archive, mode) as source:
        members = source.getmembers()
        total = 0
        for member in members:
            parts = PurePosixPath(member.name).parts
            if (not parts or parts[0] != item['directory'] or '..' in parts
                    or PurePosixPath(member.name).is_absolute()
                    or not (member.isdir() or member.isfile())):
                raise ArtifactError('Runtime archive contains an unexpected path or entry type.')
            total += member.size
            if total > item['unpacked_bytes']:
                raise ArtifactError('Runtime archive exceeds the pinned extracted size.')
        source.extractall(staging, members=members, filter='data')
    return Path(staging) / item['directory']


def extract_wheel(wheel, destination, allowance):
    """Unpack a Python wheel into ``destination`` as plain files; returns the bytes written.

    A wheel is a zip of files meant to be copied, so copying is all that happens:
    nothing is built, imported or run, and no command script is generated. Refused,
    before anything is written: traversal and absolute names, links and special
    entries, encrypted entries, a name that is already there (two packages providing
    one file), more than ``allowance`` bytes, and a wheel that needs its ``.data``
    folder spread over other places, which only an installer program can do.
    """
    destination = Path(destination)
    try:
        with zipfile.ZipFile(wheel) as source:
            members, total, files = source.infolist(), 0, set()
            for member in members:
                name = PurePosixPath(member.filename)
                kind = stat.S_IFMT(member.external_attr >> 16)
                if (not name.parts or name.is_absolute() or '..' in name.parts or '\\' in member.filename
                        or member.flag_bits & 0x1 or kind not in (0, stat.S_IFREG, stat.S_IFDIR)
                        or name.parts[0].endswith('.data')):
                    raise ArtifactError('A Python package holds an entry that cannot be unpacked as a plain file.')
                if member.is_dir():
                    continue
                total += member.file_size
                if total > allowance:
                    raise ArtifactError('The Python packages are larger than the pin allows.')
                if name in files or (destination / name).exists() or (destination / name).is_symlink():
                    raise ArtifactError('Two Python packages provide the same file.')
                files.add(name)
            written = 0
            for member in members:
                if member.is_dir():
                    continue
                path = destination / PurePosixPath(member.filename)
                path.parent.mkdir(parents=True, exist_ok=True)
                with source.open(member) as stream, path.open('xb') as target:
                    while chunk := stream.read(1024 * 1024):
                        written += len(chunk)
                        if written > total:  # more than the entry declared
                            raise ArtifactError('A Python package unpacks to more than it declares.')
                        target.write(chunk)
                path.chmod(0o755 if (member.external_attr >> 16) & 0o111 else 0o644)
    except (zipfile.BadZipFile, EOFError, NotImplementedError, RuntimeError) as error:
        raise ArtifactError('A Python package could not be unpacked.') from error
    return written


def publish(destination, prepare):
    """Publish a completed new component atomically; never replace an existing one.

    ``prepare`` receives a private temporary directory beside the destination
    and returns the completed tree inside it, which is then renamed into place.
    A failure leaves nothing published. Returns False if the destination already
    existed and was left untouched.
    """
    destination = Path(destination)
    if destination.is_symlink():
        raise ArtifactError('A runtime cannot be installed through a symlink.')
    if destination.exists():
        return False
    with tempfile.TemporaryDirectory(prefix='.install-', dir=destination.parent) as temporary:
        prepared = prepare(Path(temporary))
        Path(prepared).rename(destination)
    return True
