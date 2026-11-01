"""Rate-limited, content-free warnings for retrieval failure bursts."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable


class _RateLedger:
    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, *, interval_seconds: float, now: float) -> bool:
        with self._lock:
            previous = self._last.get(key, float("-inf"))
            if now - previous < interval_seconds:
                return False
            self._last[key] = now
            return True


_ledger = _RateLedger()


def warn_once_per_interval(
    logger: logging.Logger,
    key: str,
    message: str,
    *,
    interval_seconds: float = 5.0,
    clock: Callable[[], float] = time.monotonic,
    **fields: object,
) -> bool:
    """Emit a warning only once per process-local interval.

    Metrics remain the source of exact counts. This helper prevents a single
    model outage from generating tens of thousands of similar warnings.
    """
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if not logger.isEnabledFor(logging.WARNING):
        return False
    if not _ledger.allow(key, interval_seconds=interval_seconds, now=clock()):
        return False
    logger.warning(message, extra={"retrieval_warning_key": key, **fields})
    return True


__all__ = ["warn_once_per_interval"]
