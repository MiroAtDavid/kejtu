"""A shipped metadata shelf; payloads come only from pinned upstream GitHub blobs.

Browsing reads no network and installs nothing. A selected entry is downloaded
once by the existing disposable package reader, verified in full, and described
by the ordinary bundle reader. The coordinator then uses the ordinary owners.
"""
import hashlib
import json
from pathlib import Path
import re

from .skills import encode, path as relative, MAX_FILE, MAX_PACKAGE

SNAPSHOT = Path(__file__).resolve().parents[1] / 'shelf' / 'catalogue.json'
MAX_FILES = 256
PUBLIC = ('id', 'title', 'summary', 'publisher', 'category', 'requirements', 'license',
          'source_url', 'icon', 'bytes', 'digest', 'package')


def digest(entry):
    return hashlib.sha256(encode({k: v for k, v in entry.items() if k != 'digest'}).encode()).hexdigest()


def validate(entry):
    if (not isinstance(entry, dict) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', entry['id'])
            or not re.fullmatch(r'https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', entry['repository'])
            or not re.fullmatch(r'[a-f0-9]{40}', entry['commit'])
            or not re.fullmatch(r'[a-z0-9-]{1,64}', entry['icon'])
            or entry['license'] not in ('MIT', 'Apache-2.0', 'ISC')
            or digest(entry) != entry['digest']):
        raise ValueError('The Shelf entry has an invalid identity or content pin.')
    prefix = relative(entry['path']) + '/' if entry['path'] else ''
    files = entry['files']
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError('The Shelf package exceeds its file allowance.')
    seen, total = set(), 0
    for item in files:
        name = relative(item['path'])
        if (name in seen or type(item['bytes']) is not int or not 0 <= item['bytes'] <= MAX_FILE
                or not re.fullmatch(r'[a-f0-9]{64}', item['sha256'])
                or not re.fullmatch(r'[a-f0-9]{40}', item['git_blob'])
                or item['mode'] not in ('100644', '100755', '120000')
                or not name.startswith(prefix) and name not in entry['license_files']):
            raise ValueError('The Shelf package has invalid or overlapping file pins.')
        seen.add(name)
        total += item['bytes']
    if total != entry['bytes'] or total > MAX_PACKAGE or not set(entry['license_files']) <= seen:
        raise ValueError('The Shelf package exceeds its size allowance or is missing its licence.')
    if not entry['license_files'] or not entry['package']['installable']:
        raise ValueError('The Shelf package has no supported installation or licence.')
    return entry


def read(snapshot=SNAPSHOT):
    raw = snapshot.read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError('The Shelf catalogue is too large.')
    value = json.loads(raw)
    if value['schema'] != 1 or not 1 <= len(value['entries']) <= 32:
        raise ValueError('This Shelf catalogue is not supported.')
    entries, seen = [], set()
    for item in value['entries']:
        checked = validate(item)
        if checked['id'] in seen:
            raise ValueError('Duplicate Shelf package identity.')
        seen.add(checked['id'])
        entries.append(checked)
    return dict(revision=value['revision'], entries=entries)


def public():
    value = read()
    return dict(revision=value['revision'], entries=[{k: e[k] for k in PUBLIC} for e in value['entries']])


def selected(identity, expected):
    entry = next((e for e in read()['entries'] if e['id'] == identity), None)
    if entry is None or entry['digest'] != expected:
        raise ValueError('The Shelf entry changed. Reopen it before installing.')
    return entry
