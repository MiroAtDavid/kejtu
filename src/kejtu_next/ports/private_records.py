"""Opaque private records; schema and credential policy remain with the caller."""
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Callable, Protocol


class PrivateRecord(Protocol):
    path: Path

    def locked(self) -> AbstractContextManager:
        """Acquire immediately, retaining ownership until context exit."""
        ...

    def read(self) -> bytes | None:
        """Bounded bytes, or absence only during acknowledged new initialization."""
        ...

    def replace(self, raw: bytes, *, published: Callable[[], None]) -> None:
        """Call published after rename; acknowledgement can still fail later."""
        ...
