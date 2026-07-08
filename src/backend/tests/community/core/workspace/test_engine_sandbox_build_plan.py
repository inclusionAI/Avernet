"""EngineBuildPlan 数据类字段约束测试。

覆盖本次为 claude_code 引擎引入的 ``extra_sync_*`` 字段——它们必须保留默认
空字符串，这样 openclaw 等不需要 extra sync 的引擎依旧可以省略，
BotBuildService 据此 short-circuit 跳过额外的 rsync。
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.workspace.engine_sandbox import EngineBuildPlan


def _minimal_plan(**overrides) -> EngineBuildPlan:
    """构造一个只填必填字段的 EngineBuildPlan，便于校验默认值。"""
    base = dict(
        engine_type="test",
        source_root_name=".test",
        migration_subpath="test",
        workspace_subdir="workspace",
        mcp_config_relpath="workspace/config/mcporter.json",
        skill_source_relpath="workspace/skills",
        skill_target_relpath="workspace/skills",
        rsync_excludes=[],
    )
    base.update(overrides)
    return EngineBuildPlan(**base)


@pytest.mark.unit
class TestEngineBuildPlanExtraSync:
    def test_extra_sync_fields_default_to_empty_string(self):
        plan = _minimal_plan()

        assert plan.extra_sync_source_relpath == ""
        assert plan.extra_sync_target_relpath == ""

    def test_extra_sync_fields_are_assignable(self):
        plan = _minimal_plan(
            extra_sync_source_relpath=".claude",
            extra_sync_target_relpath="claude",
        )

        assert plan.extra_sync_source_relpath == ".claude"
        assert plan.extra_sync_target_relpath == "claude"

    def test_plan_is_frozen(self):
        plan = _minimal_plan()

        with pytest.raises(Exception):
            plan.extra_sync_source_relpath = "mutated"  # type: ignore[misc]
