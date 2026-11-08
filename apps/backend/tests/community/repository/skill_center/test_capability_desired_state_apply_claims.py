"""Apply Direct claims preserve Set ownership and asset identity."""

from __future__ import annotations
import pytest
from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.models.mcp import (
    BotMCPConfig,
    BotMCPInstallation,
    SkillSetMCPServer,
)
from agentclaw.community.core.skill_center.orm import (
    DefaultSkillsetMcpExclusion,
    DefaultSkillsetSkillExclusion,
)
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
)

from .capability_desired_state_support import (
    _Database,
    _seed_default_with_member,
    _APPLY_SCOPE,
    _ordinary_set,
)


def test_set_supply_is_active_sets_the_default_less_exclusions_and_the_platform_defaults():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, member = _seed_default_with_member(db)
    with db.transactional_orm_session() as session:
        active_skill = Skill(name="from-active", git_path="git://active", env="dev")
        idle_skill = Skill(name="from-idle", git_path="git://idle", env="dev")
        session.add_all([active_skill, idle_skill])
    _ordinary_set(db, skills=(active_skill,), mcp_codes=("mcp.active",))
    _ordinary_set(db, active=False, skills=(idle_skill,), mcp_codes=("mcp.idle",))

    names, codes = repository.set_supplied_capabilities(
        platform_default_codes=frozenset({"mcp.platform", "mcp.platform-excluded"}), **_APPLY_SCOPE
    )
    assert names == {"member", "from-active"}, "an inactive Set supplies nothing"
    assert codes == {"mcp.member", "mcp.active", "mcp.platform", "mcp.platform-excluded"}

    with db.transactional_orm_session() as session:
        session.add_all([
            DefaultSkillsetSkillExclusion(user_id="owner", bot_id="bot", skill_set_id=default.id, skill_id=member.id),
            DefaultSkillsetMcpExclusion(
                user_id="owner", bot_id="bot", skill_set_id=default.id, server_code="mcp.member"
            ),
            DefaultSkillsetMcpExclusion(
                user_id="owner", bot_id="bot", skill_set_id=default.id, server_code="mcp.platform-excluded"
            ),
        ])
    names, codes = repository.set_supplied_capabilities(
        platform_default_codes=frozenset({"mcp.platform", "mcp.platform-excluded"}), **_APPLY_SCOPE
    )
    assert (names, codes) == ({"from-active"}, {"mcp.active", "mcp.platform"}), "an exclusion ends the supply"


@pytest.mark.parametrize("supplier", ["ordinary", "default", "platform"])
def test_an_apply_mcp_claim_refuses_a_set_supplied_server_and_writes_nothing(supplier):
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    if supplier == "ordinary":
        _ordinary_set(db, mcp_codes=("mcp.taken",))
    elif supplier == "default":
        with db.transactional_orm_session() as session:
            default = SkillSet(name="default", user_id="", bolt_id="", engine_type="openclaw", is_default=True, env="dev")
            session.add(default)
            session.flush()
            session.add(SkillSetMCPServer(skill_set_id=default.id, server_code="mcp.taken", name="t", env="dev"))
    platform = frozenset({"mcp.taken"}) if supplier == "platform" else frozenset()

    with pytest.raises(SkillSetControlPlaneConflictError, match="APPLY_SET_SUPPLIED"):
        repository.claim_apply_mcp(
            server_code="mcp.taken", config={"headers": {}}, platform_default_codes=platform, **_APPLY_SCOPE
        )
    with pytest.raises(SkillSetControlPlaneConflictError, match="APPLY_SET_SUPPLIED"):
        repository.remove_apply_mcp(server_code="mcp.taken", platform_default_codes=platform, **_APPLY_SCOPE)
    with db.orm_session() as session:
        assert session.query(BotMCPConfig).count() == 0
        assert session.query(DefaultSkillsetMcpExclusion).count() == 0, "a claim never writes a Set"
        assert session.query(SkillSetMCPServer).count() == (0 if supplier == "platform" else 1)


def test_an_apply_mcp_claim_installs_a_free_server_with_its_override_and_removal_drops_both():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    _ordinary_set(db, active=False, mcp_codes=("mcp.free",))  # an inactive Set's member is free

    claimed = repository.claim_apply_mcp(
        server_code="mcp.free", config={"headers": {}}, platform_default_codes=frozenset(), **_APPLY_SCOPE
    )
    assert (claimed.changed, claimed.source_transitions) == (True, (("none", "direct"),))
    with db.orm_session() as session:
        assert [row.server_code for row in session.query(BotMCPInstallation)] == ["mcp.free"]
        assert session.query(BotMCPConfig).one().config == '{"headers":{}}'
        assert session.query(SkillSetMCPServer).count() == 1, "the Set is untouched"

    removed = repository.remove_apply_mcp(
        server_code="mcp.free", platform_default_codes=frozenset(), **_APPLY_SCOPE
    )
    assert (removed.changed, removed.source_transitions) == (True, (("direct", "none"),))
    with db.orm_session() as session:
        assert session.query(BotMCPInstallation).count() == 0
        assert session.query(BotMCPConfig).count() == 0


def test_ordinary_update_and_remove_can_manage_a_legacy_manifest_direct_mcp():
    """An earlier Manifest apply claimed a Default member Direct by excluding it; the ordinary commands still
    manage that shape."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, _skill = _seed_default_with_member(db)
    with db.transactional_orm_session() as session:
        session.add(DefaultSkillsetMcpExclusion(
            user_id="owner", bot_id="bot", skill_set_id=default.id, server_code="mcp.member",
        ))

    assert repository.manifest_direct_mcp_exists(
        bot_id="bot", owner_id="owner", server_code="mcp.member", platform_default_codes=frozenset(),
        engine_type="openclaw", default_engine_types=("openclaw",),
    ) is True
    updated = repository.set_mcp_override(
        bot_id="bot", owner_id="owner", server_code="mcp.member",
        config={"headers": {}}, platform_default_codes=frozenset(),
        engine_type="openclaw", default_engine_types=("openclaw",),
    )
    removed = repository.uninstall_mcp(
        bot_id="bot", owner_id="owner", server_code="mcp.member",
        platform_default_codes=frozenset(), engine_type="openclaw",
        default_engine_types=("openclaw",),
    )

    assert updated.changed is True
    assert removed.changed is True
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetMcpExclusion).count() == 1
        assert session.query(BotMCPInstallation).count() == 0
        assert session.query(BotMCPConfig).count() == 0


def test_an_apply_skill_claim_refuses_a_runtime_name_a_set_supplies():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        shared = Skill(name="same-name", git_path="git://shared", env="dev")
        local = Skill(
            name="same-name", git_path="local://same-name", user_id="owner", bolt_id="bot", env="dev",
        )
        session.add_all([shared, local])
    _ordinary_set(db, skills=(shared,))

    with pytest.raises(SkillSetControlPlaneConflictError, match="same-name"):
        repository.claim_apply_skill(skill_id=str(local.id), **_APPLY_SCOPE)
    with pytest.raises(SkillSetControlPlaneConflictError, match="same-name"):
        repository.remove_apply_skill(skill_id=str(shared.id), **_APPLY_SCOPE)
    with db.orm_session() as session:
        assert session.query(SkillSetSkill).count() == 1, "the Set is untouched"
        assert session.query(BotSkillInstallation).count() == 0


def test_an_apply_skill_claim_refuses_the_current_center_default_version_by_name():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        default = SkillSet(
            name="default", user_id="", bolt_id="", engine_type="openclaw", is_default=True, env="dev",
        )
        current = Skill(
            name="same-name", git_path="center://same-uuid",
            skill_uuid="same-uuid", version=2, status="PUBLISHED", env="dev",
        )
        local = Skill(
            name="same-name", git_path="local://same-name", user_id="owner", bolt_id="bot", env="dev",
        )
        session.add_all([default, current, local])
        session.flush()
        session.add(SkillSetSkill(skill_set_id=default.id, skill_id=999, skill_uuid="same-uuid", env="dev"))

    with pytest.raises(SkillSetControlPlaneConflictError):
        repository.claim_apply_skill(skill_id=str(local.id), **_APPLY_SCOPE)
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetSkillExclusion).count() == 0


def test_an_apply_skill_claim_replaces_another_direct_skill_of_its_name_and_removal_drops_it():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        older = Skill(name="same-name", git_path="git://older", env="dev")
        local = Skill(
            name="same-name", git_path="local://same-name", user_id="owner", bolt_id="bot", env="dev",
        )
        session.add_all([older, local])
        session.flush()
        session.add(BotSkillInstallation(bot_id="bot", owner_id="owner", skill_id=older.id, env="dev"))

    claimed = repository.claim_apply_skill(skill_id=str(local.id), **_APPLY_SCOPE)
    assert (claimed.changed, claimed.source_transitions) == (True, (("none", "direct"),))
    with db.orm_session() as session:
        assert {row.skill_id for row in session.query(BotSkillInstallation)} == {local.id}

    removed = repository.remove_apply_skill(skill_id=str(local.id), **_APPLY_SCOPE)
    assert (removed.changed, removed.source_transitions) == (True, (("direct", "none"),))
    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).count() == 0


def test_an_apply_claims_a_shared_center_or_repo_asset_direct():
    """A genome revision names Center and repository skills; the apply claims the shared asset itself."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        center = Skill(
            name="refund-policy", git_path="center://refund", skill_uuid="refund",
            version=3, status="PUBLISHED", env="dev",
        )
        repo = Skill(name="weather", git_path="git://team/weather", env="dev")
        session.add_all([center, repo])

    for skill in (center, repo):
        result = repository.claim_apply_skill(skill_id=str(skill.id), **_APPLY_SCOPE)
        assert result.source_transitions == (("none", "direct"),)
    with db.orm_session() as session:
        assert {row.skill_id for row in session.query(BotSkillInstallation)} == {center.id, repo.id}
        assert session.query(Skill).count() == 2, "a shared asset is claimed, never copied"


def test_an_apply_may_not_claim_another_bots_local_package():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        foreign = Skill(
            name="theirs", git_path="local://theirs", user_id="someone", bolt_id="other", env="dev",
        )
        session.add(foreign)

    with pytest.raises(SkillSetControlPlaneNotFoundError):
        repository.claim_apply_skill(skill_id=str(foreign.id), **_APPLY_SCOPE)
