"""Service API for Local Skill create-or-replace uploads."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LocalSkillUploadServiceProtocol(Protocol):
    """Create or safely replace one Bot-owned Local Skill from a complete package."""

    async def upload_local_skill(
        self, *, bot_id: str, owner_id: str, actor_id: str, package: bytes
    ) -> dict[str, Any]: ...

    async def upload_local_skill_files(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        files: Sequence[tuple[str, bytes]],
    ) -> dict[str, Any]:
        """Create or replace from one browser-selected directory's files.

        Each tuple is ``(relative_path, content)``.  The Service API owns the
        conversion to the same validated package contract as raw ZIP upload.
        """
        ...
