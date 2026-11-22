"""Browser authorization primitives shared by model and tool connections.

Service policy, credentials and token refresh stay with their existing owners.
"""
import asyncio
import base64
import hashlib
import hmac
import time
from urllib.parse import parse_qs, urlsplit


def challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')


async def joined_io(function, *args):
    """Join bounded credential IO on cancellation; never leave a late write."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


async def callback_arguments(reader, pending):
    """Read one bounded loopback callback, bound to this attempt, host and path."""
    raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
    if len(raw) > 12288 or not pending or time.monotonic() >= pending['expires']:
        raise ValueError('Invalid or expired callback')
    lines = raw.decode('ascii').split('\r\n')
    method, target, version = lines[0].split(' ')
    fields = [line.split(':', 1) for line in lines[1:] if line]
    hosts = [value.strip() for key, value in fields if key.lower() == 'host']
    url, redirect = urlsplit(target), urlsplit(pending['redirect'])
    if (method != 'GET' or version not in ('HTTP/1.0', 'HTTP/1.1') or url.path != redirect.path
            or url.netloc or url.fragment or hosts != [redirect.netloc]):
        raise ValueError('Invalid callback target')
    args = parse_qs(url.query, keep_blank_values=True, max_num_fields=16)
    if any(len(v) != 1 for v in args.values()) or not hmac.compare_digest(args.get('state', [''])[0], pending['state']):
        raise ValueError('Invalid callback state')
    return args
