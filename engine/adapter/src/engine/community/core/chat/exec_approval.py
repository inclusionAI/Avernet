"""Optional exec-only A2A contract, separate from the native ChatService.

An opt-in implementation guarantees that stream_for_a2a emits only session-bound
exec interactions with authentic IDs and expiry, and resolves those same IDs.
It must not advertise the legacy full resolve contract (ask_user/mode_switch).
Native stream() and other engines are unaffected.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from engine.community.core.chat.models import ChatRequest
from engine.community.core.engine.context import AuthContext
from engine.community.kernel.frames import EventFrame


class ExecApprovalChatService(Protocol):
    @property
    def a2a_exec_approvals(self) -> bool: ...

    def stream_for_a2a(self, request: ChatRequest,
                       auth: AuthContext | None = None) -> AsyncIterator[EventFrame]: ...

    async def resolve_exec_approval(self, *, session_key: str, interaction_id: str,
                                    decision: str, message: str | None = None) -> dict[str, Any]: ...
