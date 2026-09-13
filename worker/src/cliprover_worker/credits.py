"""Credit charge outcomes and what processing a video costs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ChargeOutcome = Literal["charged", "already-charged", "insufficient"]


@dataclass(frozen=True)
class ChargeResult:
    outcome: ChargeOutcome
    #: The balance after the attempt: unchanged unless the outcome is "charged".
    available: int


def processing_cost(duration_ms: int, credits_per_minute: int) -> int:
    """Credits per *started* minute: a 61-second video costs two minutes.

    Integer arithmetic on purpose — a float ceil of 60_000 / 60_000 must never
    round up to 2.
    """
    if duration_ms <= 0 or credits_per_minute <= 0:
        return 0
    minutes = -(-duration_ms // 60_000)
    return minutes * credits_per_minute
