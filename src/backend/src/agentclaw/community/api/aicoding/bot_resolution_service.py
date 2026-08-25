"""Service API Protocol for AICoding DIMA bot resolution."""
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
    ) -> Optional[Dict[str, Any]]: ...
