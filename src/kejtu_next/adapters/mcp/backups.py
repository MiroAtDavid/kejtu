"""What a backup takes of installed packages, and what a restore still needs.

Three small files beside the connection settings, each with one writer:

``shelf-backup.json``       the person's choices: which packages' data a backup leaves out,
                            and the size above which the last backup is pointed out. Written
                            by the app.
``shelf-backup-last.json``  what the last backup took of package data. Written by the backup.
``shelf-restored.json``     what a restore could not bring back. Written by the restore and
                            removed once the person has seen it.

A package's data is always taken unless the person chose to leave it out: nothing is left
out because of its size. The copying itself belongs to the folder owner (``PackageData``).
Program files and caches are never part of a backup; a package is installed again for those.
"""
import json
import os
from pathlib import Path
import re
import tempfile

from ...core.records import Failure
from .data import PackageData

LIMIT_BYTES = 1024 ** 3
MAX_LIMIT = 1024 ** 5
MAX_PACKAGES = 100
_ID = re.compile(r'[0-9a-f]{32}\Z')


class PackageBackups:
    def __init__(self, directory):
        self.base = Path(directory)
        self.data = PackageData(self.base)

    # The person's choices

    def choices(self):
        """``revision``, ``limit_bytes`` and the installations whose data a backup leaves out."""
        value = self._read('shelf-backup.json')
        if value is None:
            return dict(revision=0, limit_bytes=LIMIT_BYTES, leave_out=[])
        try:
            if (set(value) != {'version', 'revision', 'limit_bytes', 'leave_out'} or value['version'] != 1
                    or type(value['revision']) is not int or value['revision'] < 1
                    or type(value['limit_bytes']) is not int or not 0 <= value['limit_bytes'] <= MAX_LIMIT
                    or not isinstance(value['leave_out'], list) or len(value['leave_out']) > MAX_PACKAGES
                    or any(not isinstance(i, str) or not _ID.fullmatch(i) for i in value['leave_out'])
                    or len(set(value['leave_out'])) != len(value['leave_out'])):
                raise ValueError()
        except (TypeError, ValueError):
            raise Failure('shelf_data', 'The backup choices for packages are not readable; nothing was guessed.') from None
        return dict(revision=value['revision'], limit_bytes=value['limit_bytes'], leave_out=sorted(value['leave_out']))

    def choose(self, installation, include, expected_revision):
        """Take a package's data into backups, or leave it out. Returns the saved choices."""
        if not isinstance(installation, str) or not _ID.fullmatch(installation) or type(include) is not bool:
            raise Failure('shelf_record', 'Invalid backup choice; nothing was changed.')
        current = self.choices()
        if current['revision'] != expected_revision:
            raise Failure('settings_conflict', 'The backup choices changed. Refresh before choosing again.')
        left = set(current['leave_out']) - {installation} | (set() if include else {installation})
        if len(left) > MAX_PACKAGES:
            raise Failure('shelf_record', 'Too many packages are left out of backups; nothing was changed.')
        value = dict(version=1, revision=current['revision'] + 1, limit_bytes=current['limit_bytes'], leave_out=sorted(left))
        self._write('shelf-backup.json', value)
        return self.choices()

    # What the last backup took, and what a restore still needs

    def last(self):
        return self._note('shelf-backup-last.json')

    def restored(self):
        return self._note('shelf-restored.json')

    def note_restore(self, value):
        self._write('shelf-restored.json', dict(value, version=1))

    def seen(self):
        """The person has read what the restore left to do. True when a note was removed."""
        path = self.base / 'shelf-restored.json'
        if path.is_symlink() or not path.is_file():
            return False
        path.unlink()
        return True

    def _note(self, name):
        try:
            value = self._read(name)
        except Failure:
            return None  # a note that cannot be read says nothing; the choices and the data are unaffected
        if not isinstance(value, dict) or value.get('version') != 1:
            return None
        return {key: held for key, held in value.items() if key != 'version'}

    # Files

    def _read(self, name):
        path = self.base / name
        try:
            if path.is_symlink():
                raise OSError()
            with open(path, 'rb') as source:
                raw = source.read(262145)
        except FileNotFoundError:
            return None
        except OSError:
            raise Failure('shelf_data', 'A package backup file could not be opened safely; nothing was guessed.') from None
        try:
            if len(raw) > 262144:
                raise ValueError()
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError()
        except ValueError:
            raise Failure('shelf_data', 'A package backup file is not readable; nothing was guessed.') from None
        return value

    def _write(self, name, value):
        """One private file, replaced whole: written beside it, flushed, then renamed over it."""
        path = self.base / name
        if path.is_symlink():
            raise Failure('shelf_data', 'A package backup file is a link; nothing was written.')
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=self.base, prefix='.shelf-backup-', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            try:
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError:
            raise Failure('shelf_data', 'A package backup file could not be saved. Refresh to see what is saved.') from None


def backup_packages(directory, destination, packages, created_at, running=lambda folder: False):
    """Copy each installed package's own data into a backup, as the person chose. Leaves the live folders alone.

    ``packages`` is the installed packages that keep data, as the caller read them from the
    archive: ``id``, ``identity`` and ``version``. A package's data is taken unless the person
    chose to leave it out; its size never decides. ``running(folder)`` says whether a program
    is using that data folder right now: then nothing is copied and the backup is refused,
    before the copy and again after it. These are diagnostic checks, not a lock:
    the caller must hold exclusive archive ownership and have joined tool writers.

    Returns the receipt's entry, or None when no installed package keeps data. The entry says,
    per package, whether its data was included, left out by choice, or empty, with its size
    either way. It is also saved as the note the app shows when the total passes the limit.
    """
    if not packages:
        return None
    backups = PackageBackups(directory)
    choices = backups.choices()
    items, total = [], 0
    for package in packages:
        folder = backups.data.folder('plugin_data', package['id'])
        if running(folder):
            raise Failure('shelf_busy', f'A tool of the package {package["identity"]} is running. '
                          'Let it finish, then back up again; nothing was copied from it.')
        item = dict(installation_id=package['id'], identity=package['identity'], version=package['version'], bytes=0, files=0)
        if package['id'] in choices['leave_out']:
            # Measured, so the backup says how much it left out.
            size, files, complete = backups.data.measure(package['id'], seconds=600)
            item.update(data='left_out', bytes=size, files=files, measured_all=complete)
        elif backups.data.holds_nothing('plugin_data', package['id']):
            item['data'] = 'empty'
        else:
            copied = backups.data.snapshot(package['id'], Path(destination) / 'shelf' / package['id'] / 'data')
            if running(folder):
                raise Failure('shelf_busy', f'A tool of the package {package["identity"]} started while its data was copied. '
                              'Let it finish, then back up again.')
            item.update(data='included', bytes=copied['bytes'], files=copied['files'], links=copied['links'], not_copied=copied['left_out'])
            total += copied['bytes']
        items.append(item)
    largest = max((i for i in items if i['data'] == 'included'), key=lambda i: i['bytes'], default=None)
    entry = dict(limit_bytes=choices['limit_bytes'], data_bytes=total, over_limit=total > choices['limit_bytes'], items=items)
    backups._write('shelf-backup-last.json', dict(
        version=1, created_at=created_at, data_bytes=total, limit_bytes=choices['limit_bytes'], over_limit=entry['over_limit'],
        largest_identity=largest['identity'] if largest else '', largest_bytes=largest['bytes'] if largest else 0))
    return entry


def restore_packages(directory, source, entry):
    """Put each package's copied data back into a fresh state folder. Returns the installations restored."""
    backups, restored = PackageBackups(directory), []
    for item in (entry or {}).get('items', []):
        if item.get('data') != 'included' or not isinstance(item.get('installation_id'), str) or not _ID.fullmatch(item['installation_id']):
            continue
        backups.data.restore(item['installation_id'], Path(source) / 'shelf' / item['installation_id'] / 'data')
        restored.append(item['installation_id'])
    return restored


def restore_needs(receipt, programs, root=''):
    """What a restored backup does not bring back, as three lists for the person. Reads only its arguments.

    ``programs`` is the installed packages that have program files, as the caller read them from
    the restored archive: program files are never in a backup, so each is installed again.
    Connection keys were left out of the backup or stay in the keyring of the computer they
    were saved on. Package data the person chose to leave out is named with its size.
    ``root`` is the checkout restored into: a backup made in another folder says so, because
    saved connections still name programs by their place in that folder.
    """
    connections = receipt.get('connections') or {}
    keys = [dict(connection=row['connection'], fields=list(row['fields'])[:24], reason=reason)
            for reason, rows in (('left_out', connections.get('values_left_out', [])), ('keyring', connections.get('values_in_keyring', [])))
            for row in rows]
    packages = (receipt.get('packages') or {}).get('items', [])
    return dict(
        backup_created_at=str(receipt.get('created_at', ''))[:64],
        reinstall=[dict(installation_id=p['id'], identity=p['identity']) for p in programs[:MAX_PACKAGES]],
        reconnect=keys[:64],
        data_not_in_backup=[dict(installation_id=i['installation_id'], identity=i['identity'], bytes=i['bytes'])
                            for i in packages if i.get('data') == 'left_out'][:MAX_PACKAGES],
        connections_in_backup=connections.get('included', True) is not False,
        moved_from=str(receipt.get('root', ''))[:1024] if root and receipt.get('root') not in (None, '', str(root)) else '')
