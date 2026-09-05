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


# ── bot binding placeholder resolution (merchant template role keys) ─────────


def test_merchant_template_uses_role_placeholders_not_literal_uuids():
    """The public OSS template carries role-key placeholders, not internal uuids."""
    plan = StaticPlanDefinition.from_file("merchant-operations-goal-to-plan", TEMPLATE_DIR)
    assert plan.entry_bot_id == "${store_owner_bot_id}"
    bound = [b for node in plan.nodes for b in node.all_bot_ids if b]
    assert bound, "expected bound bot ids"
    assert all(b.startswith("${") and b.endswith("}") for b in bound), bound
    assert "${store_owner_bot_id}" in bound


def test_static_plan_from_yaml_expands_bindings():
    text = (
        "template_id: t\n"
        "nodes:\n"
        "  - id: n\n"
        "    type: bot\n"
        "    bot_id: ${store_owner_bot_id}\n"
    )
    plan = StaticPlanDefinition.from_yaml(text, bindings={"store_owner_bot_id": "uuid-1"})
    assert plan.nodes[0].bot_id == "uuid-1"


def test_static_plan_from_yaml_default_when_binding_missing():
    text = (
        "template_id: t\n"
        "nodes:\n"
        "  - id: n\n"
        "    type: bot\n"
        "    bot_id: ${store_owner_bot_id:-fallback-uuid}\n"
    )
    # non-empty map but the referenced key absent -> default branch applies
    plan = StaticPlanDefinition.from_yaml(text, bindings={"other_role": "x"})
    assert plan.nodes[0].bot_id == "fallback-uuid"


def test_static_plan_from_yaml_missing_binding_raises():
    text = (
        "template_id: t\n"
        "nodes:\n"
        "  - id: n\n"
        "    type: bot\n"
        "    bot_id: ${store_owner_bot_id}\n"
    )
    with pytest.raises(KeyError):
        StaticPlanDefinition.from_yaml(text, bindings={"other": "x"})


def test_static_plan_from_yaml_without_bindings_leaves_placeholder_literal():
    """No bindings -> placeholders stay literal (bare boot / unconfigured degrade)."""
    text = (
        "template_id: t\n"
        "nodes:\n"
        "  - id: n\n"
        "    type: bot\n"
        "    bot_id: ${store_owner_bot_id}\n"
    )
    plan = StaticPlanDefinition.from_yaml(text)
    assert plan.nodes[0].bot_id == "${store_owner_bot_id}"
    plan.validate_bindings()  # literal non-empty placeholder passes binding validation


def test_expand_placeholders_preserves_scalar_values():
    """Scalar YAML values pass through unchanged during recursive expansion."""
    from agentclaw.community.core.task.task_plan.static_plan import _expand_placeholders

    marker = object()
    assert _expand_placeholders(marker, {"role": "bot-1"}) is marker
