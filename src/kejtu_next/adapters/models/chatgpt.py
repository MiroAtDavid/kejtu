"""ChatGPT plan inference uses Kejtu's existing Responses/operation owners."""
import asyncio
from .openai import OpenAIResponses


class ChatGPTModel:
    supports_streaming = True

    def __init__(self, accounts):
        self.accounts = accounts

    async def stream(self, request, progress):
        # Refresh shares the admitted request deadline. No stale credential or
        # different billing path is tried when authentication is unavailable.
        async with asyncio.timeout_at(request.deadline):
            token = await self.accounts.access_token()
            return await OpenAIResponses('chatgpt', token).stream(request, progress)

    async def complete(self, request):
        return await self.stream(request, lambda event: None)
