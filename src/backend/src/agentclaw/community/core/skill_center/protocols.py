"""Narrow core-owned contracts shared by Skill Center domain services."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SpaceSkillEditorAccessProtocol(Protocol):
    """Canonical OWNER/MANAGER authorization seam for Draft commands."""

    def require_editor(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> str: ...


__all__ = ["SpaceSkillEditorAccessProtocol"]
