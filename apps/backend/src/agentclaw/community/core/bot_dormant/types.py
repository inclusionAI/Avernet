"""Value objects shared by dormant Bot lifecycle operations."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BotLifecycleResult:
    """Current lifecycle state after applying an owner operation."""

    bot_id: str
    owner_id: str
    status: str
    changed: bool
