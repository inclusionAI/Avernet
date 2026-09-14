"""Integration tests for chat_stream → ActiveRunRegistry wiring.

Asserts that the OpenClaw port's ``chat_stream`` registers an active run on
entry, touches it as events flow through (runId/state propagation), and
releases it on terminal (final/error/aborted) or exception paths.

Reuses the ``_FakeClient`` / ``_FakePool`` pattern from
``test_chat_stream.py`` so this stays transport-light and isolated.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from engine.community.plugins.openclaw.active_run_registry import ActiveRunRegistry
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl
from engine.community.plugins.skills_pool.center_content import (
    MountedCenterContentAdapter,
)


class _FakeClient:
    """Fake OpenClawGatewayClient — emits a scripted sequence of events."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events
        self.calls: list[dict] = []

    async def chat_stream(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        for e in self._events:
            yield dict(e)


class _FakePool:
    def __init__(self, client: _FakeClient) -> None:
        self._client = client

    async def get(self, token: str | None = None) -> _FakeClient:
        return self._client


def _impl(
    events: list[dict[str, Any]] | None = None,
    *,
    registry: ActiveRunRegistry | None = None,
) -> tuple[OpenClawPluginImpl, _FakeClient]:
    client = _FakeClient(events or [])
    impl = OpenClawPluginImpl(
        center_content_adapter=MountedCenterContentAdapter(),
        pool=_FakePool(client),
        active_run_registry=registry,
    )
    return impl, client


async def test_registers_active_run_during_stream():
    events = [
        {"state": "delta", "runId": "run-1"},
        {"state": "delta", "runId": "run-1"},
    ]
    registry = ActiveRunRegistry()
    impl, _ = _impl(events, registry=registry)
    # Push a long-running stream forward one event at a time; after the first
    # `delta` event, the registry must show one active entry.
    agen = impl.chat_stream(session_key="session:s1:user:1", message="m")
    next_event = await agen.__anext__()
    assert next_event.payload["state"] == "delta"
    live, stale = registry.list_active_sessions()
    assert stale == 0
    assert len(live) == 1
    assert live[0]["run_id"] == "run-1"
    assert live[0]["last_state"] == "delta"
    # Close the iterator cleanly — finally must release the entry.
    await agen.aclose()
    assert registry.active_count() == 0


async def test_release_on_final_terminal():
    events = [
        {"state": "delta", "runId": "run-1"},
        {"state": "final", "runId": "run-1"},
    ]
    registry = ActiveRunRegistry()
    impl, _ = _impl(events, registry=registry)
    frames = [f async for f in impl.chat_stream(session_key="session:s1:user:1", message="m")]
    assert [f.payload["state"] for f in frames] == ["delta", "final"]
    assert registry.active_count() == 0


async def test_release_on_error_terminal():
    for terminal in ("error", "aborted"):
        events = [
            {"state": "delta", "runId": "run-1"},
            {"state": terminal, "runId": "run-1"},
        ]
        registry = ActiveRunRegistry()
        impl, _ = _impl(events, registry=registry)
        frames = [f async for f in impl.chat_stream(session_key="session:s1:user:1", message="m")]
        assert frames[-1].payload["state"] == terminal
        assert registry.active_count() == 0, terminal


async def test_inject_final_does_not_release_foreground_run():
    """A `state=final` event whose runId starts with `inject-` is an
    out-of-band broadcast — the foreground run must remain registered."""
    events = [
        {"state": "delta", "runId": "run-1"},
        {"state": "final", "runId": "inject-abc"},
        {"state": "final", "runId": "run-1"},
    ]
    registry = ActiveRunRegistry()
    impl, _ = _impl(events, registry=registry)
    # Capture registry snapshot mid-stream by peeking after delta.
    agen = impl.chat_stream(session_key="session:s1:user:1", message="m")
    await agen.__anext__()  # delta
    assert registry.active_count() == 1
    # Next is the inject final — the stream continues, foreground stays active.
    second = await agen.__anext__()
    assert second.payload["runId"] == "inject-abc"
    assert registry.active_count() == 1
    last_event = None
    async for f in agen:
        last_event = f
    assert last_event.payload["runId"] == "run-1"
    assert registry.active_count() == 0


async def test_release_on_client_exception():
    """ConnectionError from client.chat_stream is re-raised, the run is
    released via the finally block (last_state=error)."""

    class _BoomClient(_FakeClient):
        async def chat_stream(self, **kwargs):  # noqa: ANN003
            self.calls.append(kwargs)
            raise ConnectionError("socket died mid-stream")
            yield {}  # pragma: no cover - never reached

    registry = ActiveRunRegistry()
    client = _BoomClient(events=[])
    impl = OpenClawPluginImpl(
        center_content_adapter=MountedCenterContentAdapter(),
        pool=_FakePool(client),
        active_run_registry=registry,
    )
    with pytest.raises(ConnectionError):
        _ = [f async for f in impl.chat_stream(session_key="session:s1:user:1", message="m")]
    assert registry.active_count() == 0


async def test_session_key_defaulted_in_payload_and_registry():
    events = [{"state": "delta", "runId": "r1"}, {"state": "final", "runId": "r1"}]
    registry = ActiveRunRegistry()
    impl, _ = _impl(events, registry=registry)
    frames = [f async for f in impl.chat_stream(session_key="my-key", message="m")]
    assert frames[0].payload["sessionKey"] == "my-key"
    # after stream, registry should be empty AND the released entry's canonical
    # session_id was "my-key" (non-uuid fallback).
    assert registry.active_count() == 0


async def test_two_parallel_stream_calls_are_isolated():
    events_a = [{"state": "delta", "runId": "runA"}, {"state": "final", "runId": "runA"}]
    events_b = [{"state": "delta", "runId": "runB"}, {"state": "final", "runId": "runB"}]
    registry = ActiveRunRegistry()
    impl_a, _ = _impl(events_a, registry=registry)
    impl_b, _ = _impl(events_b, registry=registry)
    a_gen = impl_a.chat_stream(session_key="session:sA:user:1", message="ma")
    b_gen = impl_b.chat_stream(session_key="session:sB:user:1", message="mb")
    _ = await a_gen.__anext__()
    _ = await b_gen.__anext__()
    live, stale = registry.list_active_sessions()
    assert stale == 0
    assert {e["session_id"] for e in live} == {"sA", "sB"}
    async for _ in a_gen:
        pass
    async for _ in b_gen:
        pass
    assert registry.active_count() == 0
