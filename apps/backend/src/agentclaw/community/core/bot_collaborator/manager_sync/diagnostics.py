"""Finite, payload-free diagnostics for best-effort projection sync."""

from __future__ import annotations

import logging
import re
import threading
from collections import Counter
from enum import StrEnum
from typing import Mapping

from agentclaw.community.kernel.bot_manager_sync import (
    SubmissionResult,
    SyncKey,
    SyncOutcome,
    SyncReason,
    SyncWorkKind,
)


class SyncEvent(StrEnum):
    """Closed event vocabulary emitted by the core sync path."""

    READ_LATE = "read_late"
    READ_FAILED = "read_failed"
    PROJECTION_FAILED = "projection_failed"
    DEADLINE_EXPIRED = "deadline_expired"
    CANCELLED = "cancelled"
    SEND_RETRYABLE = "send_retryable"
    SEND_BOT_MISSING_NOOP = "send_bot_missing_noop"
    SEND_CONFIRMED = "send_confirmed"
    SEND_ACKNOWLEDGED_EMPTY = "send_acknowledged_empty"
    SEND_DRIFT = "send_drift"
    SEND_REJECTED = "send_rejected"
    RETRIES_EXHAUSTED = "retries_exhausted"
    SUBMIT_ADMITTED = "submit_admitted"
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


_ERROR_CODES = frozenset(
    {
        "principal_mint_failed",
        "timeout",
        "connect_error",
        "connect_timeout",
        "pool_timeout",
        "read_timeout",
        "write_timeout",
        "transport_error",
        "malformed_envelope",
        "unclassified_delivery",
    }
)
_HTTP_CODE = re.compile(r"http_[1-5][0-9]{2}")


def _classify_error(code: str) -> str:
    """Never pass arbitrary error text through to the log."""
    if not code:
        return "none"
    if code in _ERROR_CODES or _HTTP_CODE.fullmatch(code):
        return code
    return "unclassified_delivery"


class SyncDiagnostics:
    """Log finite events and retain bounded-label counters for metrics/tests."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(
            "agentclaw.community.manager_sync.diagnostics"
        )
        self._counted: Counter[tuple[str, str]] = Counter()
        self._counted_by_kind: Counter[tuple[str, str, str]] = Counter()
        self._counted_lock = threading.Lock()

    @property
    def counted(self) -> Mapping[tuple[str, str], int]:
        with self._counted_lock:
            return dict(self._counted)

    @property
    def counted_by_kind(self) -> Mapping[tuple[str, str, str], int]:
        with self._counted_lock:
            return dict(self._counted_by_kind)

    def record(
        self,
        event: str,
        *,
        key: SyncKey,
        outcome: str,
        attempt: int = 0,
        counts: Mapping[str, int] | None = None,
        reason: SyncReason | None = None,
        work_kind: SyncWorkKind = SyncWorkKind.MANAGERS,
        request_id: str = "",
        elapsed_ms: int = 0,
        error_code: str = "",
    ) -> None:
        event_value = SyncEvent(event).value
        try:
            outcome_value = SyncOutcome(outcome).value
        except ValueError:
            try:
                outcome_value = SyncRejection(outcome).value
            except ValueError:
                outcome_value = SubmissionResult(outcome).value
        reason_value = SyncReason(reason).value if reason is not None else None
        kind_value = SyncWorkKind(work_kind).value
        code_value = _classify_error(error_code)
        with self._counted_lock:
            self._counted[(event_value, outcome_value)] += 1
            self._counted_by_kind[(kind_value, event_value, outcome_value)] += 1
        extras = " ".join(
            f"{name}={int(value)}" for name, value in (counts or {}).items()
        )
        self._logger.log(
            logging.WARNING if event_value in _WARN_EVENTS else logging.INFO,
            "manager_sync event=%s outcome=%s reason=%s attempt=%d "
            "tenant=%s env=%s bot_pk=%d kind=%s elapsed_ms=%d request_id=%s error_code=%s%s",
            event_value,
            outcome_value,
            reason_value or "none",
            attempt,
            key.avernet_tenant,
            key.env,
            key.bot_pk,
            kind_value,
            elapsed_ms,
            request_id,
            code_value,
            f" {extras}" if extras else "",
        )
