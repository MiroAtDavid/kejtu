"""Pinned local text embeddings in an owned cancellable child; never answer IO."""
import asyncio
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, build_opener, ProxyHandler

from ...core.recall import MODEL, MODEL_DIGEST, DIMENSIONS, CONTEXT_TOKENS, EmbeddingNotReady, vector_bytes
from ...core.records import Failure
from ._transport import NoRedirect

KEEP_ALIVE = '10m'
WARMUP_SECONDS = 120
REQUEST_SECONDS = 3


class LocalEmbedding:
    def __init__(self):
        self.needs_warmup = True
        self.retry_at = 0.

    async def warmup(self):
        """Idle owner only: cold initialization must not consume a chat budget.

        Start once, or after an actual request discovers eviction. No heartbeat
        keeps an unused model resident indefinitely. Foreground admission/close
        cancels and joins this child through the existing maintenance owner.
        """
        if not self.needs_warmup or time.monotonic() < self.retry_at:
            return
        try:
            await self._run(['title: none | text: Embedding readiness.'],
                            deadline=time.monotonic()+WARMUP_SECONDS, warmup=True)
        except (ValueError, OSError, TimeoutError):
            self.retry_at = time.monotonic()+60
            return
        self.needs_warmup = False
        self.retry_at = 0.

    async def embed(self, texts, *, deadline):
        try:
            values = await self._run(texts,
                deadline=min(deadline,time.monotonic()+REQUEST_SECONDS), warmup=False)
        except (ValueError, OSError, TimeoutError):
            self.needs_warmup = True
            raise
        self.needs_warmup = False
        self.retry_at = 0.
        return values

    async def _run(self, texts, *, deadline, warmup):
        # Do not inherit remote answer endpoints, credentials or ambient proxies.
        # The caller owns this subprocess and joins it even on cancellation.
        process = None
        try:
            environment = {'PYTHONPATH': str(Path(__file__).resolve().parents[3]),
                           'PYTHONDONTWRITEBYTECODE': '1'}
            if sys.platform == 'win32':
                from ._windows_transport_deadline import system_environment
                try:
                    environment.update(system_environment())
                except Failure:
                    # Keep the embedding owner's unavailable/backoff contract;
                    # never substitute an ambient or guessed Windows directory.
                    raise ValueError('Local embedding unavailable') from None
            async with asyncio.timeout_at(deadline):
                process = await asyncio.create_subprocess_exec(sys.executable, '-B', '-S', '-P', '-m',
                    'kejtu_next.adapters.models.embedding', stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                    env=environment)
                payload = json.dumps({'texts':texts,'warmup':warmup}).encode()
                if len(payload)>131072:
                    raise ValueError('Embedding request limit')
                process.stdin.write(payload)
                await process.stdin.drain()
                process.stdin.close()
                body = bytearray()
                while chunk := await process.stdout.read(65536):
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        raise ValueError('Embedding response limit')
                code = await process.wait()
                if code == 2:
                    raise EmbeddingNotReady('Local embedding is not resident')
                if code:
                    raise ValueError('Local embedding unavailable')
                vectors = json.loads(body)
                if not isinstance(vectors,list) or len(vectors) != len(texts):
                    raise ValueError('Embedding count mismatch')
                for vector in vectors:
                    vector_bytes(vector)
                return vectors
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()


def worker():
    payload = json.loads(sys.stdin.buffer.read(131073))
    if not isinstance(payload,dict) or set(payload)!={'texts','warmup'} or type(payload['warmup']) is not bool:
        raise ValueError('Invalid embedding mode')
    texts,warmup=payload['texts'],payload['warmup']
    if (not isinstance(texts, list) or not 1 <= len(texts) <= 16
            or any(not isinstance(t,str) or len(t.encode()) > 16384 for t in texts)):
        raise ValueError('Embedding request limit')
    opener = build_opener(ProxyHandler({}), NoRedirect())
    def request(path, value=None):
        data = None if value is None else json.dumps(value).encode()
        with opener.open(Request('http://127.0.0.1:11434'+path, data=data,
                headers={'Content-Type':'application/json'}), timeout=110 if warmup else 2.5) as response:
            body = response.read(2*1024*1024+1)
            if len(body) > 2*1024*1024:
                raise ValueError('Embedding response limit')
            return json.loads(body)
    tags = request('/api/tags').get('models', [])
    matching = [m.get('digest') for m in tags if m.get('name') == MODEL]
    if matching != [MODEL_DIGEST]:
        raise ValueError('Pinned embedding model unavailable or ambiguous')
    if not warmup:
        # A cold /api/embed may spend tens of seconds initializing CUDA. Ask
        # only about residence; no model load or silent slow retry in a turn.
        loaded = request('/api/ps').get('models', [])
        if not any(m.get('name') == MODEL and m.get('digest') == MODEL_DIGEST for m in loaded):
            raise EmbeddingNotReady('Local embedding is not resident')
    result = request('/api/embed', {'model':MODEL, 'input':texts, 'truncate':False,
        'dimensions':DIMENSIONS, 'keep_alive':KEEP_ALIVE,
        'options':{'num_ctx':CONTEXT_TOKENS}})['embeddings']
    if not isinstance(result,list) or len(result) != len(texts):
        raise ValueError('Embedding count mismatch')
    for vector in result:
        vector_bytes(vector)
    sys.stdout.write(json.dumps(result))


if __name__ == '__main__':
    try:
        worker()
    except EmbeddingNotReady:
        sys.exit(2)
    except Exception:
        sys.exit(1)  # No source text, endpoint body or credentials in diagnostics.
