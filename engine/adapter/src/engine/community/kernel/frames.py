"""
Engine wire-protocol envelope types.

The OpenClaw engine protocol (inherited from Moltis Gateway v3) uses three
frame types over a WebSocket connection:

  - RequestFrame  — client → server RPC request
  - ResponseFrame — server → client RPC response
  - EventFrame    — server → client asynchronous push (chat streams, approvals,
                    ticks, etc.)

Plugin Protocols that expose streaming results (e.g. ChatService.stream) yield
EventFrames because a single request may produce heterogeneous events whose
types are not known ahead of time. Plugins that expose RPC-style calls
(SessionService.list, CronService.list_jobs, …) use typed domain objects at the
Python level; the RequestFrame/ResponseFrame envelope is an implementation
detail hidden inside each plugin.

All types here are @dataclass — cheap, dict-interchangeable, no Pydantic dep.

These frames live in `kernel/` (not `core/`) because they are produced by engine
transport (in `plugins/`) and consumed by `core` internals: since `plugins` must
not import `core` (the ACL leaf rule), the shared primitive lives below both.
Import from `engine.community.kernel.frames`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# ── Constants ────────────────────────────────────────────────────────────────

PROTOCOL_VERSION = 3
MAX_PAYLOAD_BYTES = 524_288  # 512 KB
MAX_BUFFERED_BYTES = 1_572_864  # 1.5 MB
TICK_INTERVAL_MS = 30_000  # 30s
HANDSHAKE_TIMEOUT_MS = 10_000  # 10s


# ── Errors ───────────────────────────────────────────────────────────────────


class ErrorCodes:
    """Well-known error codes returned in ResponseFrame.error.code."""

    NOT_LINKED = "NOT_LINKED"
    NOT_PAIRED = "NOT_PAIRED"
    AGENT_TIMEOUT = "AGENT_TIMEOUT"
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAVAILABLE = "UNAVAILABLE"
    # Raised by AiCodingWSServer when a client dispatches an RPC whose
    # method name isn't in the server's route table (see
    # ``websocket_server.py::_handle_request``). Added alongside the
    # route-B tests that caught an ``AttributeError`` referencing this
    # constant — the previous value was never defined and the fallback
    # branch crashed with ``INTERNAL_ERROR`` instead of the intended
    # NOT_FOUND diagnostic.
    NOT_FOUND = "NOT_FOUND"


@dataclass
class ErrorShape:
    """Error structure inside ResponseFrame.error."""

    code: str
    message: str
    details: Any | None = None
    retryable: bool | None = None
    retry_after_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            d["details"] = self.details
        if self.retryable is not None:
            d["retryable"] = self.retryable
        if self.retry_after_ms is not None:
            d["retryAfterMs"] = self.retry_after_ms
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ErrorShape:
        return cls(
            code=data.get("code", "UNKNOWN"),
            message=data.get("message", "Unknown error"),
            details=data.get("details"),
            retryable=data.get("retryable"),
            retry_after_ms=data.get("retryAfterMs"),
        )


# ── Frames ───────────────────────────────────────────────────────────────────


@dataclass
class RequestFrame:
    """Client → server RPC request."""

    id: str
    method: str
    params: dict[str, Any] | None = None
    type: str = "req"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type, "id": self.id, "method": self.method}
        if self.params is not None:
            d["params"] = self.params
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RequestFrame:
        return cls(
            id=data["id"],
            method=data["method"],
            params=data.get("params"),
            type=data.get("type", "req"),
        )


@dataclass
class ResponseFrame:
    """Server → client RPC response."""

    id: str
    ok: bool
    payload: Any | None = None
    error: ErrorShape | None = None
    type: str = "res"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type, "id": self.id, "ok": self.ok}
        if self.payload is not None:
            d["payload"] = self.payload
        if self.error is not None:
            d["error"] = self.error.to_dict()
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def ok_response(cls, id: str, payload: Any) -> ResponseFrame:
        return cls(id=id, ok=True, payload=payload)

    @classmethod
    def err_response(cls, id: str, error: ErrorShape) -> ResponseFrame:
        return cls(id=id, ok=False, error=error)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResponseFrame:
        error = None
        if data.get("error"):
            error = ErrorShape.from_dict(data["error"])
        return cls(
            id=data["id"],
            ok=data["ok"],
            payload=data.get("payload"),
            error=error,
            type=data.get("type", "res"),
        )


@dataclass
class StateVersion:
    """Optional version markers nested inside EventFrame."""

    presence: int | None = None
    health: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.presence is not None:
            d["presence"] = self.presence
        if self.health is not None:
            d["health"] = self.health
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StateVersion:
        return cls(
            presence=data.get("presence"),
            health=data.get("health"),
        )


@dataclass
class EventFrame:
    """Server → client asynchronous event.

    `event` is the event-name vocabulary (e.g. "agent", "chat",
    "approval.requested", "approval.resolved", "tick"). `payload` is the
    event-specific body. Consumers dispatch on `event` to decide how to read
    `payload`.

    Streaming plugin Protocols (ChatService.stream, …) yield EventFrames so
    the caller sees the full event vocabulary rather than a pre-flattened
    domain shape.
    """

    event: str
    payload: Any | None = None
    seq: int | None = None
    state_version: StateVersion | None = None
    type: str = "event"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type, "event": self.event}
        if self.payload is not None:
            d["payload"] = self.payload
        if self.seq is not None:
            d["seq"] = self.seq
        if self.state_version is not None:
            d["stateVersion"] = self.state_version.to_dict()
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventFrame:
        state_version = None
        if data.get("stateVersion"):
            state_version = StateVersion.from_dict(data["stateVersion"])
        return cls(
            event=data["event"],
            payload=data.get("payload"),
            seq=data.get("seq"),
            state_version=state_version,
            type=data.get("type", "event"),
        )


__all__ = [
    "PROTOCOL_VERSION",
    "MAX_PAYLOAD_BYTES",
    "MAX_BUFFERED_BYTES",
    "TICK_INTERVAL_MS",
    "HANDSHAKE_TIMEOUT_MS",
    "ErrorCodes",
    "ErrorShape",
    "RequestFrame",
    "ResponseFrame",
    "EventFrame",
    "StateVersion",
]
