"""Bounded circuit for a terminal dual-Qdrant outage."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any


class RetrievalDependencyCircuit:
    """Bound the cost of repeated failures while retaining bounded recovery.

    The circuit does not probe backends itself.  Retrieval observations decide
    whether both the dense and keyword dependencies are unavailable.  While the
    outage is alive all requests use process-local fallback; after the probe TTL
    only one request at a time may retry the normal retrieval chain.
    """

    def __init__(
        self,
        *,
        outage_probe_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if outage_probe_seconds <= 0:
            raise ValueError("outage_probe_seconds must be positive")

        self._outage_probe_seconds = outage_probe_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._outaged = False
        self._outage_until = 0.0
        self._recovery_probe_running = False
        self._dense_outaged = False

    @property
    def outaged(self) -> bool:
        with self._lock:
            return self._outaged

    @property
    def dense_outaged(self) -> bool:
        """Return whether dense retrieval is known unavailable.

        This state is independent of the terminal dual-outage circuit so a
        dense-only failure can route directly to keyword retrieval.
        """

        with self._lock:
            return self._dense_outaged

    def should_fail_fast(self) -> bool:
        """Return whether this caller must use local fallback.

        At most one caller is admitted when an outage TTL expires.  That caller
        probes the normal chain; concurrent callers continue to receive bounded
        local results without holding admission capacity behind dead Qdrant.
        """

        with self._lock:
            if not self._outaged:
                return False
            if self._clock() < self._outage_until:
                return True
            if self._recovery_probe_running:
                return True
            self._recovery_probe_running = True
            return False

    def record_outage(self) -> None:
        with self._lock:
            self._outaged = True
            self._outage_until = (
                self._clock() + self._outage_probe_seconds
            )
            self._recovery_probe_running = False

    def record_dense_outage(self) -> None:
        """Open dense-only bypass and release a stale terminal outage.

        This method is only called with healthy keyword side information. A
        transient dual observation must not keep keyword traffic in process
        local fallback while dense remains unavailable.
        """

        with self._lock:
            self._dense_outaged = True
            self._outaged = False
            self._outage_until = 0.0
            self._recovery_probe_running = False

    def record_dense_healthy(self) -> None:
        with self._lock:
            self._dense_outaged = False

    def record_healthy(self) -> None:
        with self._lock:
            self._outaged = False
            self._outage_until = 0.0
            self._recovery_probe_running = False
            self._dense_outaged = False

    def observe_retrieval(self, diagnostics: Mapping[str, Any]) -> None:
        """Record a dual outage or release a successful recovery probe."""

        dual_outage = (
            diagnostics.get("semantic_unavailable") is True
            and diagnostics.get("keyword_unavailable") is True
        )
        if dual_outage:
            self.record_outage()
            return
        dense_healthy = (
            diagnostics.get("dense_qdrant_available") is True
            or diagnostics.get("dense_qdrant_ready") is True
        )
        keyword_healthy = (
            diagnostics.get("keyword_qdrant_available") is True
            or diagnostics.get("keyword_qdrant_ready") is True
        )
        dense_unavailable = (
            diagnostics.get("dense_qdrant_available") is False
            or diagnostics.get("dense_qdrant_ready") is False
        )
        if dense_unavailable and keyword_healthy:
            self.record_dense_outage()
            return
        if dense_healthy and keyword_healthy:
            self.record_healthy()


def mark_dual_retrieval_dependency_outage(
    metadata: dict[str, Any],
) -> None:
    """Attach the stable terminal-outage semantics to local fallback results."""

    metadata.update(
        {
            "dense_qdrant_available": False,
            "keyword_qdrant_available": False,
            "dense_qdrant_unavailable": True,
            "keyword_qdrant_unavailable": True,
            "semantic_unavailable": True,
            "semantic_reason_code": "retrieval_dependency_unavailable",
            "retrieval_unavailable": True,
            "retrieval_error": "RetrievalDependencyOutage",
            "candidate_unavailable": True,
            "candidate_error": "RetrievalDependencyOutage",
            "keyword_unavailable": True,
            "retrieval_dependency_failfast": True,
        }
    )


__all__ = [
    "RetrievalDependencyCircuit",
    "mark_dual_retrieval_dependency_outage",
]
