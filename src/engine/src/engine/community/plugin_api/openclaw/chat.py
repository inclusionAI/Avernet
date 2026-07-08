"""OpenClawChatPort — native port for chat streaming and abort operations.

Chat is pooled (client+pool), so port methods take `token: str | None = None`
for per-token routing. `chat_stream` returns an `AsyncIterator[EventFrame]`
— the gateway loop, event-name derivation, and sessionKey injection all happen
inside the impl, so the adapter sees fully-formed EventFrames. `chat_abort`
returns a raw dict (`{success, error, payload}`) — the adapter builds
`ChatAbortResult`.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, Protocol

from engine.community.kernel.frames import EventFrame


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


__all__ = ["OpenClawChatPort"]
