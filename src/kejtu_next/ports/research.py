"""Assignment lifecycle and source-backed result persistence."""
from typing import Protocol


class ResearchPort(Protocol):
    def epoch(self) -> int: ...
    def execute(self, args, *, conversation_id, turn_id, operation_key, ledger,
                maximum, budget, read_refs=frozenset()) -> dict: ...
