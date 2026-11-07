"""Apply UoW keeps the established UUID identity of Default Center memberships."""

from datetime import datetime

import pytest

from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill
from agentclaw.community.core.models.space_skill import SkillVersion
from agentclaw.community.core.models.skill_installation_source import (
    SkillInstallationSourceKind,
)
from agentclaw.community.core.repository.implementations.skill_center.tables import (
    skill_installations,
)
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.core.skill_center.orm import DefaultSkillsetSkillExclusion
from agentclaw.community.core.skill_center.skill_set_apply import (
    BotCapabilityScope,
    DefaultExclusionChange,
    ResolvedSkillSetWrite,
)
from .capability_desired_state_support import _Database


UUID = "00000000-0000-4000-8000-000000000001"
SCOPE = BotCapabilityScope("bot", "owner", "openclaw", ("openclaw",), "teamclaw", "dev")


def seed(*, excluded=False, missing_uuid=False, other=False):
    db = _Database()
    with db.transactional_orm_session() as session:
        session.add(
            Skill(
                id=1,
                name="weather",
                git_path="center://weather",
                skill_uuid=UUID,
                status="PUBLISHED",
                version=1,
                is_public=True,
                env="dev",
            )
        )
        session.add(
            SkillVersion(
                id=101,
                skill_id=1,
                version_ordinal=1,
                status="PUBLISHED",
                sc_version_number="1.0",
                sc_skill_id=7,
                sc_version_id=10,
                name="weather",
                description="Published current version",
                metadata_json='{"mcp_dependencies":[]}',
                published_at=datetime(2026, 9, 1),
                created_by="owner",
                env="dev",
            )
        )
        session.add(
            SkillSet(
                id=10,
                name="default",
                is_default=True,
                user_id="",
                bolt_id="",
                engine_type="openclaw",
                env="dev",
            )
        )
        session.add(
            SkillSetSkill(
                skill_set_id=10,
                skill_id=999,
                skill_uuid=None if missing_uuid else UUID,
                env="dev",
            )
        )
        if excluded:
            session.add(
                DefaultSkillsetSkillExclusion(
                    user_id="owner",
                    bot_id="bot",
                    skill_set_id=10,
                    skill_id=999,
                    avernet_tenant="teamclaw",
                )
            )
        if other:
            session.add(
                SkillSet(
                    id=20,
                    name="other",
                    is_default=False,
                    is_active=False,
                    user_id="owner",
                    bolt_id="bot",
                    engine_type="openclaw",
                    env="dev",
                )
            )
            session.add(
                SkillSetSkill(skill_set_id=20, skill_id=888, skill_uuid=UUID, env="dev")
            )
    return db, CapabilityDesiredStateRepository(db)


def replace(repo, *, restore=False, remove_names=()):
    return repo.replace_apply_skill_sets(
        scope=SCOPE,
        sets=(ResolvedSkillSetWrite("expert"),),
        remove_names=remove_names,
        default_change=DefaultExclusionChange(remove_skills=frozenset({(10, 999)}))
        if restore
        else DefaultExclusionChange(),
        platform_default_codes=frozenset(),
        mcp_catalog_entries={},
    )


def snapshot(repo):
    return repo.snapshot_desired_state(
        bot_id="bot", owner_id="owner", engine_type="openclaw"
    )


def test_apply_retains_stale_raw_default_member_using_current_published_uuid():
    db, repo = seed()
    result = replace(repo)
    assert result.details["created"] == ("expert",)
    assert snapshot(repo).installations == {1}
    with db.orm_session() as session:
        member = session.query(SkillSetSkill).filter_by(skill_set_id=10).one()
        assert (member.skill_id, member.skill_uuid) == (999, UUID)


@pytest.mark.parametrize("restore", [False, True])
def test_missing_raw_member_without_uuid_still_fails_closed(restore):
    _db, repo = seed(excluded=restore, missing_uuid=True)
    with pytest.raises(
        (SkillSetControlPlaneNotFoundError, SkillSetControlPlaneConflictError)
    ):
        replace(repo, restore=restore)
    assert [
        row["name"]
        for row in repo.list_sets(
            bot_id="bot", owner_id="owner", engine_type="openclaw"
        )
    ] == ["default"]
    assert snapshot(repo).installations == set()


def test_raw_exclusion_number_does_not_remove_an_unrelated_resolved_current_id():
    db, repo = seed()
    second_uuid = "00000000-0000-4000-8000-000000000002"
    with db.transactional_orm_session() as session:
        session.add(
            Skill(
                id=2,
                name="second",
                git_path="center://second",
                skill_uuid=second_uuid,
                status="PUBLISHED",
                version=1,
                env="dev",
            )
        )
        session.add(
            SkillVersion(
                skill_id=2,
                version_ordinal=1,
                status="PUBLISHED",
                sc_version_number="1.0",
                sc_skill_id=8,
                sc_version_id=11,
                name="second",
                metadata_json='{"mcp_dependencies":[]}',
                published_at=datetime(2026, 9, 1),
                created_by="owner",
                env="dev",
            )
        )
        # This different association's raw key collides with weather's CURRENT id.
        session.add(
            SkillSetSkill(
                skill_set_id=10, skill_id=1, skill_uuid=second_uuid, env="dev"
            )
        )
        session.add(
            DefaultSkillsetSkillExclusion(
                user_id="owner",
                bot_id="bot",
                skill_set_id=10,
                skill_id=1,
                avernet_tenant="teamclaw",
            )
        )
    replace(repo)
    state = snapshot(repo)
    assert state.installations == {1}
    assert state.skill_exclusions == frozenset({(10, 1)})


def test_stale_raw_default_restore_uses_current_uuid_without_other_supply():
    _db, repo = seed(excluded=True)
    replace(repo, restore=True)
    state = snapshot(repo)
    assert state.installations == {1}
    assert state.skill_exclusions == frozenset()


@pytest.mark.parametrize("occupant", ["direct", "inactive-set", "removed-in-this-plan"])
def test_stale_raw_default_restore_refuses_current_supply_and_keeps_exclusion(occupant):
    db, repo = seed(excluded=True, other=occupant != "direct")
    if occupant == "direct":
        # Historical Direct supply exists before the platform Default acquires
        # this stable UUID. Arrange that real fact through its table writer:
        # the legacy ordinary API uses a different raw-ID exclusion guard.
        with db.transactional_orm_session() as session:
            assert skill_installations.install(
                session,
                bot_id="bot",
                owner_id="owner",
                env="dev",
                skill_id=1,
                source_kind=SkillInstallationSourceKind.DIRECT,
            )
            assert skill_installations.sources(
                session,
                bot_id="bot",
                owner_id="owner",
                env="dev",
            ) == {1: (SkillInstallationSourceKind.DIRECT.value, None)}
    before = snapshot(repo)
    reason = (
        "RESOURCE_DIRECT_ACTIVE"
        if occupant == "direct"
        else "RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET"
    )
    with pytest.raises(SkillSetControlPlaneConflictError, match=reason):
        replace(
            repo,
            restore=True,
            remove_names=("other",) if occupant == "removed-in-this-plan" else (),
        )
    assert snapshot(repo) == before
    assert snapshot(repo).skill_exclusions == frozenset({(10, 999)})
    assert "expert" not in {
        row["name"]
        for row in repo.list_sets(
            bot_id="bot", owner_id="owner", engine_type="openclaw"
        )
    }
    if occupant != "direct":
        assert "other" in {
            row["name"]
            for row in repo.list_sets(
                bot_id="bot", owner_id="owner", engine_type="openclaw"
            )
        }


def test_excluded_missing_uuid_member_stays_excluded_without_blocking_other_sets():
    _db, repo = seed(excluded=True, missing_uuid=True)
    replace(repo)
    state = snapshot(repo)
    assert state.installations == set()
    assert state.skill_exclusions == frozenset({(10, 999)})
    assert "expert" in {
        row["name"]
        for row in repo.list_sets(
            bot_id="bot", owner_id="owner", engine_type="openclaw"
        )
    }


def test_stale_exclusion_without_membership_can_be_cleared_beside_direct_supply():
    db, repo = seed(excluded=True)
    with db.transactional_orm_session() as session:
        session.query(SkillSetSkill).filter_by(skill_set_id=10).delete()
    repo.install_skill(
        bot_id="bot",
        owner_id="owner",
        skill_id="1",
        engine_type="openclaw",
        default_engine_types=("openclaw",),
    )
    replace(repo, restore=True)
    state = snapshot(repo)
    assert state.installations == {1}
    assert state.skill_exclusions == frozenset()
