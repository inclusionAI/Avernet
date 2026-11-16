"""Select per-mutation projection at the delivery boundary."""

from __future__ import annotations

from agentclaw.community.core.apply_engine.skill_set_apply_port import SkillSetApplyPort
from agentclaw.community.core.skill_center.skill_set_apply import (
    BotCapabilityScope,
    DefaultExclusionChange,
    ResolvedSkillSetWrite,
    SkillSetApplyResult,
    SkillSetApplySnapshot,
)
from agentclaw.community.core.skill_center.skill_set_management_service_protocol import (
    SkillSetManagementServiceProtocol,
)


class _SkillSetApply(SkillSetApplyPort):
    _PROJECT: bool

    def __init__(self, inner: SkillSetManagementServiceProtocol) -> None:
        self._inner = inner

    def read_apply_snapshot(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
    ) -> SkillSetApplySnapshot:
        return self._inner.read_apply_snapshot(
            bot_id=bot_id,
            owner_id=owner_id,
            actor_id=actor_id,
        )

    async def replace_apply_skill_sets(
        self,
        *,
        scope: BotCapabilityScope,
        actor_id: str,
        sets: tuple[ResolvedSkillSetWrite, ...],
        remove_names: tuple[str, ...],
        default_change: DefaultExclusionChange,
        platform_default_codes: frozenset[str],
    ) -> SkillSetApplyResult:
        return await self._inner.replace_apply_skill_sets(
            scope=scope,
            actor_id=actor_id,
            sets=sets,
            remove_names=remove_names,
            default_change=default_change,
            platform_default_codes=platform_default_codes,
            project=self._PROJECT,
        )


class DeviceSkillSetApply(_SkillSetApply):
    _PROJECT = True


class PlatformSkillSetApply(_SkillSetApply):
    _PROJECT = False
