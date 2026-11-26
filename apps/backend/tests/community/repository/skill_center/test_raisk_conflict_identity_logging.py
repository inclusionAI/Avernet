"""RAISK raises now carry the colliding identity in the log.

The control plane's RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET answers conflicts
with a bare reason code. Operators could see *that* a default member was
claimed by another set, but never *which* set or skill, so the CDO-line
failures (publish, MCP config write) were not actionable without container
access. These tests pin the identity line: the reason code on the wire stays
byte-identical, and a warning naming the conflicting set and member is logged
right before the raise.
"""
import logging

import pytest

from agentclaw.community.core.models.skill import SkillSet, SkillSetSkill
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
)
from agentclaw.community.core.skill_center.skill_set_apply import (
    DefaultExclusionChange,
    ResolvedSkillSetWrite,
)
from .test_apply_center_membership_identity import SCOPE, UUID, seed


def test_default_sync_conflict_identifies_colliding_set_and_skill(caplog):
    db, repo = seed()
    with db.transactional_orm_session() as session:
        # This bot's ordinary set claims the very skill the default set owns.
        session.add(
            SkillSet(
                id=20,
                name="colliding-expert",
                is_active=True,
                user_id="owner",
                bolt_id="bot",
                engine_type="openclaw",
                env="dev",
            )
        )
        session.add(
            SkillSetSkill(skill_set_id=20, skill_id=1, skill_uuid=UUID, env="dev")
        )

    with caplog.at_level(logging.WARNING, logger="agentclaw"):
        with pytest.raises(
            SkillSetControlPlaneConflictError,
            match="RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET",
        ):
            repo.sync_default_installations(
                bot_id="bot",
                owner_id="owner",
                env="dev",
                engine_type="openclaw",
                default_engine_types=("openclaw",),
            )

    line = next(
        (
            r
            for r in caplog.records
            if "RAISK default membership conflict" in r.getMessage()
        ),
        None,
    )
    assert line is not None, "expected an identity line before the RAISK raise"
    message = line.getMessage()
    assert "conflicting_set_id=20" in message
    assert "conflicting_set_name=colliding-expert" in message
    assert "skill_id=1" in message


def test_apply_duplicate_members_identifies_duplicated_values_and_holders(caplog):
    # Release the default claim first so the duplicate-member guard is the one
    # that fires, not the ordinary-vs-default collision above it.
    db, repo = seed(excluded=True)
    with caplog.at_level(logging.WARNING, logger="agentclaw"):
        with pytest.raises(
            SkillSetControlPlaneConflictError,
            match="RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET",
        ):
            repo.replace_apply_skill_sets(
                scope=SCOPE,
                sets=(
                    ResolvedSkillSetWrite("alpha", is_active=True, skill_ids=(1,)),
                    ResolvedSkillSetWrite("beta", is_active=True, skill_ids=(1,)),
                ),
                remove_names=(),
                default_change=DefaultExclusionChange(),
                platform_default_codes=frozenset(),
                mcp_catalog_entries={},
            )

    line = next(
        (
            r
            for r in caplog.records
            if "RAISK duplicate members in apply targets" in r.getMessage()
        ),
        None,
    )
    assert line is not None, "expected an identity line before the RAISK raise"
    message = line.getMessage()
    assert "attr=skill_ids" in message
    assert "[1]" in message
    assert "holder_sets=['alpha', 'beta']" in message


def test_default_sync_without_conflict_stays_silent_and_unchanged(caplog):
    db, repo = seed(excluded=True)
    with caplog.at_level(logging.WARNING, logger="agentclaw"):
        repo.sync_default_installations(
            bot_id="bot",
            owner_id="owner",
            env="dev",
            engine_type="openclaw",
            default_engine_types=("openclaw",),
        )
    assert not [
        r for r in caplog.records if "RAISK" in r.getMessage()
    ]
