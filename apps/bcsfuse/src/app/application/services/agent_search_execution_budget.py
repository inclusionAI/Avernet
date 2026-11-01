"""Hard execution limits for search and recommendation stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_EMBEDDING_TIMEOUT_SECONDS = 1.500
DEFAULT_RETRIEVAL_TIMEOUT_SECONDS = 3.000
DEFAULT_LOCAL_SNAPSHOT_TIMEOUT_SECONDS = 0.500
DEFAULT_TOTAL_TIMEOUT_SECONDS = 5.000


@dataclass(frozen=True, slots=True)
class AgentSearchExecutionBudget:
    """Bounded wall-clock budgets selected by the application composition.

    Stage limits protect the event loop and queue from a downstream hang. The
    total budget bounds the admitted request even if all optional stage
    limits are disabled or local fallback work is unexpectedly slow.
    """

    embedding_timeout_seconds: float = 1.500
    retrieval_timeout_seconds: float = 3.000
    local_snapshot_timeout_seconds: float = 0.500
    total_timeout_seconds: float = 5.000

    def __post_init__(self) -> None:
        for name in (
            "embedding_timeout_seconds",
            "retrieval_timeout_seconds",
            "local_snapshot_timeout_seconds",
            "total_timeout_seconds",
        ):
            value = getattr(self, name)
            if value <= 0:
                raise ValueError(f"{name} must be positive")

    @classmethod
    def from_context(cls, context: Any) -> "AgentSearchExecutionBudget":
        config = getattr(context, "config", None)

        def number(key: str, default: float) -> float:
            method = getattr(config, "get_float", None)
            if method is None:
                return default
            return float(method(key, default))

        return cls(
            embedding_timeout_seconds=number(
                "search.execution.embedding_timeout_seconds",
                DEFAULT_EMBEDDING_TIMEOUT_SECONDS,
            ),
            retrieval_timeout_seconds=number(
                "search.execution.retrieval_timeout_seconds",
                DEFAULT_RETRIEVAL_TIMEOUT_SECONDS,
            ),
            local_snapshot_timeout_seconds=number(
                "search.execution.local_snapshot_timeout_seconds",
                DEFAULT_LOCAL_SNAPSHOT_TIMEOUT_SECONDS,
            ),
            total_timeout_seconds=number(
                "search.execution.total_timeout_seconds",
                DEFAULT_TOTAL_TIMEOUT_SECONDS,
            ),
        )


__all__ = [
    "AgentSearchExecutionBudget",
    "DEFAULT_EMBEDDING_TIMEOUT_SECONDS",
    "DEFAULT_RETRIEVAL_TIMEOUT_SECONDS",
    "DEFAULT_LOCAL_SNAPSHOT_TIMEOUT_SECONDS",
    "DEFAULT_TOTAL_TIMEOUT_SECONDS",
]
