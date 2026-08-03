"""Service API for first-time public Local Skill uploads."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LocalSkillUploadServiceProtocol(Protocol):
    """Create or safely replace one Bot-owned Local Skill from a ZIP package."""

    async def upload_local_skill(
        self, *, bot_id: str, owner_id: str, actor_id: str, package: bytes
    ) -> dict[str, Any]: ...
