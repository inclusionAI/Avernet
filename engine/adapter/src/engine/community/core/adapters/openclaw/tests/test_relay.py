"""Unit tests for the OpenClaw relay ACL adapter.

Drives `OpenClawRelayAdapter` against a fake `OpenClawRelayPort` (a plain object
returning canned frames) — the adapter's job is auth-token extraction and
transparent delegation.  No gateway is involved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from engine.community.core.adapters.openclaw.relay import OpenClawRelayAdapter
from engine.community.core.engine.context import AuthContext
from engine.community.kernel.frames import ErrorShape, ResponseFrame


@dataclass
class _FakeAuth:
    token: str | None = None


def _auth(token: str | None = None) -> AuthContext:
    return _FakeAuth(token=token)  # type: ignore[return-value]


class _FakeRelayPort:
    """Fake `OpenClawRelayPort` — records calls and returns canned frames."""

    def __init__(self) -> None:
        self.forward_request_calls: list[dict[str, Any]] = []
        self.forward_raw_frame_calls: list[dict[str, Any]] = []
        self._response: ResponseFrame = ResponseFrame(
            id="req-1", ok=True, payload={"relayed": True}
        )

    async def forward_request(
        self,
        request_id: str,
        method: str,
        params: dict[str, Any] | None = None,
        token: str | None = None,
        timeout: float = 30.0,
    ) -> ResponseFrame:
        self.forward_request_calls.append(
            {
                "request_id": request_id,
                "method": method,
                "params": params,
                "token": token,
                "timeout": timeout,
            }
        )
        return self._response

    async def forward_raw_frame(
        self,
        frame: dict[str, Any],
        token: str | None = None,
    ) -> None:
        self.forward_raw_frame_calls.append({"frame": frame, "token": token})


@pytest.mark.asyncio
async def test_forward_request_passes_token_from_auth():
    port = _FakeRelayPort()
    adapter = OpenClawRelayAdapter(port)
    result = await adapter.forward_request(
        "req-1", "some.method", {"key": "val"}, auth=_auth("tok123")
    )
    assert result.ok is True
    assert result.payload == {"relayed": True}
    assert len(port.forward_request_calls) == 1
    call = port.forward_request_calls[0]
    assert call["request_id"] == "req-1"
    assert call["method"] == "some.method"
    assert call["params"] == {"key": "val"}
    assert call["token"] == "tok123"
    assert call["timeout"] == 30.0


@pytest.mark.asyncio
async def test_forward_request_no_auth_passes_none_token():
    port = _FakeRelayPort()
    adapter = OpenClawRelayAdapter(port)
    await adapter.forward_request("req-2", "other.method", None)
    call = port.forward_request_calls[0]
    assert call["token"] is None
    assert call["params"] is None


@pytest.mark.asyncio
async def test_forward_request_custom_timeout():
    port = _FakeRelayPort()
    adapter = OpenClawRelayAdapter(port)
    await adapter.forward_request("req-3", "m", None, auth=_auth("t"), timeout=60.0)
    assert port.forward_request_calls[0]["timeout"] == 60.0


@pytest.mark.asyncio
async def test_forward_raw_frame_passes_token_from_auth():
    port = _FakeRelayPort()
    adapter = OpenClawRelayAdapter(port)
    frame = {"type": "event", "event": "tick"}
    await adapter.forward_raw_frame(frame, auth=_auth("tok-abc"))
    assert len(port.forward_raw_frame_calls) == 1
    call = port.forward_raw_frame_calls[0]
    assert call["frame"] == frame
    assert call["token"] == "tok-abc"


@pytest.mark.asyncio
async def test_forward_raw_frame_no_auth_passes_none_token():
    port = _FakeRelayPort()
    adapter = OpenClawRelayAdapter(port)
    await adapter.forward_raw_frame({"type": "ping"})
    assert port.forward_raw_frame_calls[0]["token"] is None


@pytest.mark.asyncio
async def test_forward_request_returns_error_frame_as_is():
    """Adapter passes error ResponseFrames through unchanged."""
    port = _FakeRelayPort()
    port._response = ResponseFrame(
        id="req-err",
        ok=False,
        error=ErrorShape(code="NOT_FOUND", message="method unknown"),
    )
    adapter = OpenClawRelayAdapter(port)
    result = await adapter.forward_request("req-err", "missing.method", None)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "NOT_FOUND"
