"""Service API for authoritative Human-to-Bot friendship reads."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable


@runtime_checkable
class HumanBotFriendshipServiceProtocol(Protocol):
    """Read Human-to-Bot friendship from BCN without exposing BCN DTOs."""

    def is_friend(
        self,
        *,
        human_id: str,
        bot_id: str,
        owner_id: str,
        request_headers: Mapping[str, str],
    ) -> bool: ...
