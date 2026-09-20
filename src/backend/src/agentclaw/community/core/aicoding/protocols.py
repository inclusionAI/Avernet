"""Protocols for AICoding-specific services."""
from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class AicodingBotResolutionServiceProtocol(Protocol):
    """Service API for resolving the real bot owner in AICoding DIMA flows."""

    def resolve_bot_for_dima_workspace(
        self,
        bot_id: str,
        requested_owner_id: str,
        operator_id: str,
        env: str,
    ) -> Dict[str, Any] | None: ...



@runtime_checkable
class AicodingHostedWorkspaceServiceProtocol(Protocol):
    """Service API for idempotently ensuring a hosted workspace for a Coding bot.

    只供 AICoding 的托管工作空间兜底接口使用；非 aicoding 逻辑不应依赖该协议。
    """

    def ensure_hosted_workspace(self, bot_id: str, user_id: str) -> Optional[str]: ...
