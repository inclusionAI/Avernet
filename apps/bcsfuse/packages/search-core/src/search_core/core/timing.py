"""Request-local stage timings, emitted through the existing bounded log sink."""

import asyncio
import time
from contextlib import contextmanager
from contextvars import ContextVar

from search_core.core.observation import events, failure_reason, upstream_status

_trace_id = ContextVar("search_trace_id", default="")
_stages = ContextVar("search_stage_timings", default=None)


@contextmanager
def request_timing(operation, trace_id):
    """Log each search and retain detailed slow samples for window statistics."""
    stages = {}
    token = _stages.set(stages)
    trace_token = _trace_id.set(trace_id)
    summary = {"outcome": "ok"}
    if trace_id:
        summary["trace_id"] = trace_id
    started = time.monotonic()
    try:
        yield summary
    except asyncio.CancelledError:
        summary["outcome"] = "cancelled"
        raise
    except Exception as error:
        summary.update(outcome="error", error_type=type(error).__name__)
        raise
    finally:
        _trace_id.reset(trace_token)
        _stages.reset(token)
        outcome = summary.pop("outcome")
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        results = summary.pop("results", [])
        events.record_steps(operation, outcome, elapsed_ms, stages, summary, results)
        events.record(
            "search_timing",
            "request",
            outcome,
            elapsed_ms=elapsed_ms,
            stages={name: dict(value) for name, value in stages.items()},
            **summary,
        )


@contextmanager
def stage_timing(name):
    """Elapsed wall time includes await/queue time, not just dependency CPU time.

    Names and annotations must be bounded internal values, never query/Card data.
    No active request means no timing or logging (e.g. background indexing/TC).
    """
    stages = _stages.get()
    fields = {"outcome": "ok"}
    if stages is None:
        yield fields
        return
    started = time.monotonic()
    try:
        yield fields
    except asyncio.CancelledError:
        fields["outcome"] = "cancelled"
        raise
    except Exception as error:
        fields.update(
            outcome=failure_reason(error),
            error_type=type(error).__name__,
            upstream_status=upstream_status(error),
        )
        raise
    finally:
        fields["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
        stages[name] = fields
        if name in {
            "embedding",
            "vector",
            "keyword",
            "recall_merge",
            "fusion",
            "reranker",
        } or (name == "result_cache_read" and fields.get("cache") == "hit"):
            events.record_progress(name, _trace_id.get(), **fields)
