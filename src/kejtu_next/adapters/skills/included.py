"""Publish included skills through the existing Shelf and Skill stores at startup."""
import uuid

from ...core.included_packages import definitions, included_origin
from ...core.records import Failure
from ...core.skills import content_fingerprint, encode, sha


def install_included(store):
    # Every effect is in this archive. Joining the owners' transactions makes the
    # whole publication atomic: an interruption leaves either the old or new set,
    # never a half-installed default or a re-enabled saved choice. No IO or model
    # work occurs inside the transaction; read all shipped resources first.
    supplied = definitions()
    if store.db.in_transaction:
        raise Failure('package_included_busy', 'Finish the current archive transaction before installing included packages.')
    with store.db:
        for ident, package in supplied.items():
            installation = uuid.uuid5(uuid.NAMESPACE_URL, 'kejtu:included:'+ident).hex
            old = store.shelf.installation(installation)
            origin = dict(kind='builtin', package=ident, description=package['description'], icon=package['icon'])
            if old and (old['identity'] != package['title'] or not included_origin(old['origin'])):
                raise Failure('package_included_conflict', 'An included package identity is occupied; no package was changed.')
            members = {row['key']: row for row in store.shelf.members(installation)} if old else {}
            if old and old['state'] == 'installed' and old['version'] == package['version']:
                if set(members) != set(package['skills']):
                    raise Failure('package_included_integrity', 'An included package has missing or unexpected parts.')
                for name, skill in package['skills'].items():
                    if store.skills.head(name) != skill['revision']:
                        raise Failure('package_included_integrity', 'An included skill differs from the app version.')
                    store.skills.file(name, skill['revision'])
                continue
            key = uuid.uuid4().hex
            intent = sha(encode([ident, package['version']]).encode())
            store.shelf.begin('update' if old else 'install', key, intent,
                installation_id=installation, identity=package['title'], version=package['version'],
                origin=origin, included=True, transaction=False)
            steps = {name: store.shelf.prepare(key, 'skill', name, 'register', transaction=False)
                     for name in package['skills']}
            for name, skill in package['skills'].items():
                owner = store.shelf.owner('skill', name)
                if owner is not None and owner != installation:
                    raise Failure('package_included_conflict', 'Another package owns an included skill; no package was changed.')
                def record(value, name=name):
                    pin = dict(revision=value['revision'], content_sha256=content_fingerprint(value['manifest']))
                    store.shelf.settle_within(key, steps[name], 'applied', attach=('skill', name, pin),
                        **({'detach': members[name]['id']} if name in members else {}))
                store.skills.install(skill, store.skills.epoch(), update=True, included=True,
                                     transaction=False, record=record)
            store.shelf.finish(key, 'completed', transaction=False)
