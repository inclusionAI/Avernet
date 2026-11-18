"""Default materialization with a de-identified historical membership shape."""

from datetime import datetime

import pytest

from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill
from agentclaw.community.core.models.space_skill import SkillVersion
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.skill_center.skill_set_apply import DefaultExclusionChange
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
)
from .test_apply_center_membership_identity import SCOPE, seed, snapshot
from .capability_desired_state_support import _Database


def test_default_skips_missing_historical_members_without_removing_relations():
    # Shape sampled from a preproduction Default: 57 raw members, 44 without
    # UUID or an asset, 9 legacy Repo assets and 4 Repo assets with UUIDs.
    # IDs and names below are synthetic and cannot identify the original rows.
    db = _Database()
    with db.transactional_orm_session() as session:
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
        for i in range(1, 14):
            uuid = f"00000000-0000-4000-8000-{i:012d}" if i <= 4 else None
            session.add(
                Skill(
                    id=i,
                    name=f"skill-{i}",
                    git_path=f"git://skill-{i}",
                    skill_uuid=uuid,
                    status="PUBLISHED" if uuid else "DEVELOPING",
                    env="dev",
                )
            )
            session.add(
                SkillSetSkill(skill_set_id=10, skill_id=i, skill_uuid=uuid, env="dev")
            )
        for i in range(100, 144):
            session.add(SkillSetSkill(skill_set_id=10, skill_id=i, env="dev"))
    repo = CapabilityDesiredStateRepository(db)
    for _ in range(2):
        repo.replace_apply_skill_sets(
            scope=SCOPE,
            sets=(),
            remove_names=(),
            default_change=DefaultExclusionChange(),
            platform_default_codes=frozenset(),
            mcp_catalog_entries={},
        )
        assert snapshot(repo).installations == set(range(1, 14))
    with db.orm_session() as session:
        assert session.query(SkillSetSkill).filter_by(skill_set_id=10).count() == 57


@pytest.mark.parametrize(
    "unavailable", ["no_version", "offline", "empty_version", "empty_name"]
)
def test_center_without_published_version_or_offline_is_not_installed(unavailable):
    db, repo = seed()
    with db.transactional_orm_session() as session:
        if unavailable == "no_version":
            session.query(SkillVersion).delete()
        elif unavailable == "offline":
            session.get(Skill, 1).offline_at = datetime(2026, 1, 1)
        elif unavailable == "empty_version":
            session.query(SkillVersion).one().sc_version_number = ""
        else:
            session.query(SkillVersion).one().name = ""
    repo.replace_apply_skill_sets(
        scope=SCOPE,
        sets=(),
        remove_names=(),
        default_change=DefaultExclusionChange(),
        platform_default_codes=frozenset(),
        mcp_catalog_entries={},
    )
    assert snapshot(repo).installations == set()
    with db.orm_session() as session:
        assert session.query(SkillSetSkill).filter_by(skill_set_id=10).count() == 1


@pytest.mark.parametrize("metadata", ["{", '{"mcp_dependencies":[""]}'])
def test_published_center_with_invalid_dependency_metadata_still_fails(metadata):
    db, repo = seed()
    with db.transactional_orm_session() as session:
        session.query(SkillVersion).one().metadata_json = metadata
    with pytest.raises(ValueError):
        repo.replace_apply_skill_sets(
            scope=SCOPE,
            sets=(),
            remove_names=(),
            default_change=DefaultExclusionChange(),
            platform_default_codes=frozenset(),
            mcp_catalog_entries={},
        )
    assert snapshot(repo).installations == set()


def test_reader_rechecks_plan_after_asset_goes_offline(monkeypatch):
    db, repo = seed()
    validate = repo._validate_default_dependencies

    def change_during_validation(session, **kwargs):
        if kwargs["locked"]:
            session.get(Skill, 1).offline_at = datetime(2026, 1, 1)
            session.flush()
        return validate(session, **kwargs)

    monkeypatch.setattr(
        repo, "_validate_default_dependencies", change_during_validation
    )
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="DEFAULT_SUPPLY_CHANGED"
    ):
        repo.sync_default_installations(
            bot_id="bot",
            owner_id="owner",
            env="dev",
            engine_type="openclaw",
            default_engine_types=("openclaw",),
        )
    assert snapshot(repo).installations == set()
    with db.orm_session() as session:
        assert session.get(Skill, 1).offline_at is None
