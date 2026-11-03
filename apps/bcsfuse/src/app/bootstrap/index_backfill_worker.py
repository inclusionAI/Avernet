"""Retry startup index reconciliation while the process remains available."""

from __future__ import annotations

import logging
from collections.abc import Callable
from threading import Event

from app.bootstrap.index_backfill_status import IndexBackfillStatus

logger = logging.getLogger(__name__)


class IndexBackfillWorker:
    """Keep readiness explicit until a failed backfill actually completes."""

    def __init__(
        self,
        *,
        target: Callable[[], object],
        status: IndexBackfillStatus,
        stop_event: Event,
        retry_interval_seconds: float,
    ) -> None:
        if retry_interval_seconds <= 0:
            raise ValueError("retry_interval_seconds must be positive")
        self._target = target
        self._status = status
        self._stop_event = stop_event
        self._retry_interval_seconds = retry_interval_seconds

    def run(self) -> None:
        # A started worker owns one explicit attempt.  Shutdown suppresses
        # later retries rather than silently discarding the first status update.
        while True:
            self._status.mark_started()
            try:
                self._target()
            except Exception as error:
                self._status.mark_failed(type(error).__name__)
                logger.warning(
                    "[IndexBackfill] reconciliation failed: %s; retrying",
                    type(error).__name__,
                )
                if self._stop_event.wait(self._retry_interval_seconds):
                    return
                continue
            self._status.mark_completed()
            logger.info("[IndexBackfill] reconciliation completed")
            return


__all__ = ["IndexBackfillWorker"]
