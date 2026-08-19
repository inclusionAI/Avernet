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


def test_registered_policy_uses_aicoding_global_default_for_routed_claude_code():
    selection = get_default_skill_set_selection_policy().resolve(
        persisted_engine_type="claude_code",
        runtime_engine_type="aicoding",
    )

    assert selection.engine_type == "aicoding"
    assert selection.bolt_id == "default"


def test_registered_policy_keeps_openclaw_unchanged():
    selection = get_default_skill_set_selection_policy().resolve(
        persisted_engine_type="openclaw",
        runtime_engine_type="openclaw",
    )

    assert selection.engine_type == "openclaw"
    assert selection.bolt_id is None
