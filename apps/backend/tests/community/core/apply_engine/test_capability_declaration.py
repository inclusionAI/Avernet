"""Aggregate declarations preserve report identities and legacy input scope."""

import pytest
from agentclaw.community.core.apply_engine.constructs import Category, Section
from agentclaw.community.core.apply_engine.desired_state import (
    CapabilityDeclaration,
    CenterSkillRef,
    Declaration,
    DesiredCapabilities,
    DesiredMcpServer,
    DesiredSkill,
    DesiredState,
    EntryFailure,
)


def test_legacy_inputs_normalize_once_before_phase_selection():
    skills = Declaration(
        (
            DesiredSkill(
                "review", CenterSkillRef("5e79e624-6898-4d77-91fd-11b9fb0822c4")
            ),
        )
    )
    state = DesiredState(skills=skills)
    target = state.declaration(Category.CAPABILITIES).entries[0]
    assert target.skills is skills
    assert target.skill_sets is None and target.mcp is None
    assert state.skills is None and state.mcp is None
    assert state.declared == (Category.CAPABILITIES,)
    assert state.only({Section.SCRIPT}).capabilities is None
    assert state.only({Category.SKILLS}).capabilities is None


def test_report_views_prefix_children_without_exposing_aggregate_identity():
    target = DesiredCapabilities(
        skills=Declaration((), (EntryFailure("broken", "invalid package"),)),
        mcp=Declaration((DesiredMcpServer("mcp.docs"),)),
    )
    declaration = CapabilityDeclaration.from_target(target)
    assert declaration.entries == (target,)
    assert declaration.identities == ("mcp/mcp.docs",)
    assert declaration.failures == (EntryFailure("skills/broken", "invalid package"),)
    assert (
        DesiredState(capabilities=declaration).declaration(Category.CAPABILITIES)
        is declaration
    )
    with pytest.raises(ValueError):
        DesiredState(capabilities=declaration, skills=Declaration(()))


def test_empty_target_stays_one_entry_and_absent_target_is_undeclared():
    assert CapabilityDeclaration.from_target(DesiredCapabilities()) is None
    target = DesiredCapabilities(
        skills=Declaration(()), skill_sets=Declaration(()), mcp=Declaration(())
    )
    declaration = CapabilityDeclaration.from_target(target)
    assert declaration.entries == (target,)
    assert declaration.identities == ()
    with pytest.raises(ValueError):
        CapabilityDeclaration(())
    with pytest.raises(ValueError):
        CapabilityDeclaration(
            (DesiredCapabilities(skills=Declaration((), (EntryFailure("x", "bad"),))),)
        )
