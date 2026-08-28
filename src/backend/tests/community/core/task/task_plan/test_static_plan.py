from pathlib import Path

import pytest

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.task_plan.static_plan import StaticPlanDefinition


TEMPLATE_DIR = Path(__file__).parents[7] / "src" / "backend" / "src" / "agentclaw" / "community" / "configs" / "task-plans"


def test_okr_template_declares_parallel_branches_and_join():
    plan = StaticPlanDefinition.from_file("okr-implementation", TEMPLATE_DIR)

    assert plan.template_id == "okr-implementation"
    assert plan.nodes[0].depends_on == ()
    assert plan.nodes[1].depends_on == ()
    assert plan.nodes[2].depends_on == ("risk_assessment", "marketing_strategy")
    assert plan.nodes[3].depends_on == ("strategy_approval",)


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
