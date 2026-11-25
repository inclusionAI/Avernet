"""Private provenance for WS failures, separate from public error text/frames."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from secbaas.community.api.sse import StreamChunk


class WsFrame(dict[str, Any]):
    """A decoded WS mapping; connection identity is never part of its JSON keys."""

    def __init__(
        self,
        fields: dict[str, Any],
        connection: object,
        *,
        awaiting_ack: tuple[str, ...] = (),
    ) -> None:
        super().__init__(fields)
        self.connection = connection
        self.awaiting_ack = awaiting_ack


@dataclass(frozen=True, slots=True)
class WsFailure:
    code: str | None = field(repr=False)


def failure_for_ack(ack: dict[str, Any]) -> WsFailure | None:
    if not isinstance(ack, WsFrame) or ack.get("ok") is not False:
        return None
    error = ack.get("error")
    return WsFailure(
        _nonblank_string(error.get("code")) if isinstance(error, dict) else None
    )


def failure_for_event(
    event: WsFrame, ack: WsFrame | None, session_key: str
) -> WsFailure | None:
    # COSEC: a legacy fuzzy session match is not proof of run ownership. The
    # correlated ACK and terminal must name the same attempt on the same socket.
    if (
        ack is None
        or ack.connection is not event.connection
        or ack.get("ok") is not True
    ):
        return None
    payload = ack.get("payload")
    if not isinstance(payload, dict) or payload.get("cached") is True:
        return None
    if payload.get("status") != "started":
        return None
    run_id = _nonblank_string(payload.get("runId"))
    if (
        run_id is None
        or run_id.startswith("inject-")
        or run_id != event.get("runId", event.get("run_id"))
        or event.get("stopReason") == "inject"
    ):
        return None
    canonical_key = _nonblank_string(payload.get("sessionKey"))
    event_key = event.get("sessionKey")
    # OpenClaw can emit the agent:main: form even without an ACK alias.
    # Do not reuse the legacy arbitrary substring match for code attribution.
    if not isinstance(event_key, str) or (
        event_key != canonical_key
        and event_key.removeprefix("agent:main:")
        != session_key.removeprefix("agent:main:")
    ):
        return None
    return WsFailure(_nonblank_string(event.get("errorCode")))


def _nonblank_string(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


@dataclass(frozen=True, slots=True, kw_only=True)
class PendingWsErrorChunk(StreamChunk):
    """Already delivered to the legacy session; only its code waits for ACK."""

    frame: WsFrame = field(repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class WsErrorChunk(StreamChunk):
    """An owned failure; converters still see the original content and metadata."""

    failure: WsFailure = field(repr=False)


class FailureUpdate(TypedDict, total=False):
    error_code: str
    queue_claim: tuple[str, str]


def failure_update(failure: WsFailure | None) -> FailureUpdate:
    if failure is not None and failure.code is not None:
        return {"error_code": failure.code}
    return {}
