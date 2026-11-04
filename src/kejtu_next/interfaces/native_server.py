"""Explicit candidate native backend process."""
from __future__ import annotations

import argparse
import asyncio
import json
import hashlib
from pathlib import Path
import secrets
import signal

from .native_api import NativeServer


def build_fingerprint():
    root = Path(__file__).resolve().parents[3]
    digest = hashlib.sha256()
    # The shipped guide is application context through skill reads; include it
    # so a desktop/backend mismatch cannot silently serve another manual version.
    for path in (sorted((root / 'src').rglob('*.py')) +
                 sorted((root / 'src/kejtu_next/help').glob('*.md')) +
                 [root / 'contracts/native-v1.json']):
        digest.update(path.relative_to(root).as_posix().encode() + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()


async def serve(application, *, lifecycle_event_handle=None) -> int:
    application.process_instance_id = secrets.token_hex(16)
    server = NativeServer(application)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ('SIGINT', 'SIGTERM'):
        if hasattr(signal, name):
            try:
                loop.add_signal_handler(getattr(signal, name), stop.set)
            except NotImplementedError:
                pass
    lifetime = None
    try:
        if lifecycle_event_handle is not None:
            from .native_lifetime import LifecycleEvent
            lifetime = LifecycleEvent(lifecycle_event_handle, loop, stop)
        port = await server.start()
        application.start_maintenance()
        # This pipe is consumed privately by the owning Rust process. The token
        # is never forwarded to the WebView, argv, environment, URL or logs.
        print(json.dumps({'kind': 'ready', 'port': port, 'session_token': server.token,
                          'process_instance_id': application.process_instance_id,
                          'state_id': application.store.state_id}, separators=(',', ':')), flush=True)
        await stop.wait()
        if lifetime is not None and lifetime.error is not None:
            raise lifetime.error
        return 0
    finally:
        try:
            try:
                await server.close()
            finally:
                await application.close()
        finally:
            if lifetime is not None:
                lifetime.close()
