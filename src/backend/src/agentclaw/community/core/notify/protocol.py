"""Protocol for listing bots eligible for notification polling."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class NotifyBotLister(Protocol):
    """Returns (bot_id, bot_name, sandbox_id) tuples for notification polling."""

    def list_bot_mappings(self, user_id: str) -> list[tuple[str, str, str]]:
        ...
