"""OpenClawChatPort — native port for chat streaming and abort operations.

Chat is pooled (client+pool), so port methods take `token: str | None = None`
for per-token routing. `chat_stream` returns an `AsyncIterator[EventFrame]`
— the gateway loop, event-name derivation, and sessionKey injection all happen
inside the impl, so the adapter sees fully-formed EventFrames. `chat_abort`
returns a raw dict (`{success, error, payload}`) — the adapter builds
`ChatAbortResult`.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from engine.community.kernel.frames import ErrorShape, EventFrame, ResponseFrame


@dataclass(frozen=True)
class OpenClawChatAck:
    """Upstream acknowledgement; cached is inferred, not an upstream wire field."""

    ok: bool
    payload: dict[str, Any]
    error: ErrorShape | None = None

    def response(self, request_id: str) -> ResponseFrame:
        status = self.payload.get("status")
        cached = None
        if self.ok:
            if status == "started":
                cached = False
            elif status in ("in_flight", "ok"):
                cached = True
        return ResponseFrame(
            id=request_id,
            ok=self.ok,
            payload={**self.payload, "accepted": self.ok, "cached": cached},
            error=self.error,
        )


ChatAckCallback = Callable[[OpenClawChatAck], Awaitable[None]]


class OpenClawChatPort(Protocol):
    """Native chat operations over the OpenClaw gateway."""

    async def chat_stream(
        self,
        session_key: str,
        message: str,
        timeout_ms: int | None = None,
        idempotency_key: str | None = None,
        attachments: list[Any] | None = None,
        token: str | None = None,
        on_ack: ChatAckCallback | None = None,
    ) -> AsyncGenerator[EventFrame, None]:
        """Stream chat events from the OpenClaw gateway as EventFrames.

        Relocates the gateway loop from `engines/openclaw/chat.py` —
        sessionKey injection, event-name derivation, stop-state break, and the
        `inject-` runId skip all happen inside the impl. Transport errors
        (ConnectionError / other Exception) are NOT caught here — they propagate
        to the adapter, which converts them to error EventFrames.

        Args:
            session_key: Pre-composed OpenClaw session key.
            message: The user's message text.
            timeout_ms: Optional alive-time override forwarded to the gateway.
            idempotency_key: Optional idempotency key extracted from extraParams.
            attachments: Optional attachments list extracted from extraParams.
            token: MCP token for per-token pool routing; None → default client.

        Yields:
            EventFrame instances matching the OpenClaw event vocabulary.

        If on_ack is supplied, await it before yielding events. Failed or
        completed-replay ACKs end the stream without waiting for new events.
        Closing the stream must release its upstream listeners.
        """
        ...

    async def chat_abort(
        self,
        session_key: str,
        run_id: str,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Cancel an in-flight chat run via the OpenClaw gateway.

        Returns the raw `{success, error, payload}` dict from the gateway.
        On connection/RPC exceptions returns a synthetic failure dict so the
        adapter always receives a uniform dict rather than an exception.

        Args:
            session_key: The session whose run should be aborted.
            run_id: The run id to cancel.
            token: MCP token for per-token pool routing; None → default client.

        Returns:
            Raw dict with keys: ``success`` (bool), ``error`` (dict|None),
            ``payload`` (dict|None).
        """
        ...

    async def chat_inject(
        self,
        session_key: str,
        message: str,
        label: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Inject a chat message through the OpenClaw gateway without running the agent.

        Returns the raw normalized dict from the port. Successful payloads may
        include ``messageId`` from ``chat.inject`` and ``sessionId`` from
        ``sessions.describe`` so higher layers can repair the transcript role.
        """
        ...


__all__ = ["OpenClawChatPort"]
