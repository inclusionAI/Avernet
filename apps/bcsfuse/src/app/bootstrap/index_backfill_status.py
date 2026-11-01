"""Thread-safe startup index backfill state."""

from __future__ import annotations

from threading import Lock


class IndexBackfillStatus:
    """Track whether a startup reconciliation/backfill owns readiness."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._running = False
        self._failed = False
        self._error_type: str | None = None

    def mark_started(self) -> None:
        with self._lock:
            self._running = True
            self._failed = False
            self._error_type = None

    def mark_completed(self) -> None:
        with self._lock:
            self._running = False
            self._failed = False
            self._error_type = None

    def mark_failed(self, error_type: str) -> None:
        if not error_type:
            raise ValueError("error_type is required")
        with self._lock:
            self._running = False
            self._failed = True
            self._error_type = error_type

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "running": self._running,
                "failed": self._failed,
                "error_type": self._error_type,
            }


__all__ = ["IndexBackfillStatus"]
