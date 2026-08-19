"""Authorization boundary for Bot-scoped SkillSet commands."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SkillSetAccessProtocol(Protocol):
    """Resolve a Bot only when its owner or collaborator may operate it."""

    def resolve_bot(self, *, bot_id: str, actor_id: str) -> dict[str, Any]: ...
