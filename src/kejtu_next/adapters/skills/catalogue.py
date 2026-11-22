"""Verify original online package bytes before passing them to the bundle reader.

The bootstrap supplies the existing public HTTPS adapter. This reader never owns
network policy, credentials, package execution or durable installation effects.
"""
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from ...core.shelf_catalogue import digest, validate, read, selected, public, MAX_FILES
from .bundles import read_files


def download(entry, check=lambda: None, *, get):
    """Download original bytes, verify both recorded SHA256 and Git blob identity."""
    validate(entry)
    deadline = time.monotonic() + 150
    base = entry['repository'].removeprefix('https://github.com/')

    def one(item):
        if time.monotonic() > deadline:
            raise ValueError('The package download exceeded its time allowance.')
        url = f'https://raw.githubusercontent.com/{base}/{entry["commit"]}/' + quote(item['path'], safe='/')
        body = get(url, item['bytes'], check=lambda: None, deadline=deadline)
        blob = hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest()
        if (len(body) != item['bytes'] or hashlib.sha256(body).hexdigest() != item['sha256']
                or blob != item['git_blob']):
            raise ValueError('The download does not match the selected upstream package; nothing was installed.')
        return item['path'], body

    # Bounded independent HTTP reads, all killed/joined with the disposable reader.
    with ThreadPoolExecutor(max_workers=4) as pool:
        downloaded = {}
        for path, body in pool.map(one, entry['files']):
            check()
            downloaded[path] = body
    check()
    prefix = entry['path'] + '/' if entry['path'] else ''
    files = {p[len(prefix):]: b for p, b in downloaded.items() if p.startswith(prefix)}
    origin = dict(kind='github', repository=entry['repository'], commit=entry['commit'], path=entry['path'],
                  repository_licenses=[dict(path=p, text=downloaded[p].decode('utf-8')) for p in entry['license_files']])
    result = read_files(files, origin, check, links=[e['path'][len(prefix):] for e in entry['files']
                                                   if e['mode'] == '120000' and e['path'].startswith(prefix)])
    expected = dict(entry['package'], directory=result['description']['directory'])
    if result['description'] != expected:
        raise ValueError('The package description no longer matches this Shelf entry; nothing was installed.')
    return result
