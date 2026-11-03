"""TDD for readiness-driven terminal outage preopening."""

from __future__ import annotations

import asyncio

from app.application.services.retrieval_dependency_circuit import (
    RetrievalDependencyCircuit,
)


def circuit() -> RetrievalDependencyCircuit:
    return RetrievalDependencyCircuit(outage_probe_seconds=30.0)


def test_dual_qdrant_readiness_failure_preopens_the_circuit() -> None:
    from app.bootstrap.retrieval_dependency_health import (
        RetrievalDependencyHealthMonitor,
    )

    async def probe() -> tuple[bool, dict]:
        return False, {
            "dense_qdrant_ready": False,
            "keyword_qdrant_ready": False,
        }

    monitor = RetrievalDependencyHealthMonitor(
        readiness_probe=probe,
        circuit=circuit(),
        probe_interval_seconds=1.25,
    )

    async def scenario() -> None:
        await monitor.observe_once()

    asyncio.run(scenario())
    assert monitor.circuit.outaged is True


def test_healthy_dual_qdrant_readiness_releases_the_circuit() -> None:
    from app.bootstrap.retrieval_dependency_health import (
        RetrievalDependencyHealthMonitor,
    )

    target = circuit()
    target.record_outage()

    async def probe() -> tuple[bool, dict]:
        return True, {
            "dense_qdrant_ready": True,
            "keyword_qdrant_ready": True,
        }

    monitor = RetrievalDependencyHealthMonitor(
        readiness_probe=probe,
        circuit=target,
        probe_interval_seconds=1.25,
    )

    async def scenario() -> None:
        await monitor.observe_once()

    asyncio.run(scenario())
    assert target.outaged is False


def test_unknown_or_partial_readiness_does_not_preopen_terminal_fallback() -> None:
    from app.bootstrap.retrieval_dependency_health import (
        RetrievalDependencyHealthMonitor,
    )

    target = circuit()

    async def probe() -> tuple[bool, dict]:
        return False, {
            "dense_qdrant_ready": False,
            "keyword_qdrant_ready": True,
        }

    monitor = RetrievalDependencyHealthMonitor(
        readiness_probe=probe,
        circuit=target,
        probe_interval_seconds=1.25,
    )

    async def scenario() -> None:
        await monitor.observe_once()

    asyncio.run(scenario())
    assert target.outaged is False


def test_bounded_health_probe_timeout_preopens_terminal_fallback() -> None:
    from app.bootstrap.retrieval_dependency_health import (
        RetrievalDependencyHealthMonitor,
    )

    async def timeout_probe() -> tuple[bool, dict]:
        await asyncio.sleep(0.2)
        return True, {}

    monitor = RetrievalDependencyHealthMonitor(
        readiness_probe=timeout_probe,
        circuit=circuit(),
        probe_interval_seconds=1.25,
    )

    async def scenario() -> str:
        return await monitor.observe_once_with_timeout(timeout_seconds=0.01)

    state = asyncio.run(scenario())

    assert state == "probe_timeout"
    assert monitor.circuit.outaged is True
