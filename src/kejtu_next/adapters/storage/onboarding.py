"""Archive-bound, atomic first-run state; no inference, notes or permissions."""
import json
import os
from .files import open_regular, atomic_write
from ...core.records import Failure
from ...core.onboarding import GUIDE

STATES = ('not_offered', 'available', 'active', 'completed', 'skipped')


class Onboarding:
    def __init__(self, store):
        self.store = store
        self.path = store.path.parent / 'onboarding.json'
        # Absence identifies an older installation, not an invitation to re-onboard.
        self.value = dict(status='not_offered', conversation_id=None, revision=0)
        try:
            source = open_regular(self.path)
        except FileNotFoundError:
            return
        except OSError:
            raise Failure('onboarding_state', 'Getting-started state could not be opened.') from None
        try:
            with source:
                raw = source.read(4097)
            value = json.loads(raw)
            if (len(raw) > 4096 or set(value) != {'version', 'state_id', *self.value}
                    or value['version'] != 1 or value['state_id'] != store.state_id
                    or value['status'] not in STATES
                    or type(value['revision']) is not int or not 1 <= value['revision'] <= 9007199254740990
                    or value['conversation_id'] is not None and not store._identity(value['conversation_id'])
                    or value['status'] == 'active' and value['conversation_id'] is None):
                raise ValueError
            self.value = {k: value[k] for k in self.value}
        except (ValueError, TypeError, KeyError, OSError):
            raise Failure('onboarding_state', 'Getting-started state is invalid; no setup was restarted.') from None

    def read(self):
        return dict(self.value)

    def offer(self):
        # Called only by explicit real desktop initialization, never reopen.
        if self.value['status'] == 'not_offered':
            self._save(dict(status='available', conversation_id=None, revision=1))

    def guidance(self, conversation_id):
        return GUIDE if self.value['status'] == 'active' and self.value['conversation_id'] == conversation_id else ''

    def change(self, action, conversation_id, expected_revision):
        if self.store.active_turn_id() is not None or self.store.db.in_transaction:
            raise Failure('busy', 'Finish the reply before changing getting-started guidance.')
        if type(expected_revision) is not int or expected_revision != self.value['revision']:
            raise Failure('settings_conflict', 'Getting-started state changed. Refresh before continuing.')
        status = self.value['status']
        if action == 'start' and status in ('available', 'not_offered'):
            conversation = self.store.conversation(conversation_id)
            if not conversation.history_enabled or self.store.imported_conversation(conversation_id):
                raise Failure('onboarding_state', 'Use a new conversation with tools for the guided introduction.')
            updated = dict(status='active', conversation_id=conversation_id)
        elif action == 'complete' and status == 'active':
            updated = dict(status='completed', conversation_id=self.value['conversation_id'])
        elif action == 'skip' and status in ('available', 'active'):
            updated = dict(status='skipped', conversation_id=self.value['conversation_id'])
        else:
            raise Failure('onboarding_state', 'Refresh getting-started guidance before changing it.')
        self._save(dict(updated, revision=expected_revision + 1))
        return self.read()

    def _save(self, updated):
        def publish():
            self.value = updated
        try:
            atomic_write(self.path, json.dumps(dict(version=1, state_id=self.store.state_id, **updated)).encode(),
                         prefix='.onboarding-', published=publish)
        except OSError:
            raise Failure('onboarding_state', 'Save could not be confirmed. Refresh to inspect the saved state.') from None
