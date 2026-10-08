"""Unit tests for the R1–R3 capability ownership policy."""

from __future__ import annotations

import pytest

from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
)
from agentclaw.community.core.skill_center.policies.capability_ownership import (
    is_set_managed,
    require_can_join_set,
)


def _managed(skill_set: dict, **overrides) -> bool:
    kwargs = {
        "bot_id": "bot",
        "owner_id": "owner",
        "engine_type": "openclaw",
        "default_engine_types": ("openclaw",),
    }
    kwargs.update(overrides)
    return is_set_managed(referencing_sets=[skill_set], **kwargs)


def test_r1_the_bots_own_sets_manage_their_members():
    assert _managed(
        {"is_default": False, "bolt_id": "bot", "user_id": "owner", "engine_type": "openclaw"}
    )
    assert _managed(
        {"is_default": True, "bolt_id": "bot", "user_id": "owner", "engine_type": "openclaw"}
    )


def test_r1_the_platform_default_manages_by_engine_not_bolt_id():
    """The repository projects a null bolt_id to the literal "default", which
    is also a real legacy Bot id — ownerless Defaults are settled by engine."""
    platform_default = {
        "is_default": True,
        "bolt_id": "default",
        "user_id": None,
        "engine_type": "openclaw",
    }
    assert _managed(platform_default)
    assert not _managed(
        {**platform_default, "engine_type": "aicoding"}
    )
    # The layout engine of a coding template is tried alongside the persisted
    # one — either candidate reaches the Bot.
    assert _managed(
        {**platform_default, "engine_type": "aicoding"},
        engine_type="claude_code",
        default_engine_types=("aicoding", "claude_code"),
    )


def test_r1_other_bots_sets_never_manage_this_bots_capability():
    assert not _managed(
        {"is_default": False, "bolt_id": "another-bot", "user_id": "owner"}
    )
    # The legacy "default" Bot exists once per owner: bolt_id alone does not
    # identify a Bot.
    assert not _managed(
        {"is_default": False, "bolt_id": "bot", "user_id": "someone-else"}
    )
    # A Set left behind on an engine the Bot no longer runs.
    assert not _managed(
        {
            "is_default": False,
            "bolt_id": "bot",
            "user_id": "owner",
            "engine_type": "aicoding",
        }
    )


def test_r1_no_referencing_sets_means_direct_control():
    assert not is_set_managed(
        referencing_sets=[],
        bot_id="bot",
        owner_id="owner",
        engine_type="openclaw",
        default_engine_types=("openclaw",),
    )


def test_r1_a_missing_engine_widens_rather_than_narrows():
    """engine_type None means "do not filter by engine" for owned Sets."""
    assert _managed(
        {
            "is_default": False,
            "bolt_id": "bot",
            "user_id": "owner",
            "engine_type": "aicoding",
        },
        engine_type=None,
    )


def test_r2_direct_active_is_refused_first():
    with pytest.raises(SkillSetControlPlaneConflictError) as error:
        require_can_join_set(is_directly_active=True, is_in_another_set=True)
    assert "RESOURCE_DIRECT_ACTIVE" in str(error.value)


def test_r3_membership_in_any_set_forbids_joining_another():
    with pytest.raises(SkillSetControlPlaneConflictError) as error:
        require_can_join_set(is_directly_active=False, is_in_another_set=True)
    assert "RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET" in str(error.value)


def test_a_free_capability_may_join():
    require_can_join_set(is_directly_active=False, is_in_another_set=False)
