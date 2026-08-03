"""Service API for public Local Skill desired-state commands."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LocalSkillStateServiceProtocol(Protocol):
    """Synchronously set one Bot-owned Local Skill's desired state."""

    async def set_local_skill_active(
        self, *, skill_id: str, actor_id: str, active: bool
    ) -> dict[str, Any]: ...
