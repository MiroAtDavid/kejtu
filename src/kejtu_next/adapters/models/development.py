"""Explicit deterministic development provider; never contacts a network service."""
import asyncio
import json

from ...core.context import text_bound
from ...core.records import Failure, Reply, ToolCall


class DevelopmentModel:
    def count_request_tokens(self, profile, messages, schema):
        """This fake's own tokenizer, exact by definition: one token per four bytes
        of the conservative request bound, the rule its replies already use for
        output tokens. Real adapters keep the byte bound or provider measurements;
        no real model's tokenizer is inferred from this.
        """
        size = (sum(text_bound(message, provider=profile.provider) for message in messages)
                + len(json.dumps(schema, ensure_ascii=False).encode()) + 32)
        return -(-size // 4)

    async def complete(self, request):
        user = next((message.content for message in reversed(request.messages) if message.role == 'user'), '')
        if user.startswith('[[delay]]'):
            await asyncio.sleep(3.0)
        if user.startswith('[[note]]') and not any(message.role == 'tool' for message in request.messages):
            return Reply('', 'tool_calls', output_tokens=12, tool_calls=(ToolCall('development-note', 'write_note',
                json.dumps({'text': user[len('[[note]]'):].strip()})),))
        if user.startswith('[[note]]'):
            if '[[after-commit-delay]]' in user:
                await asyncio.sleep(3.0)
            return Reply('Synthetic note operation finished; inspect its durable outcome.', 'stop', output_tokens=12)
        if user.startswith('[[failure]]'):
            raise Failure('development_provider_failure', 'The selected development provider failed as requested.')
        if user.startswith('[[partial]]'):
            return Reply('Deterministic partial response.', 'length', input_tokens=None, output_tokens=3)
        answer = 'Development reply: ' + user
        return Reply(answer, 'stop', input_tokens=None, output_tokens=max(1, len(answer.encode('utf-8')) // 4))
