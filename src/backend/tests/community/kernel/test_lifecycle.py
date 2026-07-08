"""Unit tests for :mod:`agentclaw.community.kernel.lifecycle`.

Covers:
  * ``discover_lifecycle_participants`` — finds Lifecycle implementors,
    skips non-implementors, dedupes by instance identity.
  * Two-phase ordering — every ``bootstrap()`` finishes before any
    ``startup()`` begins (and the symmetric shutdown→teardown).
  * Fail-fast in the setup direction.
  * Log-and-continue in the teardown direction.
  * ``LifecycleBase``'s four no-op defaults are individually callable.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from injector import Injector, Module, provider, singleton

from agentclaw.community.kernel.lifecycle import (
    Lifecycle,
    LifecycleBase,
    discover_lifecycle_participants,
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class _Recorder:
    """Shared side-channel for ordering assertions."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def mark(self, label: str) -> None:
        self.events.append(label)


class _StubA(LifecycleBase):
    def __init__(self, rec: _Recorder) -> None:
        self.rec = rec

    async def bootstrap(self) -> None:
        self.rec.mark("A.bootstrap")

    async def startup(self) -> None:
        self.rec.mark("A.startup")

    async def shutdown(self) -> None:
        self.rec.mark("A.shutdown")

    async def teardown(self) -> None:
        self.rec.mark("A.teardown")


class _StubB(LifecycleBase):
    """Only overrides startup() and teardown() — rest stay as no-ops."""

    def __init__(self, rec: _Recorder) -> None:
        self.rec = rec

    async def startup(self) -> None:
        self.rec.mark("B.startup")

    async def teardown(self) -> None:
        self.rec.mark("B.teardown")


class _NotALifecycle:
    """Has no lifecycle hooks — must not be discovered."""

    pass


# ---------------------------------------------------------------------------
# discover_lifecycle_participants
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_discover_finds_lifecycle_impls_and_skips_others() -> None:
    rec = _Recorder()
    a = _StubA(rec)
    b = _StubB(rec)
    plain = _NotALifecycle()

    class M(Module):
        @singleton
        @provider
        def _a(self) -> _StubA:
            return a

        @singleton
        @provider
        def _b(self) -> _StubB:
            return b

        @singleton
        @provider
        def _plain(self) -> _NotALifecycle:
            return plain

    injector = Injector([M()])
    found = discover_lifecycle_participants(injector)

    assert a in found
    assert b in found
    assert plain not in found


@pytest.mark.unit
def test_discover_dedupes_by_instance_identity() -> None:
    """The same singleton reachable from two bindings is returned once."""
    rec = _Recorder()
    a = _StubA(rec)

    class M(Module):
        @singleton
        @provider
        def _direct(self) -> _StubA:
            return a

        @singleton
        @provider
        def _aliased(self) -> Lifecycle:
            return a  # same instance, different bound interface

    injector = Injector([M()])
    found = discover_lifecycle_participants(injector)

    matching = [p for p in found if p is a]
    assert len(matching) == 1, f"expected dedup, got {len(matching)}"


# ---------------------------------------------------------------------------
# Two-phase ordering — bootstrap before startup, shutdown before teardown
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_two_phase_ordering() -> None:
    """All bootstrap() finish before any startup(); all shutdown() before any teardown()."""
    rec = _Recorder()
    a = _StubA(rec)
    b = _StubB(rec)

    async def run() -> None:
        participants = [a, b]
        await asyncio.gather(*(p.bootstrap() for p in participants))
        await asyncio.gather(*(p.startup() for p in participants))
        await asyncio.gather(
            *(p.shutdown() for p in participants), return_exceptions=True
        )
        await asyncio.gather(
            *(p.teardown() for p in participants), return_exceptions=True
        )

    asyncio.run(run())

    # All bootstrap events come before any startup event.
    boot_idx = [i for i, e in enumerate(rec.events) if e.endswith(".bootstrap")]
    start_idx = [i for i, e in enumerate(rec.events) if e.endswith(".startup")]
    assert boot_idx, "no bootstrap events recorded"
    assert start_idx, "no startup events recorded"
    assert max(boot_idx) < min(start_idx), (
        f"bootstrap should fully complete before startup; events={rec.events}"
    )

    # All shutdown events come before any teardown event.
    shut_idx = [i for i, e in enumerate(rec.events) if e.endswith(".shutdown")]
    tear_idx = [i for i, e in enumerate(rec.events) if e.endswith(".teardown")]
    assert shut_idx, "no shutdown events recorded"
    assert tear_idx, "no teardown events recorded"
    assert max(shut_idx) < min(tear_idx), (
        f"shutdown should fully complete before teardown; events={rec.events}"
    )


# ---------------------------------------------------------------------------
# Fail-fast in the setup direction
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_setup_fail_fast_on_bootstrap_exception() -> None:
    """An exception in any bootstrap() propagates out of the gather."""

    class _BadBootstrap(LifecycleBase):
        async def bootstrap(self) -> None:
            raise RuntimeError("boom-bootstrap")

    rec = _Recorder()
    good = _StubA(rec)
    bad = _BadBootstrap()

    async def run() -> None:
        await asyncio.gather(*(p.bootstrap() for p in [good, bad]))

    with pytest.raises(RuntimeError, match="boom-bootstrap"):
        asyncio.run(run())


@pytest.mark.unit
def test_setup_fail_fast_on_startup_exception() -> None:
    """An exception in any startup() propagates out of the gather."""

    class _BadStartup(LifecycleBase):
        async def startup(self) -> None:
            raise RuntimeError("boom-startup")

    rec = _Recorder()
    good = _StubA(rec)
    bad = _BadStartup()

    async def run() -> None:
        await asyncio.gather(*(p.startup() for p in [good, bad]))

    with pytest.raises(RuntimeError, match="boom-startup"):
        asyncio.run(run())


# ---------------------------------------------------------------------------
# Log-and-continue in the teardown direction
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_teardown_logs_and_continues_on_shutdown_exception() -> None:
    """One participant's shutdown() exception does not block others."""

    class _BadShutdown(LifecycleBase):
        async def shutdown(self) -> None:
            raise RuntimeError("boom-shutdown")

    rec = _Recorder()
    bad = _BadShutdown()
    good = _StubA(rec)

    async def run() -> list[Any]:
        return await asyncio.gather(
            *(p.shutdown() for p in [bad, good]), return_exceptions=True
        )

    results = asyncio.run(run())
    # First participant raised; second completed.
    assert isinstance(results[0], RuntimeError) and "boom-shutdown" in str(results[0])
    assert results[1] is None
    assert "A.shutdown" in rec.events, (
        f"good participant's shutdown should have run; events={rec.events}"
    )


@pytest.mark.unit
def test_teardown_logs_and_continues_on_teardown_exception() -> None:
    """Symmetric: a teardown() exception does not block other teardowns."""

    class _BadTeardown(LifecycleBase):
        async def teardown(self) -> None:
            raise RuntimeError("boom-teardown")

    rec = _Recorder()
    bad = _BadTeardown()
    good = _StubA(rec)

    async def run() -> list[Any]:
        return await asyncio.gather(
            *(p.teardown() for p in [bad, good]), return_exceptions=True
        )

    results = asyncio.run(run())
    assert isinstance(results[0], RuntimeError) and "boom-teardown" in str(results[0])
    assert results[1] is None
    assert "A.teardown" in rec.events


# ---------------------------------------------------------------------------
# LifecycleBase — no-op defaults are individually callable
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_lifecycle_base_no_op_defaults() -> None:
    """LifecycleBase's four hooks each return None without args."""
    base = LifecycleBase()

    async def run() -> tuple:
        return (
            await base.bootstrap(),
            await base.startup(),
            await base.shutdown(),
            await base.teardown(),
        )

    results = asyncio.run(run())
    assert results == (None, None, None, None)


@pytest.mark.unit
def test_lifecycle_base_satisfies_protocol_at_runtime() -> None:
    """isinstance() check works because LifecycleBase declares all four attrs."""
    assert isinstance(LifecycleBase(), Lifecycle)
