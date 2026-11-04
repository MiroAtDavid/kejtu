"""Named immutable execution policies, independent of model configuration and IO."""
from enum import StrEnum
from dataclasses import dataclass
import math

MAX_FOREGROUND_OPERATIONS = 256
DEFAULT_FOREGROUND_SECONDS = 1800
# Background procedures use the same operation allowance, with a shorter run.
DEFAULT_BACKGROUND_SECONDS = 600
PROCEDURE_LIMIT_BOUNDS = (
    ('calls', 1, MAX_FOREGROUND_OPERATIONS + 1),
    ('operations', 0, MAX_FOREGROUND_OPERATIONS),
    ('seconds', 1, DEFAULT_BACKGROUND_SECONDS),
)


@dataclass(frozen=True)
class TurnLimits:
    """Saved foreground allowance; cannot grant tools absent from a conversation."""
    operations: int
    seconds: float

    def __post_init__(self):
        if (type(self.operations) is not int or not 0 <= self.operations <= MAX_FOREGROUND_OPERATIONS
                or type(self.seconds) not in (int, float) or not math.isfinite(self.seconds) or self.seconds <= 0):
            raise ValueError('Invalid saved turn limits.')

    @property
    def calls(self):
        return self.operations + 1


@dataclass(frozen=True)
class ExecutionLimits:
    """Explicit procedure ceilings consumed by the existing Ledger, not a profile."""
    calls: int
    operations: int
    seconds: int

    def __post_init__(self):
        for name, low, high in PROCEDURE_LIMIT_BOUNDS:
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError('Procedure execution limits must be bounded whole numbers.')


class HistoryPolicy(StrEnum):
    STANDARD = 'history_standard_v1'
    R007 = 'r007_development_v1'

    @property
    def operations(self) -> int:
        return 2 if self is HistoryPolicy.R007 else 16

    @property
    def calls(self) -> int:
        # Upper bound for one operation per reply plus a final answer. Batches
        # can complete the same operation allowance with fewer model calls.
        return self.operations + 1


def select_history_policy(value, *, enabled: bool) -> HistoryPolicy | None:
    """Default only on creation; persisted history grants must name their policy."""
    if type(enabled) is not bool:
        raise ValueError('history grant must be boolean')
    if not enabled:
        if value is not None:
            raise ValueError('execution policy requires a history grant')
        return None
    return HistoryPolicy.STANDARD if value is None else HistoryPolicy(value)
