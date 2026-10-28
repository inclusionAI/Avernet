"""Finite, payload-free diagnostics for best-effort manager sync."""

from __future__ import annotations

import logging
from collections import Counter
from enum import StrEnum
from typing import Mapping

from agentclaw.community.kernel.bot_manager_sync import SyncKey, SyncOutcome


class SyncEvent(StrEnum):
    """Closed event vocabulary emitted by the core sync path."""

    READ_LATE = "read_late"
    READ_FAILED = "read_failed"
    PROJECTION_FAILED = "projection_failed"
    DEADLINE_EXPIRED = "deadline_expired"
    CANCELLED = "cancelled"
    SEND_RETRYABLE = "send_retryable"
    SEND_CONFIRMED = "send_confirmed"
    SEND_ACKNOWLEDGED_EMPTY = "send_acknowledged_empty"
    SEND_DRIFT = "send_drift"
    SEND_REJECTED = "send_rejected"
    RETRIES_EXHAUSTED = "retries_exhausted"
    SUBMIT_REJECTED = "submit_rejected"
    SPACE_EXPANSION_FAILED = "space_expansion_failed"
    SPACE_EXPANSION_TRUNCATED = "space_expansion_truncated"


class SyncRejection(StrEnum):
    """Closed admission-rejection vocabulary."""

    QUEUE_FULL = "queue_full"
    CLOSED = "closed"
    ENV_MISMATCH = "env_mismatch"


_WARN_EVENTS = frozenset(
    {
        SyncEvent.READ_FAILED,
        SyncEvent.DEADLINE_EXPIRED,
        SyncEvent.SEND_RETRYABLE,
        SyncEvent.SEND_REJECTED,
        SyncEvent.RETRIES_EXHAUSTED,
        SyncEvent.SUBMIT_REJECTED,
        SyncEvent.SPACE_EXPANSION_FAILED,
        SyncEvent.SPACE_EXPANSION_TRUNCATED,
    }
)


class SyncDiagnostics:
    """Log finite events and retain bounded-label counters for metrics/tests."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(
            "agentclaw.community.manager_sync.diagnostics"
        )
        self._counted: Counter[tuple[str, str]] = Counter()

    @property
    def counted(self) -> Mapping[tuple[str, str], int]:
        return dict(self._counted)

    def record(
        self,
        event: str,
        *,
        key: SyncKey,
        outcome: str,
        attempt: int = 0,
        counts: Mapping[str, int] | None = None,
    ) -> None:
        event_value = SyncEvent(event).value
        try:
            outcome_value = SyncOutcome(outcome).value
        except ValueError:
            outcome_value = SyncRejection(outcome).value
        self._counted[(event_value, outcome_value)] += 1
        extras = " ".join(
            f"{name}={int(value)}" for name, value in (counts or {}).items()
        )
        self._logger.log(
            logging.WARNING if event_value in _WARN_EVENTS else logging.INFO,
            "manager_sync event=%s outcome=%s attempt=%d tenant=%s env=%s bot_pk=%d%s",
            event_value,
            outcome_value,
            attempt,
            key.avernet_tenant,
            key.env,
            key.bot_pk,
            f" {extras}" if extras else "",
        )
