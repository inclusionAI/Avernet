"""Private provenance for WS failures, separate from public error text/frames."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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


def ack_error_code(ack: dict[str, Any]) -> str | None:
    if not isinstance(ack, WsFrame) or ack.get("ok") is not False:
        return None
    error = ack.get("error")
    if not isinstance(error, dict):
        return None
    return _nonblank_string(error.get("code"))


def event_matches_ack(event: WsFrame, ack: WsFrame | None, session_key: str) -> bool:
    # COSEC: a legacy fuzzy session match is not proof of run ownership. The
    # correlated ACK and terminal must name the same attempt on the same socket.
    if (
        ack is None
        or ack.connection is not event.connection
        or ack.get("ok") is not True
    ):
        return False
    payload = ack.get("payload")
    if not isinstance(payload, dict) or payload.get("cached") is True:
        return False
    if payload.get("status") != "started":
        return False
    run_id = _nonblank_string(payload.get("runId"))
    if (
        run_id is None
        or run_id.startswith("inject-")
        or run_id != event.get("runId", event.get("run_id"))
        or event.get("stopReason") == "inject"
    ):
        return False
    canonical_key = _nonblank_string(payload.get("sessionKey"))
    event_key = event.get("sessionKey")
    # OpenClaw can emit the agent:main: form even without an ACK alias.
    # Do not reuse the legacy arbitrary substring match for code attribution.
    if not isinstance(event_key, str):
        return False
    if event_key == canonical_key:
        return True
    return event_key.removeprefix("agent:main:") == session_key.removeprefix(
        "agent:main:"
    )


def event_error_code(event: WsFrame) -> str | None:
    return _nonblank_string(event.get("errorCode"))


def _nonblank_string(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


@dataclass(frozen=True, slots=True)
class WsErrorChunk(StreamChunk):
    """Private error code; SSE still uses the original content and metadata."""

    error_code: str | None = field(default=None, repr=False)
    pending_frame: WsFrame | None = field(default=None, repr=False)

    def resolve_code(self, ack: WsFrame | None, session_key: str) -> str | None:
        if self.pending_frame is None:
            return self.error_code
        if event_matches_ack(self.pending_frame, ack, session_key):
            return event_error_code(self.pending_frame)
        return None
