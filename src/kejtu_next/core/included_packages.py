"""Identities and exact resources of the included, ordinary skill packages."""
from functools import cache
from importlib.resources import files
import json

from .skills import encode, sha

PACKAGE_IDS = ('projects', 'learning-study', 'development')


@cache
def definitions():
    root = files('kejtu_next').joinpath('included_packages')
    result = {}
    for ident in PACKAGE_IDS:
        folder = root.joinpath(ident)
        manifest = json.loads(folder.joinpath('plugin.json').read_text())
        skills = {}
        for path in manifest['skills']:
            body = folder.joinpath(path, 'SKILL.md').read_bytes()
            # These shipped frontmatters use JSON strings, valid YAML without an
            # optional import parser. Third-party imports retain their own parser.
            front = body.decode().split('---', 2)[1].strip().splitlines()
            metadata = {key: (json.loads(value) if value.startswith('"') else value)
                        for line in front for key, value in [line.split(': ', 1)]}
            name = metadata['name']
            skill_manifest = dict(name=name, metadata=metadata,
                origin=dict(kind='builtin', package=ident),
                files={'SKILL.md': dict(sha256=sha(body), bytes=len(body))})
            skills[name] = dict(manifest=skill_manifest,
                revision=sha(encode(skill_manifest).encode()), files={'SKILL.md': body})
        version = sha(encode(dict(manifest=manifest, skills={name: item['revision'] for name, item in skills.items()})).encode())
        result[ident] = dict(title=manifest['title'], description=manifest['description'], icon=manifest['icon'], version=version, skills=skills)
    return result


def packages():
    return {name: item for package in definitions().values() for name, item in package['skills'].items()}


def included_skill(name):
    return name in packages()


def included_origin(origin):
    return origin.get('kind') == 'builtin' and origin.get('package') in PACKAGE_IDS
