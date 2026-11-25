"""Normal Direct control checks the raw keys of the matching stable memberships."""

from datetime import datetime

import pytest

from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill
from agentclaw.community.core.models.space_skill import SkillVersion
from agentclaw.community.core.models.skill_installation_source import SkillInstallationSourceKind
from agentclaw.community.core.repository.implementations.skill_center.tables import skill_installations
from agentclaw.community.core.skill_center.errors import SkillSetControlPlaneConflictError
from agentclaw.community.core.skill_center.orm import DefaultSkillsetSkillExclusion
from .test_apply_center_membership_identity import UUID, seed, snapshot


SECOND_UUID = "00000000-0000-4000-8000-000000000002"
SCOPE = dict(bot_id="bot", owner_id="owner", engine_type="openclaw", default_engine_types=("openclaw",))


def _second_member(session):
    session.add(Skill(
        id=2, name="second", git_path="center://second", skill_uuid=SECOND_UUID,
        status="PUBLISHED", version=1, env="dev",
    ))
    session.add(SkillVersion(
        skill_id=2, version_ordinal=1, status="PUBLISHED", sc_version_number="1.0",
        sc_skill_id=8, sc_version_id=11, name="second", metadata_json='{"mcp_dependencies":[]}',
        published_at=datetime(2026, 9, 1), created_by="owner", env="dev",
    ))
    session.add(SkillSetSkill(skill_set_id=10, skill_id=1, skill_uuid=SECOND_UUID, env="dev"))


def _exclude(session, raw_id):
    session.add(DefaultSkillsetSkillExclusion(
        user_id="owner", bot_id="bot", skill_set_id=10, skill_id=raw_id, avernet_tenant="teamclaw",
    ))


@pytest.mark.parametrize("action", ["install_skill", "uninstall_skill"])
@pytest.mark.parametrize("installed", [True, False])
@pytest.mark.parametrize("own_excluded", [True, False])
@pytest.mark.parametrize("other_excluded", [True, False])
def test_direct_control_uses_only_its_matching_raw_default_key(action, installed, own_excluded, other_excluded):
    db, repo = seed(excluded=own_excluded)
    with db.transactional_orm_session() as session:
        _second_member(session)
        if other_excluded:
            _exclude(session, 1)
        if installed:
            skill_installations.install(
                session, bot_id="bot", owner_id="owner", env="dev", skill_id=1,
                source_kind=SkillInstallationSourceKind.DEFAULT, source_set_id=10,
            )
    before = snapshot(repo)
    operation = getattr(repo, action)
    if not own_excluded:
        with pytest.raises(SkillSetControlPlaneConflictError, match="RESOURCE_MANAGED_BY_SKILL_SET"):
            operation(**SCOPE, skill_id="1")
        assert snapshot(repo) == before
    else:
        result = operation(**SCOPE, skill_id="1")
        after = snapshot(repo)
        assert after.skill_exclusions == before.skill_exclusions
        if action == "install_skill":
            assert after.installations == {1}
            assert after.installation_sources[1] == ("DIRECT", None)
        else:
            assert result.changed is installed
            assert after.installations == set()
            assert after.installation_sources == {}


@pytest.mark.parametrize("action", ["install_skill", "uninstall_skill"])
@pytest.mark.parametrize("all_excluded", [True, False])
def test_every_matching_raw_membership_must_be_excluded_before_direct_control(action, all_excluded):
    db, repo = seed(excluded=True)
    with db.transactional_orm_session() as session:
        session.add(SkillSetSkill(skill_set_id=10, skill_id=888, skill_uuid=UUID, env="dev"))
        if all_excluded:
            _exclude(session, 888)
    before = snapshot(repo)
    if all_excluded:
        getattr(repo, action)(**SCOPE, skill_id="1")
        assert snapshot(repo).skill_exclusions == before.skill_exclusions
    else:
        with pytest.raises(SkillSetControlPlaneConflictError, match="RESOURCE_MANAGED_BY_SKILL_SET"):
            getattr(repo, action)(**SCOPE, skill_id="1")
        assert snapshot(repo) == before


@pytest.mark.parametrize("active", [True, False])
@pytest.mark.parametrize("owns_target", [True, False])
def test_ordinary_center_alias_only_blocks_the_asset_it_actually_owns(active, owns_target):
    db, repo = seed(excluded=True)
    with db.transactional_orm_session() as session:
        _second_member(session)
        _exclude(session, 1)
        session.add(SkillSet(
            id=20, name="ordinary", user_id="owner", bolt_id="bot", engine_type="openclaw",
            is_active=active, env="dev",
        ))
        session.add(SkillSetSkill(
            skill_set_id=20, skill_id=888 if owns_target else 1,
            skill_uuid=UUID if owns_target else SECOND_UUID, env="dev",
        ))
    before = snapshot(repo)
    if owns_target:
        with pytest.raises(SkillSetControlPlaneConflictError, match="RESOURCE_MANAGED_BY_SKILL_SET"):
            repo.install_skill(**SCOPE, skill_id="1")
        assert snapshot(repo) == before
    else:
        repo.install_skill(**SCOPE, skill_id="1")
        after = snapshot(repo)
        assert after.installations == {1}
        assert after.installation_sources[1] == ("DIRECT", None)
        assert after.skill_exclusions == before.skill_exclusions
        assert after.memberships == before.memberships


@pytest.mark.parametrize("excluded", [True, False])
def test_missing_asset_uninstall_retains_the_raw_membership_guard(excluded):
    _db, repo = seed(excluded=excluded, missing_uuid=True)
    before = snapshot(repo)
    if excluded:
        assert not repo.uninstall_skill(**SCOPE, skill_id="999").changed
    else:
        with pytest.raises(SkillSetControlPlaneConflictError, match="RESOURCE_MANAGED_BY_SKILL_SET"):
            repo.uninstall_skill(**SCOPE, skill_id="999")
    assert snapshot(repo) == before
