"""Platform desktop keyrings; credentials never enter application files."""
import asyncio
import sys

from ...core.records import Failure
from ...core.credentials import validate_key
from ...core.providers import provider


_DEFAULT_SPAWN = asyncio.create_subprocess_exec


class DesktopKeyring:
    def __init__(self, provider_id='deepseek', *, spec=None, spawn=_DEFAULT_SPAWN):
        self.spawn = spawn
        self.spec = spec if spec is not None else provider(provider_id)
        if not (self.spec.requires_key or getattr(self.spec, 'optional_key', False)):
            raise ValueError('This provider does not use a key')
        self.attributes = ('application', 'dev.kejtu.next.development',
                           'provider', self.spec.id, 'endpoint', self.spec.endpoint)
        self._windows = None
        # An explicit subprocess factory retains the legacy Secret Service
        # injection contract, including isolated controls on Windows hosts.
        if sys.platform == 'win32' and spawn is _DEFAULT_SPAWN:
            from .windows_credentials import WindowsCredentials
            self._windows = WindowsCredentials(self.attributes)

    def for_provider(self, provider_id):
        return self if provider_id == self.spec.id else DesktopKeyring(provider_id, spawn=self.spawn)

    async def _run(self, action, key=None):
        process = None
        try:
            async with asyncio.timeout(20):
                args = ('store', f'--label=Kejtu — {self.spec.label} API key') if action == 'store' else (action,)
                process = await self.spawn('/usr/bin/secret-tool', *args, *self.attributes,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE)
                out, error = await process.communicate(key.encode('ascii') if key is not None else b'')
                # Never include command output or raw exceptions in diagnostics.
                if len(out) > 4096 or len(error) > 4096:
                    raise ValueError
                if action == 'lookup' and process.returncode == 1 and not out and not error:
                    return None
                if process.returncode:
                    raise ValueError
                return out.decode('ascii') if action == 'lookup' else None
        except (OSError, ValueError, TimeoutError):
            raise Failure('credential_store',
                'The desktop keyring is unavailable or locked. Unlock it and try again; the key was not saved to a file.') from None
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()

    async def read(self):
        if self._windows is not None:
            return self._windows.read()
        value = await self._run('lookup')
        return validate_key(value) if value is not None else None

    async def save(self, key):
        if self._windows is not None:
            # These bounded local calls deliberately have no await/thread: a
            # cancelled task cannot leave a late write running after its result.
            # Save and verification complete together before cancellation yields.
            return self._windows.save(key)
        await self._run('store', validate_key(key))

    async def remove(self):
        if self._windows is not None:
            return self._windows.remove()
        # Lookup distinguishes an absent item from an inaccessible keyring.
        if await self.read() is not None:
            await self._run('clear')
            if await self.read() is not None:
                raise Failure('credential_store', 'The saved key could not be removed. Unlock the desktop keyring and try again.')
