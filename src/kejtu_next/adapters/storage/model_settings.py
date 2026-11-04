"""One saved model-profile authority, independent of immutable conversations."""
from dataclasses import asdict
import json
import os
from pathlib import Path
from .files import open_regular, atomic_write

from ...core.records import Failure, Profile
from ...core.providers import PROVIDERS


class SavedModels:
    def __init__(self, directory: Path, state_id: str, seed: Profile):
        self.path = directory / 'model-settings.json'
        self.state_id = state_id
        self.profiles = {name: Profile(spec.default_model, spec.endpoint, spec.default_context,
            spec.default_generation, thinking_level=spec.thinking.default, provider=name)
            for name, spec in PROVIDERS.items()}
        self.profiles[seed.provider] = seed
        self.active, self.revision = seed.provider, 0
        self._read()

    def _read(self):
        try:
            source = open_regular(self.path)
        except FileNotFoundError:
            return
        except OSError:
            raise Failure('model_settings', 'Saved model settings could not be opened.') from None
        try:
            with source:
                raw = source.read(16385)
            if len(raw) > 16384:
                raise ValueError
            value = json.loads(raw)
            if (set(value) != {'version', 'state_id', 'revision', 'active', 'profiles'}
                    or value['version'] not in (1, 2) or value['state_id'] != self.state_id
                    or type(value['revision']) is not int or value['revision'] < 1
                    or value['active'] not in value['profiles']
                    or not set(value['profiles']) <= set(PROVIDERS)
                    or (value['version'] == 1 and set(value['profiles']) != {'ollama', 'deepseek'})):
                raise ValueError
            profiles = {name: Profile(**profile) for name, profile in value['profiles'].items()}
            if any(profile.provider != name for name, profile in profiles.items()):
                raise ValueError
            self.profiles, self.active, self.revision = self.profiles | profiles, value['active'], value['revision']
        except (OSError, ValueError, TypeError, KeyError):
            raise Failure('model_settings', 'Saved model settings are invalid; no launch-profile fallback was used.') from None

    @property
    def profile(self):
        return self.profiles[self.active]

    def save(self, profile, active, expected_revision):
        if expected_revision != self.revision:
            raise Failure('settings_conflict', 'Model settings changed. Reload them before saving.')
        profiles = self.profiles | {profile.provider: profile}
        if active not in profiles:
            raise Failure('model_settings', 'Choose a registered model provider.')
        if self.revision > 0 and profiles == self.profiles and active == self.active:
            return
        value = dict(version=2, state_id=self.state_id, revision=self.revision + 1,
                     active=active, profiles={name: asdict(p) for name, p in profiles.items()})
        raw = json.dumps(value, ensure_ascii=True).encode()
        if len(raw) > 16384:
            raise Failure('model_settings', 'Model settings exceed the supported size.')
        def publish():
            self.profiles, self.active, self.revision = self.profiles | profiles, active, value['revision']
        try:
            atomic_write(self.path, raw, prefix='.model-settings-', published=publish)
        except OSError:
            raise Failure('model_settings', 'Model settings could not be confirmed. Reload to inspect the saved values.') from None
