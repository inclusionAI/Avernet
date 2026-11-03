"""Bounded non-blocking logging for latency-sensitive application processes.

The HTTP accept loop must not perform console I/O. This module moves formatting
and stream writes to one logging worker while putting a hard upper bound on
in-memory log backlog. If the producer wins a sustained race with a blocked
writer, diagnostics are dropped and counted instead of stalling request I/O.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Iterable
from typing import Any

from prometheus_client import Counter, Gauge

_LOG_METRICS: dict[str, Any] | None = None


def _get_log_metrics() -> dict[str, Any]:
    global _LOG_METRICS
    if _LOG_METRICS is not None:
        return _LOG_METRICS

    from prometheus_client import REGISTRY

    def existing(name: str) -> Any:
        collector = getattr(REGISTRY, "_names_to_collectors", {}).get(name)
        return collector

    dropped = existing("bcsfuse_logging_dropped_total")
    write_errors = existing("bcsfuse_logging_write_errors_total")
    depth = existing("bcsfuse_logging_queue_depth")
    oversize = existing("bcsfuse_logging_queue_capacity")
    _LOG_METRICS = {
        "dropped": dropped
        or Counter(
            "bcsfuse_logging_dropped_total",
            "Log records dropped because the non-blocking queue was full",
        ),
        "errors": write_errors
        or Counter(
            "bcsfuse_logging_write_errors_total",
            "Background log stream write failures",
        ),
        "depth": depth
        or Gauge(
            "bcsfuse_logging_queue_depth",
            "Records waiting in the non-blocking logging queue",
        ),
        "capacity": oversize
        or Gauge(
            "bcsfuse_logging_queue_capacity",
            "Configured capacity of the non-blocking logging queue",
        ),
    }
    return _LOG_METRICS


class NonblockingLoggingHandler(logging.Handler):
    """Queue records and emit them on a standalone process-wide writer."""

    def __init__(
        self,
        *,
        handlers: Iterable[logging.Handler],
        queue_size: int,
        poll_interval_seconds: float = 0.1,
        close_timeout_seconds: float = 5.0,
    ) -> None:
        super().__init__(level=logging.NOTSET)
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if close_timeout_seconds <= 0:
            raise ValueError("close_timeout_seconds must be positive")
        self.handlers = list(handlers)
        if not self.handlers:
            raise ValueError("at least one destination handler is required")
        self.queue_size = queue_size
        self._queue: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=queue_size)
        self._closing = threading.Event()
        self._state_lock = threading.Lock()
        self._dropped_records = 0
        self._write_errors = 0
        self._poll_interval_seconds = poll_interval_seconds
        self._close_timeout_seconds = close_timeout_seconds
        self._metrics = _get_log_metrics()
        self._metrics["capacity"].set(queue_size)
        self._worker = threading.Thread(
            target=self._write_loop,
            name="bcsfuse-nonblocking-logging",
            daemon=True,
        )
        self._worker.start()

    @property
    def dropped_records(self) -> int:
        return self._dropped_records

    @property
    def write_errors(self) -> int:
        return self._write_errors

    @property
    def formatter(self) -> logging.Formatter | None:  # type: ignore[override]
        return self.handlers[0].formatter

    @formatter.setter
    def formatter(self, value: logging.Formatter | None) -> None:
        # logging.Handler.__init__ assigns None before destination handlers are
        # stored. The destination formatter remains the single formatting place.
        if value is None:
            return
        for handler in self.handlers:
            handler.setFormatter(value)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait(record)
            self._metrics["depth"].set(self._queue.qsize())
        except queue.Full:
            with self._state_lock:
                self._dropped_records += 1
            self._metrics["dropped"].inc()

    def wait(self, *, timeout: float) -> None:
        """Wait for all currently queued records to be written."""

        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.005)
        for handler in self.handlers:
            handler.flush()

    def close(self, *, timeout: float | None = None) -> None:
        self._closing.set()
        self._worker.join(timeout=timeout or self._close_timeout_seconds)
        for handler in self.handlers:
            try:
                handler.flush()
                handler.close()
            except Exception:
                with self._state_lock:
                    self._write_errors += 1
                self._metrics["errors"].inc()
        super().close()

    def flush(self) -> None:
        for handler in self.handlers:
            handler.flush()

    def _write_loop(self) -> None:
        while not self._closing.is_set() or not self._queue.empty():
            try:
                record = self._queue.get(timeout=self._poll_interval_seconds)
            except queue.Empty:
                continue
            try:
                for handler in self.handlers:
                    handler.handle(record)
            except Exception:
                with self._state_lock:
                    self._write_errors += 1
                self._metrics["errors"].inc()
            finally:
                self._queue.task_done()


__all__ = ["NonblockingLoggingHandler"]
