"""Apply's read-only capability facts and bulk Set command, without delivery policy."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.skill_set_apply import (
    BotCapabilityScope,
    DefaultExclusionChange,
    ResolvedSkillSetWrite,
    SkillSetApplyResult,
    SkillSetApplySnapshot,
)


@runtime_checkable
class SkillSetApplyPort(Protocol):
    @abstractmethod
    def read_apply_snapshot(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
    ) -> SkillSetApplySnapshot: ...

    @abstractmethod
    async def replace_apply_skill_sets(
        self,
        *,
        scope: BotCapabilityScope,
        actor_id: str,
        sets: tuple[ResolvedSkillSetWrite, ...],
        remove_names: tuple[str, ...],
        default_change: DefaultExclusionChange,
        platform_default_codes: frozenset[str],
    ) -> SkillSetApplyResult: ...
