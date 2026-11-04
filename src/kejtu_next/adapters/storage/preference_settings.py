"""Local recall sensitivity, separate from model budgets and memory authority."""
import json
import os
from .files import open_regular, atomic_write
from ...core.preferences import STRENGTHS
from ...core.records import Failure


class PreferenceSettings:
    def __init__(self, store):
        self.store = store
        self.path = store.path.parent / 'preference-settings.json'
        self.value = dict(strength='subtle', revision=0)
        try:
            source = open_regular(self.path)
        except FileNotFoundError:
            return
        except OSError:
            raise Failure('preference_settings', 'Preference settings could not be opened.') from None
        try:
            with source:
                raw = source.read(4097)
            value = json.loads(raw)
            if (len(raw) > 4096 or set(value) != {'version', 'state_id', 'strength', 'revision'}
                    or type(value['version']) is not int or value['version'] != 1
                    or value['state_id'] != store.state_id or value['strength'] not in STRENGTHS
                    or type(value['revision']) is not int or value['revision'] < 1):
                raise ValueError
            self.value = dict(strength=value['strength'], revision=value['revision'])
        except (ValueError, TypeError, KeyError, OSError):
            raise Failure('preference_settings', 'Preference settings are invalid; no fallback was used.') from None

    def read(self):
        return dict(self.value)

    def save(self, strength, expected_revision):
        if strength not in STRENGTHS or type(expected_revision) is not int or expected_revision < 0:
            raise Failure('preference_settings', 'Choose an available preference sensitivity.')
        if self.store.active_turn_id() is not None:
            raise Failure('busy', 'Wait for the current reply before changing preference sensitivity.')
        if expected_revision != self.value['revision']:
            raise Failure('settings_conflict', 'Preference settings changed. Reload before saving.')
        if strength == self.value['strength']:
            return self.read()
        value = dict(strength=strength, revision=expected_revision + 1)
        def publish():
            self.value = value
        try:
            atomic_write(self.path, json.dumps(dict(version=1, state_id=self.store.state_id, **value)).encode(),
                         prefix='.preference-settings-', published=publish)
        except OSError:
            raise Failure('preference_settings', 'Save could not be confirmed. Reload to inspect the saved setting.') from None
        return self.read()
