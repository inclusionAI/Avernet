"""Prometheus metrics for search-critical external dependencies."""

from __future__ import annotations

import prometheus_client

from contextlib import contextmanager
from threading import Lock
from time import perf_counter
from typing import Any, Iterator


_METRICS: dict[str, Any] | None = None
_METRICS_LOCK = Lock()


def _get_metrics() -> dict[str, Any]:
    """Return one process-wide metric set and safely own one-time registration."""
    global _METRICS
    if _METRICS is not None:
        return _METRICS

    with _METRICS_LOCK:
        if _METRICS is not None:
            return _METRICS

        from prometheus_client import Counter, Gauge, Histogram
        registry = prometheus_client.REGISTRY

        qdrant_labels = ["operation", "mode", "outcome"]
        lock_labels = ["operation", "mode"]
        _METRICS = {
            "qdrant_duration": Histogram(
                "bcsfuse_qdrant_search_duration_seconds",
                "Dense and keyword searches in the Qdrant-backed vector store",
                qdrant_labels,
                buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
                registry=registry,
            ),
            "qdrant_lock_wait": Histogram(
                "bcsfuse_qdrant_lock_wait_seconds",
                "Time spent waiting for the vector index lock before a search",
                lock_labels,
                buckets=(0.0001, 0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1),
                registry=registry,
            ),
            "qdrant_errors": Counter(
                "bcsfuse_qdrant_errors_total",
                "Failed dense or keyword searches in the Qdrant-backed vector store",
                ["operation", "mode"],
                registry=registry,
            ),
            "rerank_calls": Counter(
                "bcsfuse_rerank_calls_total",
                "External reranker provider invocations",
                ["outcome"],
                registry=registry,
            ),
            "rerank_duration": Histogram(
                "bcsfuse_rerank_duration_seconds",
                "External reranker provider latency",
                ["outcome"],
                buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
                registry=registry,
            ),
            "search_fallback_unavailable": Counter(
                "bcsfuse_search_fallback_unavailable_total",
                "Terminal search or recommendation fallback envelopes by reason",
                ["operation", "reason_code"],
                registry=registry,
            ),
            "agent_search_last_known_good_stores": Counter(
                "bcsfuse_search_last_known_good_stores_total",
                "Successful search outcomes stored as last-known-good candidates",
                registry=registry,
            ),
            "agent_search_last_known_good_hits": Counter(
                "bcsfuse_search_last_known_good_hits_total",
                "Dependency-degraded requests served by explicit last-known-good data",
                registry=registry,
            ),
            "agent_search_negative_cache_stores": Counter(
                "bcsfuse_search_negative_cache_stores_total",
                "Terminal fallback outcomes stored in the short-lived negative cache",
                registry=registry,
            ),
            "agent_search_negative_cache_hits": Counter(
                "bcsfuse_search_negative_cache_hits_total",
                "Requests answered by the short-lived negative cache during an outage",
                registry=registry,
            ),
            "embedding_calls": Counter(
                "bcsfuse_embedding_calls_total",
                "External embedding provider invocations",
                ["model", "outcome"],
                registry=registry,
            ),
            "embedding_duration": Histogram(
                "bcsfuse_embedding_duration_seconds",
                "External embedding provider latency",
                ["model", "outcome"],
                buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
                registry=registry,
            ),
            "embedding_errors": Counter(
                "bcsfuse_embedding_errors_total",
                "Failed external embedding provider calls after retries",
                ["model"],
                registry=registry,
            ),
            "embedding_wire_responses": Counter(
                "bcsfuse_embedding_wire_responses_total",
                "External embedding responses by negotiated transport protocol",
                ["model", "protocol"],
                registry=registry,
            ),
            "embedding_dimension_contract_violations": Counter(
                "bcsfuse_embedding_dimension_contract_violations_total",
                "Embedding responses that violated the configured dimension contract",
                ["model", "protocol", "expected_dimension", "actual_dimensions"],
                registry=registry,
            ),
            "embedding_quota_rejected": Counter(
                "bcsfuse_embedding_quota_rejected_total",
                "Requests rejected by the shared model-capacity state",
                ["model", "reason"],
                registry=registry,
            ),
            "embedding_capacity_store_errors": Counter(
                "bcsfuse_embedding_capacity_store_errors_total",
                "Failures while reading or writing shared embedding capacity state",
                ["model", "operation"],
                registry=registry,
            ),
            "rerank_circuit_open": Gauge(
                "bcsfuse_rerank_circuit_open",
                "Whether the process-local reranker circuit is open or probing",
                ["model"],
                registry=registry,
            ),
            "rerank_circuit_rejected": Counter(
                "bcsfuse_rerank_circuit_rejected_total",
                "Reranker calls rejected while the circuit was open",
                ["model"],
                registry=registry,
            ),
            "rerank_circuit_transitions": Counter(
                "bcsfuse_rerank_circuit_transitions_total",
                "Reranker circuit state transitions",
                ["model", "from_state", "to_state"],
                registry=registry,
            ),
            "embedding_circuit_open": Gauge(
                "bcsfuse_embedding_circuit_open",
                "Whether the process-local embedding circuit is open or probing",
                ["model"],
                registry=registry,
            ),
            "embedding_circuit_rejected": Counter(
                "bcsfuse_embedding_circuit_rejected_total",
                "Embedding calls rejected while the circuit was open",
                ["model"],
                registry=registry,
            ),
            "embedding_circuit_transitions": Counter(
                "bcsfuse_embedding_circuit_transitions_total",
                "Embedding circuit state transitions",
                ["model", "from_state", "to_state"],
                registry=registry,
            ),
        }
    return _METRICS


class _QdrantCallMetrics:
    def __init__(self) -> None:
        self.lock_wait_seconds = 0.0


@contextmanager
def qdrant_search_metrics(operation: str, mode: str) -> Iterator[_QdrantCallMetrics]:
    """Record true success/error semantics without hiding provider failures."""
    metrics = _get_metrics()
    call = _QdrantCallMetrics()
    started_at = perf_counter()
    try:
        yield call
    except Exception:
        labels = {"operation": operation, "mode": mode, "outcome": "error"}
        metrics["qdrant_lock_wait"].labels(
            operation=operation, mode=mode
        ).observe(call.lock_wait_seconds)
        metrics["qdrant_duration"].labels(**labels).observe(
            perf_counter() - started_at
        )
        metrics["qdrant_errors"].labels(operation=operation, mode=mode).inc()
        raise
    else:
        metrics["qdrant_lock_wait"].labels(
            operation=operation, mode=mode
        ).observe(call.lock_wait_seconds)
        metrics["qdrant_duration"].labels(
            operation=operation, mode=mode, outcome="success"
        ).observe(perf_counter() - started_at)


def record_rerank_call(outcome: str, duration_seconds: float) -> None:
    """Record one provider outcome at the public reranker boundary."""
    metrics = _get_metrics()
    metrics["rerank_calls"].labels(outcome=outcome).inc()
    metrics["rerank_duration"].labels(outcome=outcome).observe(duration_seconds)


def record_embedding_call(
    model: str, outcome: str, duration_seconds: float
) -> None:
    """Record one real embedding provider outcome.

    An empty batch records nothing because it does not contact the provider.
    """
    metrics = _get_metrics()
    metrics["embedding_calls"].labels(model=model, outcome=outcome).inc()
    metrics["embedding_duration"].labels(model=model, outcome=outcome).observe(
        duration_seconds
    )
    if outcome == "error":
        metrics["embedding_errors"].labels(model=model).inc()


def record_embedding_wire_response(model: str, protocol: str) -> None:
    """Record which successful embedding response protocol was used."""
    _get_metrics()["embedding_wire_responses"].labels(
        model=model, protocol=protocol
    ).inc()


def set_embedding_circuit_open(model: str, *, open_: bool) -> None:
    _get_metrics()["embedding_circuit_open"].labels(model=model).set(
        1 if open_ else 0
    )


def record_embedding_dimension_contract_violation(
    model: str,
    protocol: str,
    *,
    expected_dimension: int,
    actual_dimensions: set[int],
) -> None:
    """Record a fail-closed provider response dimension contract violation."""

    _get_metrics()["embedding_dimension_contract_violations"].labels(
        model=model,
        protocol=protocol,
        expected_dimension=str(int(expected_dimension)),
        actual_dimensions=",".join(
            str(int(dimension)) for dimension in sorted(actual_dimensions)
        ),
    ).inc()


def record_embedding_quota_rejection(model: str, reason: str) -> None:
    _get_metrics()["embedding_quota_rejected"].labels(
        model=model, reason=reason or "unknown"
    ).inc()


def record_embedding_capacity_store_error(model: str, operation: str) -> None:
    _get_metrics()["embedding_capacity_store_errors"].labels(
        model=model, operation=operation
    ).inc()


def record_embedding_circuit_rejection(model: str) -> None:
    _get_metrics()["embedding_circuit_rejected"].labels(model=model).inc()


def record_embedding_circuit_transition(
    model: str, from_state: str, to_state: str
) -> None:
    _get_metrics()["embedding_circuit_transitions"].labels(
        model=model, from_state=from_state, to_state=to_state
    ).inc()

def set_rerank_circuit_open(model: str, *, open_: bool) -> None:
    _get_metrics()["rerank_circuit_open"].labels(model=model).set(
        1 if open_ else 0
    )


def record_rerank_circuit_rejection(model: str) -> None:
    _get_metrics()["rerank_circuit_rejected"].labels(model=model).inc()


def record_rerank_circuit_transition(
    model: str, from_state: str, to_state: str
) -> None:
    _get_metrics()["rerank_circuit_transitions"].labels(
        model=model, from_state=from_state, to_state=to_state
    ).inc()


def record_search_fallback_unavailable(operation: str, reason_code: str) -> None:
    """Record one explicit terminal outcome rather than a hidden empty success."""
    _get_metrics()["search_fallback_unavailable"].labels(
        operation=operation, reason_code=reason_code or "unknown"
    ).inc()


def record_agent_search_last_known_good_store() -> None:
    _get_metrics()["agent_search_last_known_good_stores"].inc()


def record_agent_search_last_known_good_hit() -> None:
    _get_metrics()["agent_search_last_known_good_hits"].inc()


def record_agent_search_negative_cache_store() -> None:
    _get_metrics()["agent_search_negative_cache_stores"].inc()


def record_agent_search_negative_cache_hit() -> None:
    _get_metrics()["agent_search_negative_cache_hits"].inc()
