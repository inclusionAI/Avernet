"""User-facing Runtime Projection recovery guidance."""

from __future__ import annotations

import pytest

from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ResolvedSkillPlan,
    RuntimeProjectionResult,
)
from agentclaw.community.core.skill_center.runtime_resolver import (
    RuntimeProjectionResolver,
)
from agentclaw.community.core.skill_center.services.runtime_projections.skill_runtime_delivery import (
    SkillRuntimeDelivery,
)
from agentclaw.community.core.skills_pool.models import (
    MappingItemResult,
    MappingProjectionStatus,
    MappingPublishResult,
    MappingVerificationResult,
    RegisteredSkillAsset,
)


class _MappingResultRuntime:
    def __init__(self, code: str) -> None:
        self.item = MappingItemResult(
            target="/runtime/active/repo-skill",
            source="/runtime/repo/repo-skill",
            status=MappingProjectionStatus.DEGRADED,
            code=code,
        )

    async def publish_mappings(self, **_kwargs):
        return MappingPublishResult(
            published=False,
            status=MappingProjectionStatus.DEGRADED,
            items=(self.item,),
        )

    async def apply_mappings(self, **_kwargs):
        from agentclaw.community.core.skills_pool.runtime import (
            LegacyMappingApplyRequired,
        )

        raise LegacyMappingApplyRequired()

    async def verify_mappings(self, **_kwargs):
        return MappingVerificationResult(
            valid=False,
            status=MappingProjectionStatus.DEGRADED,
            items=(self.item,),
        )


class _MissingLayouts:
    def get(self, _scope):
        return None


class _UnusedLegacyService:
    async def project_skills(self, **_kwargs):
        raise AssertionError("Repo mapping must not use Legacy DeviceSync")


class _UnusedFactory:
    def create(self, **_kwargs):
        raise AssertionError("Repo mapping must not construct Legacy DeviceSync")


@pytest.mark.parametrize(
    ("code", "expected_action"),
    [
        (
            "MANAGED_SOURCE_MISSING",
            "该技能的内容暂时不可用。请重新同步或重新添加该技能后，再保存能力集。",
        ),
        (
            "UNMANAGED_ACTIVE_ENTRY_RETAINED",
            "该技能已在 Bot 内被手动维护。为避免覆盖现有内容，平台没有替换它。"
            "请联系 Bot 管理员确认处理后，再保存能力集。",
        ),
        (
            "EXTERNAL_ACTIVE_ENTRY_RETAINED",
            "该技能当前由其他配置管理，平台没有修改它。请联系 Bot 管理员确认"
            "是否交由平台管理后，再保存能力集。",
        ),
        (
            "UNKNOWN_MAPPING_FAILURE",
            "部分技能未完成运行时同步。请稍后再次保存能力集；若持续失败，"
            "请联系管理员并提供错误详情。",
        ),
    ],
)
@pytest.mark.asyncio
async def test_mapping_message_exposes_complete_user_action(
    code: str,
    expected_action: str,
) -> None:
    asset = RegisteredSkillAsset(
        skill_id=7,
        name="repo-skill",
        git_path="git://team/repo-skill",
    )
    plan = ResolvedSkillPlan(
        bot_id="bot-1",
        owner_id="owner-1",
        bot={
            "env": "pre",
            "entity_id": "owner-1",
            "active_engine": "openclaw",
        },
        engine="openclaw",
        projection=RuntimeProjectionResolver().resolve_skills((asset,)),
    )
    delivery = SkillRuntimeDelivery(
        pool_runtime=_MappingResultRuntime(code),
        pool_layouts=_MissingLayouts(),
    )

    result = await delivery.deliver(plan=plan, service_factory=_UnusedFactory())

    assert result.issues[0].suggested_action == expected_action


def test_pending_runtime_action_is_exposed_to_the_caller() -> None:
    result = RuntimeProjectionResult.pending(
        code="RUNTIME_SNAPSHOT_UNAVAILABLE",
        reason="Bot 运行环境当前不可连接，能力状态已保存但尚未同步",
        suggested_action="Bot 当前不可连接或仍在启动。能力集已保存；待 Bot 恢复后，请再次保存能力集完成同步。",
    )

    assert result.issues[0].suggested_action == (
        "Bot 当前不可连接或仍在启动。能力集已保存；待 Bot 恢复后，请再次保存能力集完成同步。"
    )
