#!/usr/bin/env python3
"""Build the shipped Shelf metadata from original, pinned public GitHub sources.

No package code runs. Every selected package must pass the existing reader and
have recorded licence evidence. Failure leaves the last catalogue untouched.
Use --refresh to resolve newer commits explicitly, then review the resulting diff.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
from kejtu_next.adapters.skills.bundles import read_files
from kejtu_next.adapters.skills.catalogue import digest, validate, MAX_FILES
from kejtu_next.adapters.web.skill_github import fetch
from kejtu_next.core.skills import MAX_FILE, MAX_PACKAGE, path as relative
from kejtu_next.interfaces.native_wire import matches


def build(source, get=fetch, *, refresh=False):
    repo = source['repository']
    if not re.fullmatch(r'https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Use an original public GitHub repository.')
    api = repo.replace('https://github.com/', 'https://api.github.com/repos/')
    deadline = time.monotonic() + 240
    def read(url, limit):
        return get(url, limit, check=lambda: None, deadline=deadline)
    commit = source['commit']
    if refresh:
        commit = json.loads(read(api + '/commits/HEAD', 1_000_000))['sha']
    if not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('A full immutable commit is required.')
    tree = json.loads(read(api + '/git/trees/' + commit + '?recursive=1', 8_000_000))
    if tree.get('truncated'):
        raise ValueError('The repository tree is incomplete.')
    prefix = relative(source['path']) + '/' if source['path'] else ''
    entries = [e for e in tree['tree'] if e['type'] != 'tree' and
               (e['path'].startswith(prefix) or e['path'] in source['license_files'])]
    if (not 1 <= len(entries) <= MAX_FILES or
            any(e.get('mode') not in ('100644', '100755', '120000') or e['type'] != 'blob' for e in entries)):
        raise ValueError('Package has too many files, symbolic links or submodules.')
    files, pins = {}, []
    for item in entries:
        path = relative(item['path'])
        count = item.get('size')
        if type(count) is not int or not 0 <= count <= MAX_FILE:
            raise ValueError('Unbounded package file.')
        body = read(f'https://raw.githubusercontent.com/{repo.removeprefix("https://github.com/")}/{commit}/' + quote(path, safe='/'), count)
        blob = hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest()
        if len(body) != count or blob != item['sha'] or path in files:
            raise ValueError('Upstream file identity or length mismatch.')
        files[path] = body
        pins.append(dict(path=path, bytes=count, sha256=hashlib.sha256(body).hexdigest(), git_blob=blob, mode=item['mode']))
    total = sum(len(b) for b in files.values())
    if total > MAX_PACKAGE:
        raise ValueError('Package exceeds 8 MiB.')
    licences = [files[p].decode('utf-8') for p in source['license_files']]
    phrase = {'MIT': 'Permission is hereby granted, free of charge', 'Apache-2.0': 'Apache License', 'ISC': 'Permission to use, copy, modify'}[source['license']]
    if not licences or not all(phrase in text for text in licences):
        raise ValueError('Licence evidence does not match the selected declaration.')
    found = read_files({p[len(prefix):]: b for p, b in files.items() if p.startswith(prefix)},
                      dict(kind='github', repository=repo, commit=commit, path=source['path']),
                      links=[e['path'][len(prefix):] for e in entries if e['mode'] == '120000' and e['path'].startswith(prefix)])['description']
    if not matches(found, 'shelf_description'):
        raise ValueError('The package preview is not supported by the native contract.')
    if not found['installable']:
        raise ValueError('Package not installable: ' + json.dumps(found['unsupported']))
    entry = dict(source, commit=commit, files=pins, bytes=total, package=found,
                 source_url=repo+'/tree/'+commit+('/'+source['path'] if source['path'] else ''))
    entry['digest'] = digest(entry)
    return validate(entry)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, default=ROOT/'catalogue/sources.json')
    parser.add_argument('--output', type=Path, default=ROOT/'src/kejtu_next/shelf/catalogue.json')
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    entries, failures = [], []
    for source in json.loads(args.sources.read_text())['entries']:
        try:
            entry = build(source, refresh=args.refresh)
            entries.append(entry)
            print(f'{entry["id"]}: {len(entry["package"]["components"])} parts, {entry["bytes"]} bytes')
        except Exception as error:
            failures.append(dict(id=source.get('id'), error=str(error)))
    if failures:
        print(json.dumps(dict(failures=failures), indent=2), file=sys.stderr)
        return 1
    if not 1 <= len(entries) <= 20 or len({e['id'] for e in entries}) != len(entries):
        raise ValueError('The starter shelf requires 1–20 distinct packages.')
    revision = hashlib.sha256(''.join(e['digest'] for e in entries).encode()).hexdigest()
    value = dict(schema=1, revision=revision, entries=entries)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    staging = args.output.with_suffix('.tmp')
    staging.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    staging.replace(args.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
