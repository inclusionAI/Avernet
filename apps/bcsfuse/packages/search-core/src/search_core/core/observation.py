"""Bounded, privacy-safe event summaries and an optional request time budget."""

import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from queue import Empty, Full, Queue
from threading import Lock
from uuid import uuid4

search_transport = ContextVar("search_transport", default="")
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
    uses only bounded internal metadata; exception messages are excluded.
    Timing windows keep the slowest request's stages plus total mean/max latency.
    """

    def __init__(self, interval=5.0):
        self.interval = interval
        self._lock = Lock()
        self._buckets = {}
        self._pending = Queue(maxsize=128)
        self._steps = Queue(maxsize=4096)

    def record_steps(self, operation, outcome, elapsed_ms, stages, summary, results):
        if not logger.isEnabledFor(logging.INFO):
            return
        record = {
            "event": "search_steps",
            "timestamp": time.time(),
            "operation": operation,
            "trace_id": summary.get("trace_id", ""),
            "request_id": _request_id.get(),
            "outcome": outcome,
            "elapsed_ms": elapsed_ms,
            "result_count": summary.get("result_count"),
            "result_cache": stages.get("result_cache_read", {}).get("cache", "skipped"),
            "stage_ms": {name: stage["elapsed_ms"] for name, stage in stages.items()},
        }
        if "card_fetch_details" in summary:
            record["card_fetch_details"] = summary["card_fetch_details"]
        record["results"] = results
        record["reasons"] = summary.get("reasons", ())
        if "error_type" in summary:
            record["error_type"] = summary["error_type"]
        self.enqueue_detail(record)

    def record_progress(self, stage, trace_id, **fields):
        if logger.isEnabledFor(logging.INFO):
            self.enqueue_detail(
                {
                    "event": "search_progress",
                    "stage": stage,
                    "trace_id": trace_id,
                    "request_id": _request_id.get(),
                    "timestamp": time.time(),
                    **fields,
                }
            )

    def enqueue_detail(self, record):
        try:
            self._steps.put_nowait(record)
        except Full:
            self.record("search_steps_dropped", "logging", "queue_full")

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
            if event == "search_timing":
                elapsed = fields["elapsed_ms"]
                if elapsed >= bucket.get("max_ms", 0):
                    bucket["sample"] = {"request_id": _request_id.get(), **fields}
                bucket["total_ms"] = bucket.get("total_ms", 0) + elapsed
                bucket["max_ms"] = max(bucket.get("max_ms", 0), elapsed)
            else:
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
        if record["event"] == "search_timing":
            record.update(
                avg_ms=round(bucket["total_ms"] / bucket["count"], 2),
                max_ms=bucket["max_ms"],
            )
        level = (
            logging.INFO
            if record["event"] == "search_timing"
            or (
                record["event"] == "search_index_sync" and record["reason"] == "applied"
            )
            else logging.WARNING
        )
        try:
            self._pending.put_nowait(
                (level, json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            )
        except Full:
            # Retain all counts for the next flush if the sink is stalled.
            return
        bucket.update(count=0, start=now, last=now, total_ms=0, max_ms=0)

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
                level, line = self._pending.get_nowait()
            except Empty:
                break
            logger.log(level, line)

        for _ in range(self._steps.qsize()):
            try:
                record = self._steps.get_nowait()
            except Empty:
                break
            logger.info(json.dumps(record, ensure_ascii=False, separators=(",", ":")))


events = EventSummaries()
