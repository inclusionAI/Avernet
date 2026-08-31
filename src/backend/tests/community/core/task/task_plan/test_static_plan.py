from pathlib import Path

import pytest

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.task_plan.static_plan import StaticPlanDefinition


TEMPLATE_DIR = Path(__file__).parents[7] / "src" / "backend" / "src" / "agentclaw" / "community" / "core" / "task" / "task_plan" / "plans"


def test_okr_template_declares_parallel_branches_and_join():
    plan = StaticPlanDefinition.from_file("okr-implementation", TEMPLATE_DIR)
    by_id = {n.node_id: n for n in plan.nodes}

    assert plan.template_id == "okr-implementation"
    # 两条根并行支:风险评估群 / 大促营销策略 均无前置
    assert by_id["risk_assessment"].depends_on == ()
    assert by_id["marketing_strategy"].depends_on == ()
    # 营销策略扇出→(圈人、选品),后与风险结果四路在 strategy_approval 汇合(join)
    assert set(by_id["crowd_selection"].depends_on) == {"marketing_strategy"}
    assert set(by_id["product_selection"].depends_on) == {"marketing_strategy"}
    assert by_id["strategy_approval"].depends_on == (
        "risk_assessment", "marketing_strategy", "crowd_selection", "product_selection",
    )
    # 实施依赖审核;notify 终端节点依赖实施并发钉钉通知(无 bot 绑定,validate_bindings 豁免)
    assert by_id["implementation"].depends_on == ("strategy_approval",)
    assert by_id["notify_done"].depends_on == ("implementation",)
    assert by_id["notify_done"].node_type == "notify"
    plan.validate_bindings()  # notify 无 bot_id 不应触发 missing


def test_static_plan_rejects_missing_required_input():
    plan = StaticPlanDefinition.from_file("okr-implementation", TEMPLATE_DIR)

    with pytest.raises(ValueError, match="missing static plan input: okr"):
        plan.validate_input({})


def test_static_plan_rejects_unbound_bots_before_run():
    text = (TEMPLATE_DIR / "okr-implementation.yaml").read_text(encoding="utf-8")
    plan = StaticPlanDefinition.from_yaml(text.replace("bot_id: 20260828_whd6nx7x", "bot_id: "))

    with pytest.raises(TaskStateError, match="template bot binding missing"):
        plan.validate_bindings()


def test_static_plan_rejects_cycles():
    with pytest.raises(ValueError, match="dependency cycle"):
        StaticPlanDefinition.from_yaml(
            """
            template_id: cycle
            nodes:
              - id: a
                depends_on: [b]
              - id: b
                depends_on: [a]
            """
        )
