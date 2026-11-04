"""Local retrieval embeddings, independent of answer generation/profile policy."""
from typing import Protocol

class EmbeddingPort(Protocol):
    async def embed(self, texts: list[str], *, deadline: float) -> list[list[float]]: ...
