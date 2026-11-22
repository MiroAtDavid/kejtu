"""Connection credentials live in the desktop keyring; settings keep only references.

A connection's ``env`` or ``headers`` values never enter ``mcp-settings.json``
once it is imported. Each value is saved as its own keyring item under a random
identity, and the saved row lists ``{field: identity}`` under ``secrets``. The
values are read back only by the launch owner, right before a worker starts.

A reference is a random identity, never a name: a replaced connection keeps its
old values until the replacement is saved, so a credential is never offered to
an address it was not saved for. The keyring itself is supplied by the caller
(one item per identity), so this module needs no other adapter family.
"""
import uuid

from ...core.records import Failure

MAX_VALUE_BYTES = 8192


def credential_refs(row):
    """All saved credentials, including browser sign-in, share removal ownership."""
    return [*row.get('secrets', {}).values(),
            *([row['oauth']['secret']] if row.get('oauth') else [])]


def credential_value(value):
    """The rule for one saved env or header value: bounded, single-line text."""
    if (not isinstance(value, str) or not value or any(ord(c) < 32 for c in value)
            or len(value.encode('utf-8', 'surrogatepass')) > MAX_VALUE_BYTES):
        raise Failure('mcp_credentials', 'A credential value must be 1–8192 bytes of single-line text; nothing was saved.')
    return value


def credential_fields(row):
    """The mapping that carries a connection's credentials: env for local programs, headers for remote ones."""
    return 'env' if row['transport'] == 'stdio' else 'headers'


class ConnectionSecrets:
    def __init__(self, keyring):
        """``keyring(identity, label)`` returns that item's store: async read, save and remove."""
        self.keyring = keyring

    async def store(self, name, values):
        """Save each value under a new identity. Returns ``{field: identity}``.

        If a save is refused or interrupted, every identity that was attempted is
        deleted again, including one whose save may have written before it failed.
        That cleanup is best effort: a keyring that has stopped working can keep
        what it already accepted.
        """
        for value in values.values():
            credential_value(value)
        references = {}
        try:
            for field, value in values.items():
                identity = uuid.uuid4().hex
                references[field] = identity  # before the save: it can write and then fail
                await self.keyring(identity, f'connection {name} · {field}').save(value)
        except BaseException:
            await self.forget(references.values(), quiet=True)
            raise
        return references

    async def usable(self, identity):
        """Raise unless the keyring can be used now. Reads one item and discards it."""
        await self.keyring(identity, 'connection').read()

    async def forget(self, identities, *, quiet=False):
        """Delete saved values; every one is attempted. An absent item is already forgotten.

        The first failure is raised after all were tried, unless ``quiet``, which is
        for cleaning up after another failure that is the one reported.
        """
        failed = None
        for identity in list(identities):
            try:
                await self.keyring(identity, 'connection').remove()
            except Failure as error:
                failed = failed or error
        if failed and not quiet:
            raise failed

    async def resolve(self, row):
        """The row as its worker needs it: values in place, references gone. Nothing is cached."""
        fields = credential_fields(row)
        values = dict(row[fields])
        for field, identity in row['secrets'].items():
            value = await self.keyring(identity, 'connection').read()
            if value is None:
                raise Failure('mcp_credentials', 'This connection’s saved key is not in this computer’s keyring. '
                              'Open its package account details or re-import the connection to reconnect; nothing was started.')
            values[field] = value
        resolved = {k: v for k, v in row.items() if k != 'secrets'}
        resolved[fields] = values
        return resolved
