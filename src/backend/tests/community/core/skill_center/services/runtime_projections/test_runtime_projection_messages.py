"""User-facing Runtime Projection recovery guidance."""

from __future__ import annotations

import pytest

from agentclaw.community.core.skill_center.runtime_projection_contract import (
    RuntimeProjectionResult,
)
from agentclaw.community.core.skill_center.services.runtime_projections.per_domain import (
    PerDomainRuntimeProjection,
)


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
def test_mapping_message_exposes_complete_user_action(
    code: str,
    expected_action: str,
) -> None:
    _, _, suggested_action, _, _ = PerDomainRuntimeProjection._mapping_message(code)

    assert suggested_action == expected_action


def test_pending_runtime_action_is_exposed_to_the_caller() -> None:
    result = RuntimeProjectionResult.pending(
        code="RUNTIME_SNAPSHOT_UNAVAILABLE",
        reason="Bot 运行环境当前不可连接，能力状态已保存但尚未同步",
        suggested_action="Bot 当前不可连接或仍在启动。能力集已保存；待 Bot 恢复后，请再次保存能力集完成同步。",
    )

    assert result.issues[0].suggested_action == (
        "Bot 当前不可连接或仍在启动。能力集已保存；待 Bot 恢复后，请再次保存能力集完成同步。"
    )
