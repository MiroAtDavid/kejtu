"""Explicit app-wide feature access; absent choices retain existing chat grants.

This policy never grants history/memory, changes source eligibility, or starts a
job. Local controls retain their own authority. One atomic file owns the global
choice; the old grant rows remain unchanged for provenance and receipt recovery.
"""
import json
import os
from .files import open_regular, atomic_write
from ...core.records import Failure

OPTIONS = {'people': ('off', 'read', 'write'), 'reminders': ('off', 'on'), 'tasks': ('off', 'on')}
MAX_REVISION = 9007199254740990


class AccessSettings:
    def __init__(self, store):
        self.store = store
        self.path = store.path.parent / 'access-settings.json'
        self.value = dict(people=None, reminders=None, tasks=None, revision=0)
        try:
            source = open_regular(self.path)
        except FileNotFoundError:
            return
        except OSError:
            raise Failure('access_settings', 'Access settings could not be opened.') from None
        try:
            with source:
                raw = source.read(4097)
            value = json.loads(raw)
            if (len(raw) > 4096 or set(value) != {'version', 'state_id', *OPTIONS, 'revision'}
                    or type(value['version']) is not int or value['version'] != 1
                    or value['state_id'] != store.state_id
                    or type(value['revision']) is not int or not 1 <= value['revision'] <= MAX_REVISION
                    or any(value[k] is not None and value[k] not in modes for k, modes in OPTIONS.items())):
                raise ValueError
            self.value = {k: value[k] for k in (*OPTIONS, 'revision')}
        except (ValueError, TypeError, KeyError, OSError):
            raise Failure('access_settings', 'Access settings are invalid; no older permission was substituted.') from None

    def read(self):
        return dict(self.value)

    def resolve(self, feature, previous):
        choice = self.value[feature]
        return previous if choice is None else choice if feature == 'people' else choice == 'on'

    def require_conversation_control(self, feature):
        if self.value[feature] is not None:
            raise Failure('access_managed', 'This feature is now managed for all chats in Privacy & access.')

    def save(self, feature, value, expected_revision):
        if (not isinstance(feature, str) or feature not in OPTIONS or value not in OPTIONS[feature]
                or type(expected_revision) is not int or not 0 <= expected_revision < MAX_REVISION):
            raise Failure('access_settings', 'Choose an available feature access value and current revision.')
        if self.store.db.in_transaction or self.store.active_turn_id() is not None:
            raise Failure('busy', 'Finish the current operation before changing access.')
        if expected_revision != self.value['revision']:
            raise Failure('settings_conflict', 'Access settings changed. Reload before saving.')
        if value == self.value[feature]:
            return self.read()
        updated = dict(self.value, **{feature: value}, revision=expected_revision + 1)
        def publish():
            self.value = updated
        try:
            atomic_write(self.path, json.dumps(dict(version=1, state_id=self.store.state_id, **updated)).encode(),
                         prefix='.access-settings-', published=publish)
        except OSError:
            raise Failure('access_settings', 'Save could not be confirmed. Reload the saved access before trying again.') from None
        return self.read()
