"""Port-impl tests for OpenClawPluginImpl.chat_stream stop/skip semantics.

Drives the impl against a fake pool→client so the inject-runId skip + stop-state
break logic (relocated from the legacy chat service) is pinned at the transport
layer — the adapter tests use a fake *port* and never exercise this.
"""
from __future__ import annotations

from typing import Any

from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl


class _FakeClient:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events
        self.calls: list[dict] = []

    async def chat_stream(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        for e in self._events:
            yield dict(e)  # copy — chat_stream mutates event_data in place


class _FakePool:
    def __init__(self, client: _FakeClient) -> None:
        self._client = client

    async def get(self, token: str | None = None) -> _FakeClient:
        return self._client


def _impl(events: list[dict[str, Any]]) -> tuple[OpenClawPluginImpl, _FakeClient]:
    client = _FakeClient(events)
    return OpenClawPluginImpl(pool=_FakePool(client)), client


async def test_inject_runid_final_does_not_stop_stream():
    # A `final` event whose runId starts with "inject-" must be yielded but must
    # NOT terminate the stream (matches legacy chat.py inject-skip).
    events = [
        {"state": "delta", "runId": "r1"},
        {"state": "final", "runId": "inject-abc"},  # yielded, NOT a stop
        {"state": "delta", "runId": "r1"},
        {"state": "final", "runId": "r1"},          # real final → stop here
        {"state": "delta", "runId": "r1"},          # never reached
    ]
    impl, _ = _impl(events)
    frames = [f async for f in impl.chat_stream(session_key="s", message="m")]
    assert len(frames) == 4
    assert frames[-1].payload["state"] == "final"
    assert frames[-1].payload["runId"] == "r1"


async def test_real_final_stops_stream():
    events = [
        {"state": "delta", "runId": "r1"},
        {"state": "final", "runId": "r1"},
        {"state": "delta", "runId": "r1"},  # never reached
    ]
    impl, _ = _impl(events)
    frames = [f async for f in impl.chat_stream(session_key="s", message="m")]
    assert len(frames) == 2


async def test_error_and_aborted_states_stop_stream():
    for stop_state in ("error", "aborted"):
        events = [
            {"state": "delta", "runId": "r1"},
            {"state": stop_state, "runId": "r1"},
            {"state": "delta", "runId": "r1"},  # never reached
        ]
        impl, _ = _impl(events)
        frames = [f async for f in impl.chat_stream(session_key="s", message="m")]
        assert len(frames) == 2, stop_state
        assert frames[-1].payload["state"] == stop_state


async def test_session_key_defaulted_into_event_payload():
    events = [{"state": "final", "runId": "r1"}]
    impl, client = _impl(events)
    frames = [f async for f in impl.chat_stream(session_key="my-key", message="m")]
    assert frames[0].payload["sessionKey"] == "my-key"
    # the client received the same session_key
    assert client.calls[0]["session_key"] == "my-key"
