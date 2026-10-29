"""Bounded, privacy-safe event summaries and an optional request time budget."""

import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from queue import Empty, Full, Queue
from threading import Lock
from uuid import uuid4

_deadline = ContextVar("search_deadline", default=float("inf"))
_request_id = ContextVar("search_request_id", default="")
logger = logging.getLogger("search_core.events")


@contextmanager
def request_budget(seconds):
    deadline = _deadline.set(min(_deadline.get(), time.monotonic() + seconds))
    trace = _request_id.set(uuid4().hex)
    try:
        yield
    finally:
        _request_id.reset(trace)
        _deadline.reset(deadline)


def remaining_timeout(seconds):
    return max(0.0, min(seconds, _deadline.get() - time.monotonic()))


def upstream_status(error):
    """Inspect SDK wrappers without logging response bodies or exception messages."""
    current = error
    for _ in range(6):
        if current is None:
            break
        status = getattr(current, "status_code", None)
        if status is None:
            status = getattr(getattr(current, "response", None), "status_code", None)
        if isinstance(status, int) and 100 <= status <= 599:
            return status
        current = current.__cause__ or current.__context__
    return None


def failure_reason(error):
    status = upstream_status(error)
    if status == 429:
        return "quota_exceeded"
    if status is not None and 400 <= status < 500:
        return "upstream_rejected"
    current = error
    for _ in range(6):
        if current is None:
            break
        if isinstance(current, TimeoutError) or "Timeout" in type(current).__name__:
            return "timeout"
        current = current.__cause__ or current.__context__
    return "dependency_error"


class EventSummaries:
    """Every event is counted; emit first sample, then periodic count summaries.

    Keys must be bounded internal stage/reason names, never user input. Logging
    uses only whitelisted scalar metadata; exception messages are excluded.
    """

    def __init__(self, interval=5.0):
        self.interval = interval
        self._lock = Lock()
        self._buckets = {}
        self._pending = Queue(maxsize=128)

    def record(self, event, stage, reason, **fields):
        now = time.monotonic()
        key = (event, stage, reason)
        with self._lock:
            bucket = self._buckets.setdefault(
                key,
                {
                    "event": event,
                    "stage": stage,
                    "reason": reason,
                    "count": 0,
                    "start": now,
                    "last": 0.0,
                },
            )
            bucket["count"] += 1
            bucket["sample"] = {"request_id": _request_id.get(), **fields}
            if now - bucket["last"] >= self.interval:
                self._emit(bucket, now)

    def _emit(self, bucket, now):
        if not bucket["count"]:
            return
        record = {k: bucket[k] for k in ("event", "stage", "reason", "count")}
        record.update(
            window_ms=round((now - bucket["start"]) * 1000, 2),
            timestamp=time.time(),
            sample=bucket["sample"],
        )
        try:
            self._pending.put_nowait(
                json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            )
        except Full:
            # Retain all counts for the next flush if the sink is stalled.
            return
        bucket.update(count=0, start=now, last=now)

    def flush(self, *, force=False):
        now = time.monotonic()
        with self._lock:
            for bucket in self._buckets.values():
                if force or now - bucket["last"] >= self.interval:
                    self._emit(bucket, now)

        # Host calls flush on a background thread. Never write the sink while
        # holding the counter lock or from the request's record() path.
        for _ in range(128):
            try:
                line = self._pending.get_nowait()
            except Empty:
                break
            logger.warning(line)


events = EventSummaries()
