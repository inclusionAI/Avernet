"""
ChatService Protocol — chat plugin interface.

Each engine implementation under engines/<name>/chat.py provides a class that
structurally satisfies this Protocol. EngineManager exposes the active engine's
chat plugin via `EngineManager.get_instance().chat`.

`stream()` yields `EventFrame` values — the same wire envelope the engine
pushes to the frontend — so there is a single event vocabulary across engines
(`agent`, `chat`, `approval.requested`, `approval.resolved`, `tick`, …). The
caller dispatches on `EventFrame.event` to decide how to read the payload.

Approval handling is intentionally not part of ChatService; it lives on the
ApprovalService (added in a later milestone) so engines can declare and
implement approval orthogonally to chat.

Non-streaming completion (`complete()`) was dropped in the M0 revision — no
production path consumed it, and no engine implemented it. When a future
engine actually supports non-streaming completion, re-add the method with an
explicit `CHAT_COMPLETE` capability gate.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from engine.community.core.chat.models import ChatAbortRequest, ChatAbortResult, ChatRequest
from engine.community.core.engine.context import AuthContext
from engine.community.kernel.frames import EventFrame


@runtime_checkable
class ChatService(Protocol):
    """Backend talks to the chat engine through this Protocol."""

    async def stream(
        self,
        request: ChatRequest,
        auth: AuthContext | None = None,
    ) -> AsyncIterator[EventFrame]:
        """Stream engine events for the given chat request.

        Implementations yield EventFrames matching the OpenClaw-protocol
        vocabulary (`agent`, `chat`, `approval.requested`, …). Engines that
        speak the protocol natively (e.g. OpenClaw) can passthrough; engines
        that speak a different underlying protocol translate into this shape.

        `auth` carries the MCP token (or other principal) the server extracted
        from the inbound connection. Plugins that pool upstream connections
        per-tenant use it to pick the right client; plugins that don't need
        per-user isolation can ignore it.
        """
        ...

    async def abort(
        self,
        request: ChatAbortRequest,
        auth: AuthContext | None = None,
    ) -> ChatAbortResult:
        """Cancel an in-flight chat run.

        Returns a structured result describing whether a run was actually
        aborted, plus any follow-up `EventFrame`s the server should relay to
        the frontend after the response frame (OpenClaw synthesizes a
        `state=aborted` chat event on successful cancellation).

        Failures to reach the engine are reported via `ok=False` + `error`
        rather than exceptions, mirroring the in-band error shape of the
        underlying `chat.abort` RPC.
        """
        ...


__all__ = ["ChatService"]
