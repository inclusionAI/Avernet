"""SyncDiagnostics — aggregatable, payload-free observability for sync.

Rules fixed by the feature contract:

- ``event`` / ``outcome`` come only from the finite enums below /
  ``SyncOutcome`` (round outcomes) / ``SyncRejection`` (admission
  rejection reasons) — ``record`` validates both and raises ``ValueError``
  on anything else, so no dynamic string can slip in as a label and no
  cardinality explosion can reach the sink.
- No payloads, no credentials: the record addresses the ``SyncKey``
  dimension (tenant/env/bot_pk) and integer counts only; manager id
  labels never appear.
- Wiring: standard library ``logging`` with one machine-splittable
  ``manager_sync event=… outcome=…`` line per event — aggregatable by
  any existing log pipeline, **no new external dependency**. A local
  counter (``counts``) keeps exact tallies for tests and later metric
  exporters without changing this interface.

Infra failures log at WARNING (the "alert" channel available here);
everything else logs at INFO.
"""
from __future__ import annotations

import logging
from collections import Counter
from enum import StrEnum
from typing import Mapping

from agentclaw.community.kernel.bot_manager_sync import SyncKey, SyncOutcome


class SyncEvent(StrEnum):
    """The closed set of events the sync service may emit."""

    CLAIM_ACQUIRED = "claim_acquired"
    CLAIM_BUSY = "claim_busy"
    CLAIM_BLOCKED = "claim_blocked"
    CLAIM_FAILED = "claim_failed"
    READ_LATE = "read_late"
    READ_FAILED = "read_failed"
    PROJECTION_FAILED = "projection_failed"
    DEADLINE_EXPIRED = "deadline_expired"
    LEASE_RENEW_FAILED = "lease_renew_failed"
    BEGIN_LOST = "begin_lost"
    BEGIN_UNCERTAIN = "begin_uncertain"
    CANCELLED = "cancelled"
    SEND_CONFIRMED = "send_confirmed"
    SEND_ACKNOWLEDGED_EMPTY = "send_acknowledged_empty"
    SEND_DRIFT = "send_drift"
    SEND_UNKNOWN = "send_unknown"
    SEND_REJECTED = "send_rejected"
    SEND_CANCELLED = "send_cancelled"
    FINISH_FAILED = "finish_failed"
    FINISH_MISMATCH = "finish_mismatch"
    RETRIES_EXHAUSTED = "retries_exhausted"
    RELEASE_FAILED = "release_failed"
    HOLD_SET = "hold_set"
    HOLD_FAILED = "hold_failed"
    SUBMIT_REJECTED = "submit_rejected"
    SPACE_EXPANSION_TRUNCATED = "space_expansion_truncated"


class SyncRejection(StrEnum):
    """Finite admission-rejection reasons (`SUBMIT_REJECTED` taxonomy).

    The outcome vocabulary for submission-refusal records: a closed set
    so a rejection reason can never be a dynamic string label. Same
    validation discipline as ``SyncOutcome`` — ``record`` raises
    ``ValueError`` on anything outside this enum.
    """

    QUEUE_FULL = "queue_full"
    CLOSED = "closed"
    ENV_MISMATCH = "env_mismatch"


#: Events that describe infrastructure trouble — these log at WARNING.
_WARN_EVENTS = frozenset(
    {
        SyncEvent.CLAIM_FAILED,
        SyncEvent.READ_FAILED,
        SyncEvent.DEADLINE_EXPIRED,
        SyncEvent.LEASE_RENEW_FAILED,
        SyncEvent.BEGIN_UNCERTAIN,
        SyncEvent.SEND_UNKNOWN,
        SyncEvent.FINISH_FAILED,
        SyncEvent.RELEASE_FAILED,
        SyncEvent.HOLD_FAILED,
        SyncEvent.SUBMIT_REJECTED,
        SyncEvent.SPACE_EXPANSION_TRUNCATED,
    }
)

_Logger = logging.Logger


class SyncDiagnostics:
    """Aggregatable sync event sink (see module docstring)."""

    def __init__(self, *, logger: _Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(
            "agentclaw.community.manager_sync.diagnostics"
        )
        self._counted: Counter[tuple[str, str]] = Counter()

    @property
    def counted(self) -> Mapping[tuple[str, str], int]:
        """Exact (event, outcome) tallies — test/metricExporter hook."""
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
        """Record one event; validates the enums and logs one line.

        ``key`` contributes the tenant/env/bot_pk dimensions only;
        ``counts`` carries integers (managers sent, attempts used, …).
        Anything outside the finite enums raises ``ValueError`` — the
        service may only speak its own vocabulary.
        """
        event_value = SyncEvent(event).value
        try:
            outcome_value = SyncOutcome(outcome).value
        except ValueError:
            # The one other finite outcome vocabulary: admission
            # rejection reasons (submission / expansion truncation).
            # Still enum-validated — no dynamic label reaches the sink.
            outcome_value = SyncRejection(outcome).value
        extras = counts or {}
        self._counted[(event_value, outcome_value)] += 1
        line = " ".join(f"{name}={value}" for name, value in extras.items())
        level = logging.WARNING if event_value in _WARN_EVENTS else logging.INFO
        self._logger.log(
            level,
            "manager_sync event=%s outcome=%s attempt=%d tenant=%s env=%s"
            " bot_pk=%d%s",
            event_value,
            outcome_value,
            attempt,
            key.avernet_tenant,
            key.env,
            key.bot_pk,
            f" {line}" if line else "",
        )