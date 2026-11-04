"""Explicit model settings actions; opening Settings never contacts a provider."""
import asyncio
from dataclasses import asdict

from ..core.records import Failure, Profile
from ..core.providers import PROVIDERS


class ModelControls:
    def __init__(self, application, saved, session):
        self.app, self.saved, self.session = application, saved, session
        self.lock = asyncio.Lock()
        self.chatgpt = session.chatgpt
        if self.chatgpt is None:
            raise Failure('model_settings', 'ChatGPT account storage was not composed for this session.')

    def status(self):
        return {'revision': self.saved.revision, 'active': self.saved.active,
                'profiles': {name: asdict(profile) for name, profile in self.saved.profiles.items()},
                'key_statuses': dict(self.session.key_statuses),
                'providers': [spec.settings_descriptor() for spec in PROVIDERS.values()]}

    async def dispatch(self, operation, args):
        if operation == 'models.read':
            return self.status()
        if operation == 'models.chatgpt.read':
            return self.chatgpt.status()
        async with self.lock:
            await self.app.titles.cancel()
            # A key/profile change never changes an admitted foreground or
            # scheduled operation midway through its work.
            if self.app.tasks or self.app.store.active_turn_id() is not None or self.app.background.active is not None:
                raise Failure('busy', 'Wait for current work to finish before changing model settings.')
            if operation == 'models.save':
                try:
                    profile = Profile(**args['profile'])
                except (ValueError, TypeError):
                    raise Failure('model_settings', 'Check the model, address, context, reply reserve and thinking settings.') from None
                self.saved.save(profile, args['active'], args['expected_revision'])
                self.app.profile = self.saved.profile
                self.app.provider_mode = self.saved.active
                self.app.service.working_context.clear()
            elif operation == 'models.key':
                if args['action'] == 'save':
                    await self.session.save_key(args.get('api_key'), args['provider'])
                elif args['action'] == 'remove':
                    await self.session.remove_key(args['provider'])
                else:
                    await self.session.restore(args['provider'])
            elif operation == 'models.check':
                try:
                    profile = Profile(**args['profile'])
                except (ValueError, TypeError):
                    raise Failure('model_settings', 'Check the model profile before testing its connection.') from None
                return await self.session.check(profile)
            elif operation == 'models.chatgpt':
                action = args['action']
                if action == 'start':
                    return await self.chatgpt.begin(args.get('account_id'))
                if action == 'finish':
                    return await self.chatgpt.finish()
                if action == 'cancel':
                    return await self.chatgpt.cancel()
                if action == 'select':
                    return await self.chatgpt.select(args['account_id'])
                if action == 'sign_out':
                    return await self.chatgpt.sign_out(args['account_id'])
                if action == 'welcome_seen':
                    return self.chatgpt.welcome_seen()
            return self.status()
