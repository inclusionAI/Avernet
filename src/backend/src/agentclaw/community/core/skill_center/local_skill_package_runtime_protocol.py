"""Service API for package-level Local Skill Runtime delivery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class LocalSkillPackageRuntimeResult:
    skill_name: str
    action: str
    content_digest: str
    target_path: str | None = None


@runtime_checkable
class LocalSkillPackageRuntimeProtocol(Protocol):
    async def apply(
        self,
        *,
        bot_id: str,
        owner_id: str,
        skill_name: str,
        layout: str,
        package: bytes,
    ) -> LocalSkillPackageRuntimeResult | None:
        """Apply through the new API, or return None for safe legacy fallback."""
        ...


__all__ = ["LocalSkillPackageRuntimeProtocol", "LocalSkillPackageRuntimeResult"]
