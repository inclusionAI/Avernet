"""Bounded retry policy for one best-effort manager projection."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

_JITTER_FRACTION = 0.1


@dataclass(frozen=True)
class SyncRunPolicy:
    """Immutable limits shared by every attempt in one ``sync_once`` run."""

    max_attempts: int = 3
    budget_seconds: float = 15.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.budget_seconds <= 0:
            raise ValueError("budget_seconds must be positive")


def default_jitter(base_seconds: float) -> float:
    """Return additive jitter in the range ``base`` through ``base * 1.1``."""

    return base_seconds * (1.0 + random.random() * _JITTER_FRACTION)


def backoff_delay(
    *,
    retry_index: int,
    remaining_seconds: float,
    jitter: Callable[[float], float],
) -> float | None:
    """Return the bounded 1s/2s/... delay before a retry.

    ``retry_index`` is one after the first failed attempt, two after the
    second, and so on. ``None`` means the shared run deadline has expired.
    """

    if remaining_seconds <= 0:
        return None
    base = float(2 ** (retry_index - 1))
    return max(0.0, min(float(jitter(base)), remaining_seconds))
