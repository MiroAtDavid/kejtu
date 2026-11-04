"""Read a bounded public GitHub skill directory at a resolved immutable commit.

No git subprocess, archive extraction, credentials, installation hooks or package
code execution. Every TLS connection uses the existing DNS-pinned public-network
check. Git tree identities and downloaded blob bytes must agree.
"""
import hashlib
import http.client
import json
import re
import ssl
import time
from urllib.parse import quote, unquote, urlsplit

from ...core.skills import MAX_FILES, MAX_FILE as MAX_FILE_BYTES, MAX_PACKAGE as MAX_PACKAGE_BYTES, path as relative
from .network import connect_public


class PublicHTTPS(http.client.HTTPSConnection):
    def connect(self):
        connection = connect_public(self.host, 443, timeout=min(self.timeout, 5))
        try:
            self.sock = self._context.wrap_socket(connection, server_hostname=self.host)
        except BaseException:
            connection.close()
            raise


def fetch(url, limit, *, check, deadline):
    check()
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in ('api.github.com', 'raw.githubusercontent.com')
            or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment):
        raise ValueError('Only public GitHub API/raw HTTPS downloads are supported.')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('Skill import time allowance reached.')
    connection = PublicHTTPS(parsed.hostname, timeout=min(10, remaining), context=ssl.create_default_context())
    try:
        connection.request('GET', parsed.path + ('?' + parsed.query if parsed.query else ''),
                           headers={'User-Agent': 'Kejtu-Agent-Skills', 'Accept': 'application/vnd.github+json', 'Accept-Encoding': 'identity'})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f'GitHub returned HTTP {response.status}; the skill was not installed.')
        data = bytearray()
        while True:
            check()
            if time.monotonic() >= deadline:
                raise ValueError('Skill import time allowance reached.')
            chunk = response.read(min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError('GitHub response exceeds the bounded import allowance.')
        check()
        return bytes(data)
    finally:
        connection.close()


def source(url, path=None, ref=None):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query or parsed.fragment:
        raise ValueError('Use a public https://github.com/OWNER/REPO[/tree/REF/PATH] URL.')
    parts = [unquote(p) for p in parsed.path.strip('/').split('/')]
    if len(parts) < 2 or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', s) for s in parts[:2]):
        raise ValueError('Invalid GitHub repository.')
    owner, repo = parts[:2]
    if repo.endswith('.git'):
        repo = repo[:-4]
    if len(parts) > 2:
        if len(parts) < 4 or parts[2] not in ('tree', 'blob') or path is not None or ref is not None:
            raise ValueError('Paste a GitHub repository link, or open the skill folder on GitHub and copy its URL.')
        ref = parts[3]
        path = '/'.join(parts[4:])
        if parts[2] == 'blob':
            if path != 'SKILL.md' and not path.endswith('/SKILL.md'):
                raise ValueError('A GitHub file link must point to SKILL.md.')
            path = '' if path == 'SKILL.md' else path[:-len('/SKILL.md')]
    path = path or ''
    if path:
        relative(path)
    if ref is not None and (not ref or len(ref) > 200 or any(ord(c) < 32 for c in ref)):
        raise ValueError('Invalid GitHub version. Use a folder link with a commit ID instead of a branch name containing slashes.')
    return owner, repo, path, ref


def download(url, *, path=None, ref=None, check=lambda: None, timeout=30, deadline=None, get=fetch):
    owner, repo, path, ref = source(url, path, ref)
    deadline = deadline or time.monotonic() + timeout
    api = f'https://api.github.com/repos/{owner}/{repo}'
    def read(url, limit):
        return get(url, limit, check=check, deadline=deadline)
    if ref is None:
        ref = json.loads(read(api, 200000))['default_branch']
    commit = json.loads(read(api + '/commits/' + quote(ref, safe=''), 1000000))['sha']
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('GitHub did not return a full commit identity.')
    tree = json.loads(read(api + '/git/trees/' + commit + '?recursive=1', 8_000_000))
    if tree.get('truncated'):
        raise ValueError('GitHub tree is incomplete; choose a smaller repository.')
    prefix = path + '/' if path else ''
    if not any(r['path'] == prefix + 'SKILL.md' for r in tree['tree']):
        choices = sorted(r['path'][:-len('/SKILL.md')] for r in tree['tree']
                         if r['path'].endswith('/SKILL.md'))
        # A repository link can identify its sole package. Never substitute a
        # different folder for an explicit selection or guess among packages.
        if not path and len(choices) == 1:
            path = relative(choices[0])
            prefix = path + '/'
        elif not choices:
            raise ValueError('No SKILL.md was found in this repository. Choose a repository containing a skill.')
        else:
            message = ('This repository contains several skills. ' if not path else
                       'The selected folder does not contain SKILL.md. ')
            message += 'Open the folder containing the skill you want on GitHub, then paste its URL here.'
            # Native import errors have a 500-character bound. List only whole
            # folder names, leaving room for an honest indication of omissions.
            shown = []
            for choice in choices[:12]:
                if len(message) + len(' Folders: ' + ', '.join(shown + [choice])) > 450:
                    break
                shown.append(choice)
            if shown:
                message += ' Folders: ' + ', '.join(shown) + '.'
            if len(shown) < len(choices):
                message += ' More folders are available in the repository.'
            raise ValueError(message)
    entries = [r for r in tree['tree'] if r['path'].startswith(prefix) and r['type'] != 'tree']
    if not entries or len(entries) > MAX_FILES:
        raise ValueError('Skill folder is missing or exceeds 128 files.')
    if any(r.get('mode') not in ('100644', '100755') or r.get('type') != 'blob' for r in entries):
        raise ValueError('Skill packages cannot contain symlinks or submodules.')
    if sum(r.get('size', MAX_FILE_BYTES + 1) for r in entries) > MAX_PACKAGE_BYTES:
        raise ValueError('Skill folder exceeds 8 MiB.')
    files = {}
    for entry in entries:
        check()
        local = relative(entry['path'][len(prefix):])
        if local in files:
            raise ValueError('Ambiguous duplicate Git tree file path.')
        size = entry.get('size')
        if type(size) is not int or not 0 <= size <= MAX_FILE_BYTES:
            raise ValueError('GitHub skill file exceeds 2 MiB.')
        data = read(f'https://raw.githubusercontent.com/{owner}/{repo}/{commit}/' + quote(entry['path'], safe='/'), size)
        blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if len(data) != size or blob != entry['sha']:
            raise ValueError('Downloaded file does not match the pinned Git blob identity.')
        files[local] = data
    dirname = path.split('/')[-1] if path else repo
    origin = {'kind': 'github', 'repository': f'https://github.com/{owner}/{repo}',
              'requested_ref': ref, 'commit': commit, 'path': path,
              'url': f'https://github.com/{owner}/{repo}/tree/{commit}/{path}'}
    # Retain an upstream root license separately; never rewrite upstream files.
    licenses = [r for r in tree['tree'] if r['path'].casefold() in ('license', 'license.md', 'license.txt', 'copying')
                and r.get('mode') in ('100644', '100755') and r.get('size', 100001) <= 100000]
    origin['repository_licenses'] = []
    for entry in licenses[:4]:
        data = read(f'https://raw.githubusercontent.com/{owner}/{repo}/{commit}/' + quote(entry['path'], safe='/'), entry['size'])
        if hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() != entry['sha']:
            raise ValueError('Repository license changed during download.')
        origin['repository_licenses'].append({'path': entry['path'], 'text': data.decode('utf-8'), 'git_blob': entry['sha']})
    check()
    return files, dirname, origin
