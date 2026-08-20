from agentclaw.community.core.bot_management.engines.registry import (
    get_default_skill_set_selection_policy,
)
from agentclaw.community.core.skill_center.policies.default_skill_set_selection import (
    DefaultSkillSetSelectionPolicy,
)


def test_default_policy_keeps_openclaw_unchanged():
    selection = DefaultSkillSetSelectionPolicy().resolve(
        persisted_engine_type="openclaw",
        runtime_engine_type="openclaw",
    )

    assert selection.engine_type == "openclaw"
    assert selection.bolt_id is None


def test_default_policy_keeps_normal_claude_code_unchanged():
    selection = DefaultSkillSetSelectionPolicy().resolve(
        persisted_engine_type="claude_code",
        runtime_engine_type="claude_code",
    )

    assert selection.engine_type == "claude_code"
    assert selection.bolt_id is None


def test_default_policy_has_no_engine_specific_runtime_routing():
    selection = DefaultSkillSetSelectionPolicy().resolve(
        persisted_engine_type="claude_code",
        runtime_engine_type="aicoding",
    )

    assert selection.engine_type == "claude_code"
    assert selection.bolt_id is None


def test_registered_policy_uses_aicoding_null_global_default_for_routed_claude_code():
    selection = get_default_skill_set_selection_policy().resolve(
        persisted_engine_type="claude_code",
        runtime_engine_type="aicoding",
    )

    assert selection.engine_type == "aicoding"
    assert selection.bolt_id is None


def test_registered_policy_keeps_openclaw_unchanged():
    selection = get_default_skill_set_selection_policy().resolve(
        persisted_engine_type="openclaw",
        runtime_engine_type="openclaw",
    )

    assert selection.engine_type == "openclaw"
    assert selection.bolt_id is None


def test_registered_policy_orders_routed_claude_code_global_default_fallbacks():
    plan = get_default_skill_set_selection_policy().resolve_candidates(
        persisted_engine_type="claude_code",
        runtime_engine_type="aicoding",
    )

    assert [(item.engine_type, item.bolt_id) for item in plan] == [
        ("aicoding", None),
        ("claude_code", None),
    ]


def test_registered_policy_orders_routed_claude_code_bot_then_global_fallbacks():
    plan = get_default_skill_set_selection_policy().resolve_candidates(
        persisted_engine_type="claude_code",
        runtime_engine_type="aicoding",
        bolt_id="bot-1",
    )

    assert [(item.engine_type, item.bolt_id) for item in plan] == [
        ("claude_code", "bot-1"),
        ("aicoding", None),
        ("claude_code", None),
    ]


def test_registered_policy_does_not_duplicate_global_default_bolt_id():
    plan = get_default_skill_set_selection_policy().resolve_candidates(
        persisted_engine_type="claude_code",
        runtime_engine_type="aicoding",
        bolt_id="default",
    )

    assert [(item.engine_type, item.bolt_id) for item in plan] == [
        ("aicoding", None),
        ("claude_code", None),
    ]


def test_policy_accepts_single_selection_resolver_result():
    from agentclaw.community.core.skill_center.policies.default_skill_set_selection import (
        DefaultSkillSetSelection,
    )

    class SingleSelectionResolver:
        def resolve_default_skill_set_selection(self, **_kwargs):
            return DefaultSkillSetSelection(engine_type="runtime", bolt_id="default")

    candidates = DefaultSkillSetSelectionPolicy(
        resolvers=[SingleSelectionResolver()]
    ).resolve_candidates(
        persisted_engine_type="persisted",
        runtime_engine_type="runtime",
    )

    assert [(item.engine_type, item.bolt_id) for item in candidates] == [
        ("runtime", "default")
    ]
