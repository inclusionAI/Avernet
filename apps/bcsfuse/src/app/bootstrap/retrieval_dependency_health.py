"""Readiness-driven preopening for the terminal retrieval outage circuit."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.application.services.retrieval_dependency_circuit import (
    RetrievalDependencyCircuit,
)

logger = logging.getLogger(__name__)

ReadinessProbe = Callable[[], Awaitable[tuple[bool, dict[str, Any]]]]


class RetrievalDependencyHealthMonitor:
    """Keep the retrieval circuit aligned with an independently observed health.

    Probe results are explicit and conservative.  An observed failure of both
    dense and keyword dependencies, or a hard health-probe timeout, preopens
    terminal fallback.  Only a successful observation of both dependencies
    closes it.  Partial or unknown observations leave the serving circuit
    unchanged.
    """

    def __init__(
        self,
        *,
        readiness_probe: ReadinessProbe,
        circuit: RetrievalDependencyCircuit,
        probe_interval_seconds: float,
    ) -> None:
        if probe_interval_seconds <= 0:
            raise ValueError("probe_interval_seconds must be positive")
        self.circuit = circuit
        self._readiness_probe = readiness_probe
        self._probe_interval_seconds = probe_interval_seconds
        self._last_state = "unknown"

    async def observe_once(self) -> str:
        """Observe dependency readiness within the hard health budget."""

        return await self.observe_once_with_timeout(timeout_seconds=1.0)

    async def observe_once_with_timeout(self, *, timeout_seconds: float) -> str:
        """Observe once and never allow startup to inherit a dependency hang."""

        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        try:
            _, info = await asyncio.wait_for(
                self._readiness_probe(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            # A hard health timeout is a serving-availability signal. Fail over
            # to terminal local fallback immediately while a later bounded
            # health sample can recover the semantic chain.
            self.circuit.record_outage()
            self._last_state = "probe_timeout"
            return self._last_state
        dense_ready = bool(info.get("dense_qdrant_ready", False)) is True
        keyword_ready = bool(info.get("keyword_qdrant_ready", False)) is True
        dense_observed = "dense_qdrant_ready" in info
        keyword_observed = "keyword_qdrant_ready" in info

        if (
            dense_observed
            and keyword_observed
            and dense_ready is False
            and keyword_ready is False
        ):
            self.circuit.record_outage()
            self._last_state = "outage"
            return self._last_state
        if dense_ready and keyword_ready:
            self.circuit.record_healthy()
            self._last_state = "healthy"
            return self._last_state
        self._last_state = "unknown"
        return self._last_state

    async def run(self) -> None:
        """Poll readiness until cancelled without stopping on probe errors."""

        while True:
            # Startup already made one explicit observation. The loop waits for
            # its full interval before the next health sample.
            await asyncio.sleep(self._probe_interval_seconds)
            try:
                await self.observe_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._last_state = "unknown"
                logger.warning(
                    "[RetrievalHealth] readiness observation failed: %s",
                    type(error).__name__,
                )


__all__ = [
    "RetrievalDependencyHealthMonitor",
]
