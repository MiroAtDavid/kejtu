"""Private opaque credential records, independent of models/OAuth policy."""
from contextlib import contextmanager
import os
from pathlib import Path

from .files import private_directory, open_private_regular, atomic_write
from ...core.records import Failure


class PrivateRecordFile:
    def __init__(self, directory, *, maximum=524288):
        self.directory = Path(directory)
        self.path = self.directory / 'chatgpt-accounts.json'
        self.maximum = maximum
        self._owned = False
        self._new = False

    @contextmanager
    def locked(self):
        if self._owned:
            raise Failure('busy', 'ChatGPT connection storage is already being updated.')
        try:
            with private_directory(self.directory) as created:
                with open_private_regular(self.directory / '.lock', lock=True) as owner:
                    if os.name != 'nt':
                        import fcntl
                        fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._new, self._owned = created, True
                    try:
                        yield
                    finally:
                        self._owned = False
        except OSError as error:
            if isinstance(error, BlockingIOError) or getattr(error, 'winerror', None) in (32, 33):
                raise Failure('busy', 'Another Kejtu process is updating this ChatGPT connection. Try again shortly.') from None
            raise Failure('chatgpt_storage', 'ChatGPT credential storage could not be opened privately. No credentials were replaced.') from None

    def _require_owner(self):
        if not self._owned:
            raise Failure('chatgpt_storage', 'ChatGPT storage requires its current exclusive owner.')

    def read(self):
        self._require_owner()
        try:
            with open_private_regular(self.path) as source:
                raw = source.read(self.maximum + 1)
        except FileNotFoundError:
            if self._new:
                return None
            raise Failure('chatgpt_storage', 'Saved ChatGPT connections are missing. Existing private storage was not replaced.') from None
        except OSError:
            raise Failure('chatgpt_storage', 'Saved ChatGPT connections could not be read privately.') from None
        if len(raw) > self.maximum:
            raise Failure('chatgpt_storage', 'Saved ChatGPT connections exceed their storage limit.')
        return raw

    def replace(self, raw, *, published):
        self._require_owner()
        if not isinstance(raw, bytes) or len(raw) > self.maximum:
            raise Failure('chatgpt_storage', 'Saved ChatGPT connections exceed their storage limit.')
        def visible():
            self._new = False
            published()
        try:
            atomic_write(self.path, raw, prefix='.accounts-', published=visible)
            with open_private_regular(self.path) as source:
                if source.read(self.maximum + 1) != raw:
                    raise OSError('Private record readback changed.')
        except OSError:
            raise Failure('chatgpt_storage', 'ChatGPT storage could not be confirmed. Reopen to inspect saved state; no operation was retried.') from None
