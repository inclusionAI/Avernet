"""SkillSet scope, lifecycle and transaction contracts."""

from __future__ import annotations
import pytest
from sqlalchemy import event
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
    SkillRuntimeNameConflictError,
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.core.skill_center.legacy_skill_set_compatibility import (
    LegacySkillSetScope,
)

from .capability_desired_state_support import (
    _Database as _Database,
    _bridge_fixture as _bridge_fixture,
    _bridge as _bridge,
    _FailingInsertSession as _FailingInsertSession,
    _stale_center_membership as _stale_center_membership,
    _offlined_center_member as _offlined_center_member,
    _seed_default_with_member as _seed_default_with_member,
    _DEFAULT_SCOPE as _DEFAULT_SCOPE,
    _seed_skill_with_dependencies as _seed_skill_with_dependencies,
    _seed_center_skill_with_dependencies as _seed_center_skill_with_dependencies,
    _APPLY_SCOPE as _APPLY_SCOPE,
    _ordinary_set as _ordinary_set,
)


def test_purge_bot_installations_is_exactly_owner_bot_env_scoped() -> None:
    db = _Database()
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                BotSkillInstallation(
                    owner_id="owner", bot_id="default", skill_id=1, env="dev"
                ),
                BotSkillInstallation(
                    owner_id="other", bot_id="default", skill_id=2, env="dev"
                ),
                BotMCPInstallation(
                    owner_id="owner", bot_id="default", server_code="mcp.mine", env="dev"
                ),
                BotMCPInstallation(
                    owner_id="other", bot_id="default", server_code="mcp.other", env="dev"
                ),
                BotMCPConfig(
                    owner_id="owner",
                    bot_id="default",
                    server_code="mcp.mine",
                    config='{"headers":{"X-Test":"mine"}}',
                    env="dev",
                ),
                BotMCPConfig(
                    owner_id="other",
                    bot_id="default",
                    server_code="mcp.other",
                    config='{"headers":{"X-Test":"other"}}',
                    env="dev",
                ),
            ]
        )

    result = CapabilityDesiredStateRepository(db).purge_bot_installations(
        owner_id="owner", bot_id="default", env="dev"
    )

    assert result == {"skills": 1, "mcps": 1}
    with db.orm_session() as session:
        assert [row.owner_id for row in session.query(BotSkillInstallation)] == [
            "other"
        ]
        assert [row.owner_id for row in session.query(BotMCPInstallation)] == [
            "other"
        ]
        assert [row.owner_id for row in session.query(BotMCPConfig)] == ["other"]


def test_legacy_scope_resolution_returns_only_ordinary_set_address() -> None:
    db = _Database()
    with db.transactional_orm_session() as session:
        ordinary = SkillSet(
            name="ordinary",
            user_id="owner",
            bolt_id="persisted-bot",
            engine_type="claude_code",
            env="dev",
        )
        default = SkillSet(
            name="default",
            user_id="",
            bolt_id="",
            engine_type="openclaw",
            is_default=True,
            env="dev",
        )
        session.add_all([ordinary, default])
        session.flush()
        ordinary_id = str(ordinary.id)
        default_id = str(default.id)

    repository = CapabilityDesiredStateRepository(db)

    assert repository.resolve_legacy_set_scope(
        set_id=ordinary_id
    ) == LegacySkillSetScope(owner_id="owner", bot_id="persisted-bot")
    assert repository.resolve_legacy_set_scope(set_id=default_id) is None
    with pytest.raises(SkillSetControlPlaneNotFoundError):
        repository.resolve_legacy_set_scope(set_id="999999")


def test_list_sets_is_scoped_to_exact_owner_for_shared_default_bot_id():
    """``bot_id=default`` is reused, so it cannot identify a user's sets."""
    db = _Database()
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                SkillSet(
                    name="mine",
                    user_id="owner-a",
                    bolt_id="default",
                    engine_type="openclaw",
                    env="dev",
                ),
                SkillSet(
                    name="other-owner",
                    user_id="owner-b",
                    bolt_id="default",
                    engine_type="openclaw",
                    env="dev",
                ),
                SkillSet(
                    name="system-default",
                    user_id="",
                    bolt_id="",
                    engine_type="openclaw",
                    is_default=True,
                    env="dev",
                ),
                SkillSet(
                    name="other-engine-default",
                    user_id="",
                    bolt_id="",
                    engine_type="hermes",
                    is_default=True,
                    env="dev",
                ),
            ]
        )

    items = CapabilityDesiredStateRepository(db).list_sets(
        bot_id="default", owner_id="owner-a", engine_type="openclaw"
    )

    assert [item["name"] for item in items] == ["system-default", "mine"]


def test_global_default_reads_apply_owner_bot_exclusions_without_hiding_membership():
    """Global Default is visible to every Bot, but its content is per Bot."""
    db = _Database()
    with db.transactional_orm_session() as session:
        default = SkillSet(
            name="system-default",
            user_id="",
            bolt_id="",
            engine_type="openclaw",
            is_default=True,
            env="dev",
        )
        included = Skill(name="included", git_path="git://included", env="dev")
        excluded = Skill(name="excluded", git_path="git://excluded", env="dev")
        session.add_all([default, included, excluded])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=default.id, skill_id=included.id, env="dev"
                ),
                SkillSetSkill(
                    skill_set_id=default.id, skill_id=excluded.id, env="dev"
                ),
                SkillSetMCPServer(
                    skill_set_id=default.id,
                    server_code="mcp.included",
                    name="included",
                    env="dev",
                ),
                SkillSetMCPServer(
                    skill_set_id=default.id,
                    server_code="mcp.excluded",
                    name="excluded",
                    env="dev",
                ),
                DefaultSkillsetSkillExclusion(
                    user_id="owner-a",
                    bot_id="default",
                    skill_set_id=default.id,
                    skill_id=excluded.id,
                ),
                DefaultSkillsetMcpExclusion(
                    user_id="owner-a",
                    bot_id="default",
                    skill_set_id=default.id,
                    server_code="mcp.excluded",
                ),
            ]
        )

    repository = CapabilityDesiredStateRepository(db)

    assert repository.get_set(
        bot_id="default", owner_id="owner-a", set_id=str(default.id), engine_type="openclaw"
    )["is_default"] is True
    assert [item["name"] for item in repository.list_skills(
        bot_id="default", owner_id="owner-a", set_id=str(default.id), engine_type="openclaw"
    )] == ["included"]
    assert [item["server_code"] for item in repository.list_mcps(
        bot_id="default", owner_id="owner-a", set_id=str(default.id), engine_type="openclaw"
    )] == ["mcp.included"]
    # The same system membership remains intact for another owner's Bot.
    assert [item["name"] for item in repository.list_skills(
        bot_id="default", owner_id="owner-b", set_id=str(default.id), engine_type="openclaw"
    )] == ["included", "excluded"]


def test_default_only_sync_materializes_defaults_without_repairing_ordinary_history():
    """The post-backfill path has one writer: Default membership/exclusion."""
    db = _Database()
    with db.transactional_orm_session() as session:
        default = SkillSet(
            name="platform-default", bolt_id="", user_id="", engine_type="openclaw",
            is_default=True, env="dev",
        )
        ordinary = SkillSet(
            name="ordinary", bolt_id="bot", user_id="owner", engine_type="openclaw",
            is_active=True, env="dev",
        )
        default_skill = Skill(name="default", git_path="git://default", env="dev")
        excluded_skill = Skill(name="excluded", git_path="git://excluded", env="dev")
        shared_skill = Skill(name="shared", git_path="git://shared", env="dev")
        direct_skill = Skill(name="direct", git_path="local://direct", env="dev")
        former_default_skill = Skill(
            name="former-default", git_path="git://former-default", env="dev"
        )
        session.add_all([
            default, ordinary, default_skill, excluded_skill, shared_skill,
            direct_skill, former_default_skill,
        ])
        session.flush()
        session.add_all([
            SkillSetSkill(skill_set_id=default.id, skill_id=default_skill.id, env="dev"),
            SkillSetSkill(skill_set_id=default.id, skill_id=excluded_skill.id, env="dev"),
            SkillSetSkill(skill_set_id=default.id, skill_id=shared_skill.id, env="dev"),
            SkillSetSkill(skill_set_id=ordinary.id, skill_id=shared_skill.id, env="dev"),
            SkillSetMCPServer(skill_set_id=default.id, server_code="mcp.default", name="default", env="dev"),
            SkillSetMCPServer(skill_set_id=default.id, server_code="mcp.excluded", name="excluded", env="dev"),
            SkillSetMCPServer(skill_set_id=default.id, server_code="mcp.shared", name="shared", env="dev"),
            SkillSetMCPServer(skill_set_id=ordinary.id, server_code="mcp.shared", name="shared", env="dev"),
            DefaultSkillsetSkillExclusion(
                user_id="owner", bot_id="bot", skill_set_id=default.id,
                skill_id=excluded_skill.id,
            ),
            DefaultSkillsetSkillExclusion(
                user_id="owner", bot_id="bot", skill_set_id=default.id,
                skill_id=shared_skill.id,
            ),
            DefaultSkillsetMcpExclusion(
                user_id="owner", bot_id="bot", skill_set_id=default.id,
                server_code="mcp.excluded",
            ),
            BotSkillInstallation(bot_id="bot", owner_id="owner", skill_id=excluded_skill.id, env="dev"),
            # Historical malformed overlap: Default exclusion must not remove
            # the active ordinary Set's claim.
            BotSkillInstallation(bot_id="bot", owner_id="owner", skill_id=shared_skill.id, env="dev"),
            BotSkillInstallation(bot_id="bot", owner_id="owner", skill_id=direct_skill.id, env="dev"),
            # Default membership removal is an explicit operations cleanup;
            # the reader cannot infer that this is not a Direct installation.
            BotSkillInstallation(bot_id="bot", owner_id="owner", skill_id=former_default_skill.id, env="dev"),
            BotMCPInstallation(bot_id="bot", owner_id="owner", server_code="mcp.excluded", env="dev"),
        ])

    repository = CapabilityDesiredStateRepository(db)
    plan = repository.sync_default_installations(
        bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
    )

    assert plan.skills_to_install == frozenset({default_skill.id})
    assert plan.skills_to_uninstall == frozenset()
    assert plan.mcps_to_install == frozenset({"mcp.default", "mcp.shared"})
    assert plan.mcps_to_uninstall == frozenset()
    with db.orm_session() as session:
        assert {row.skill_id for row in session.query(BotSkillInstallation).all()} == {
            default_skill.id, excluded_skill.id, shared_skill.id,
            direct_skill.id, former_default_skill.id,
        }
        assert {row.server_code for row in session.query(BotMCPInstallation).all()} == {
            "mcp.default", "mcp.excluded", "mcp.shared",
        }

    assert repository.sync_default_installations(
        bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
    ) == plan


def test_new_bot_initialization_inserts_missing_rows_without_deleting_existing_rows():
    """Creation/retry initializes DB state without Reader or Runtime projection."""
    db = _Database()
    with db.transactional_orm_session() as session:
        active = SkillSet(
            name="active", bolt_id="bot", user_id="owner", engine_type="openclaw",
            is_active=True, env="dev",
        )
        inactive = SkillSet(
            name="inactive", bolt_id="bot", user_id="owner", engine_type="openclaw",
            is_active=False, env="dev",
        )
        active_skill = Skill(name="active", git_path="git://active", env="dev")
        stale_skill = Skill(name="stale", git_path="git://stale", env="dev")
        direct_skill = Skill(name="direct", git_path="local://direct", env="dev")
        session.add_all([active, inactive, active_skill, stale_skill, direct_skill])
        session.flush()
        session.add_all([
            SkillSetSkill(skill_set_id=active.id, skill_id=active_skill.id, env="dev"),
            SkillSetSkill(skill_set_id=inactive.id, skill_id=stale_skill.id, env="dev"),
            SkillSetMCPServer(skill_set_id=active.id, server_code="mcp.active", name="active", env="dev"),
            SkillSetMCPServer(skill_set_id=inactive.id, server_code="mcp.stale", name="stale", env="dev"),
            BotSkillInstallation(bot_id="bot", owner_id="owner", skill_id=stale_skill.id, env="dev"),
            BotSkillInstallation(bot_id="bot", owner_id="owner", skill_id=direct_skill.id, env="dev"),
            BotMCPInstallation(bot_id="bot", owner_id="owner", server_code="mcp.stale", env="dev"),
            BotMCPInstallation(bot_id="bot", owner_id="owner", server_code="mcp.direct", env="dev"),
        ])

    CapabilityDesiredStateRepository(db).initialize_installations(
        bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
    )

    with db.orm_session() as session:
        assert {row.skill_id for row in session.query(BotSkillInstallation).all()} == {
            active_skill.id, stale_skill.id, direct_skill.id,
        }
        assert {row.server_code for row in session.query(BotMCPInstallation).all()} == {
            "mcp.active", "mcp.stale", "mcp.direct",
        }


def test_flush_does_not_resurrect_a_deactivated_sets_member():
    """A later flush cannot re-add a member after its Set is deactivated."""
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="active", bolt_id="bot", user_id="owner", is_active=True, env="dev"
        )
        skill = Skill(name="member", git_path="git://member", env="dev")
        session.add_all([skill_set, skill])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=skill_set.id, skill_id=skill.id, env="dev"),
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=skill.id, env="dev"
                ),
            ]
        )

    repository = CapabilityDesiredStateRepository(db)
    repository.set_skill_set_active(bot_id="bot", owner_id="owner", set_id="1", active=False)

    plan = repository.flush_installations(bot_id="bot", owner_id="owner", env="dev")

    assert plan.skills_to_install == frozenset()
    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).count() == 0


def test_cross_owner_set_id_is_not_readable_or_mutable_for_shared_default_bot_id():
    db = _Database()
    with db.transactional_orm_session() as session:
        session.add(
            SkillSet(name="owner-b-set", user_id="owner-b", bolt_id="default", env="dev")
        )

    repository = CapabilityDesiredStateRepository(db)
    with pytest.raises(SkillSetControlPlaneNotFoundError):
        repository.get_set(bot_id="default", owner_id="owner-a", set_id="1")
    with pytest.raises(SkillSetControlPlaneNotFoundError):
        repository.update_set(
            bot_id="default",
            owner_id="owner-a",
            set_id="1",
            name="stolen",
            description=None,
        )


def test_global_default_is_immutable_even_for_description_only_update():
    db = _Database()
    with db.transactional_orm_session() as session:
        session.add(
            SkillSet(
                name="system-default",
                user_id="",
                bolt_id="",
                engine_type="openclaw",
                is_default=True,
                description="original",
                env="dev",
            )
        )

    repository = CapabilityDesiredStateRepository(db)
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="SYSTEM_DEFAULT_IMMUTABLE"
    ):
        repository.update_set(
            bot_id="default",
            owner_id="owner-a",
            set_id="1",
            name=None,
            description="must-not-write",
            engine_type="openclaw",
        )
    with db.orm_session() as session:
        assert session.query(SkillSet).one().description == "original"


def test_routed_claude_code_prefers_aicoding_global_default_before_fallback():
    db = _Database()
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                SkillSet(
                    name="aicoding-default",
                    user_id="",
                    bolt_id="",
                    engine_type="aicoding",
                    is_default=True,
                    env="dev",
                ),
                SkillSet(
                    name="claude-fallback",
                    user_id="",
                    bolt_id="",
                    engine_type="claude_code",
                    is_default=True,
                    env="dev",
                ),
            ]
        )

    items = CapabilityDesiredStateRepository(db).list_sets(
        bot_id="bot",
        owner_id="owner",
        engine_type="claude_code",
        default_engine_types=("aicoding", "claude_code"),
    )

    assert [item["name"] for item in items] == ["aicoding-default"]


def test_mcp_membership_is_independent_for_two_owners_sharing_default_bot_id():
    db = _Database()
    with db.transactional_orm_session() as session:
        owner_a = SkillSet(name="a", user_id="owner-a", bolt_id="default", env="dev")
        owner_b = SkillSet(name="b", user_id="owner-b", bolt_id="default", env="dev")
        session.add_all([owner_a, owner_b])
        session.flush()
        session.add(
            SkillSetMCPServer(
                skill_set_id=owner_b.id,
                server_code="mcp.weather",
                name="weather",
                user_id="owner-b",
                env="dev",
            )
        )

    result = CapabilityDesiredStateRepository(db).add_mcp(
        platform_default_codes=frozenset(),
        bot_id="default",
        owner_id="owner-a",
        set_id=str(owner_a.id),
        server_code="mcp.weather",
        name="Weather",
        description=None,
        icon=None,
    )

    assert result.changed is True


def test_activation_rolls_back_all_membership_installations_when_nth_insert_fails():
    """No half-selected set can survive a storage failure at member N."""
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(name="set", user_id="owner", bolt_id="bot", is_active=False, env="dev")
        first = Skill(name="one", git_path="git://one", env="dev")
        second = Skill(name="two", git_path="git://two", env="dev")
        session.add_all([skill_set, first, second])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=skill_set.id, skill_id=first.id, env="dev"),
                SkillSetSkill(skill_set_id=skill_set.id, skill_id=second.id, env="dev"),
            ]
        )

    inserts = 0

    def fail_second_install(_mapper, _connection, _target):
        nonlocal inserts
        inserts += 1
        if inserts == 2:
            raise RuntimeError("second installation write failed")

    event.listen(BotSkillInstallation, "before_insert", fail_second_install)
    try:
        with pytest.raises(RuntimeError, match="second installation"):
            CapabilityDesiredStateRepository(db).set_skill_set_active(
                bot_id="bot", owner_id="owner", set_id="1", active=True
            )
    finally:
        event.remove(BotSkillInstallation, "before_insert", fail_second_install)

    with db.orm_session() as session:
        assert session.query(SkillSet).one().is_active is False
        assert session.query(SkillSetSkill).count() == 2
        assert session.query(BotSkillInstallation).count() == 0


def test_activation_rejects_runtime_name_conflict_before_installation_write():
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(name="set", user_id="owner", bolt_id="bot", is_active=False, env="dev")
        active = Skill(name="same", git_path="git://active", env="dev")
        candidate = Skill(name="same", git_path="git://candidate", env="dev")
        session.add_all([skill_set, active, candidate])
        session.flush()
        session.add_all(
            [
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=active.id, env="dev"
                ),
                SkillSetSkill(
                    skill_set_id=skill_set.id,
                    skill_id=candidate.id,
                    env="dev",
                ),
            ]
        )

    with pytest.raises(SkillRuntimeNameConflictError):
        CapabilityDesiredStateRepository(db).set_skill_set_active(
            bot_id="bot", owner_id="owner", set_id="1", active=True
        )

    with db.orm_session() as session:
        assert session.query(SkillSet).one().is_active is False
        installations = session.query(BotSkillInstallation).all()
        assert [row.skill_id for row in installations] == [1]


def test_create_rejects_a_duplicate_name_without_a_durable_replay_record():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)

    first = repository.create_set(
        bot_id="bot",
        owner_id="owner",
        name="set",
        description="description",
    )
    assert first["name"] == "set"
    assert first["is_active"] is True
    with pytest.raises(SkillSetControlPlaneConflictError, match="SKILL_SET_NAME_CONFLICT"):
        repository.create_set(
            bot_id="bot", owner_id="owner", name="set", description="description"
        )
    with db.orm_session() as session:
        assert session.query(SkillSet).count() == 1


def test_default_projection_is_always_active_even_for_historical_false_row():
    db = _Database()
    with db.transactional_orm_session() as session:
        session.add(
            SkillSet(
                name="default",
                user_id="",
                bolt_id="",
                is_default=True,
                is_active=False,
                engine_type="openclaw",
                env="dev",
            )
        )

    repository = CapabilityDesiredStateRepository(db)
    assert repository.list_sets(
        bot_id="bot", owner_id="owner", engine_type="openclaw"
    )[0]["is_active"] is True
    result = repository.set_skill_set_active(
        bot_id="bot",
        owner_id="owner",
        set_id="1",
        active=True,
        engine_type="openclaw",
        default_engine_types=("openclaw",),
    )
    assert result.item["is_active"] is True


def test_database_keeps_historical_cross_skill_set_memberships_compatible():
    db = _Database()
    with db.transactional_orm_session() as session:
        first = SkillSet(name="first", bolt_id="bot", env="dev")
        second = SkillSet(name="second", bolt_id="bot", env="dev")
        skill = Skill(name="skill", git_path="git://skill", env="dev")
        session.add_all([first, second, skill])
        session.flush()
        session.add(
            SkillSetSkill(
                skill_set_id=first.id,
                skill_id=skill.id,
                env="dev",
            )
        )

    with db.transactional_orm_session() as session:
        session.add(SkillSetSkill(skill_set_id=2, skill_id=1, env="dev"))


def test_database_allows_system_default_and_one_ordinary_membership():
    db = _Database()
    with db.transactional_orm_session() as session:
        default = SkillSet(name="default", bolt_id="bot", is_default=True, env="dev")
        ordinary = SkillSet(name="ordinary", bolt_id="bot", env="dev")
        skill = Skill(name="skill", git_path="local://skill", env="dev")
        session.add_all([default, ordinary, skill])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=default.id,
                    skill_id=skill.id,
                    env="dev",
                ),
                SkillSetSkill(
                    skill_set_id=ordinary.id,
                    skill_id=skill.id,
                    env="dev",
                ),
            ]
        )


def test_skill_set_name_is_unique_for_bot_across_engines():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    repository.create_set(
        bot_id="bot",
        owner_id="owner",
        name="set",
        description=None,
        engine_type="openclaw",
    )

    with pytest.raises(
        SkillSetControlPlaneConflictError, match="SKILL_SET_NAME_CONFLICT"
    ):
        repository.create_set(
            bot_id="bot",
            owner_id="owner",
            name="set",
            description=None,
            engine_type="hermes",
        )


def test_skill_set_rename_is_unique_for_bot_across_engines():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    repository.create_set(
        bot_id="bot",
        owner_id="owner",
        name="openclaw-set",
        description=None,
        engine_type="openclaw",
    )
    repository.create_set(
        bot_id="bot",
        owner_id="owner",
        name="hermes-set",
        description=None,
        engine_type="hermes",
    )

    with pytest.raises(
        SkillSetControlPlaneConflictError, match="SKILL_SET_NAME_CONFLICT"
    ):
        repository.update_set(
                bot_id="bot",
                owner_id="owner",
            set_id="2",
            name="openclaw-set",
            description=None,
            engine_type="hermes",
        )


def test_legacy_resolver_keeps_bot_scope_and_suffix_matching_for_existing_repo_skills():
    """Historical name/path references must not select another Bot's asset."""
    db = _Database()
    with db.transactional_orm_session() as session:
        target = Skill(
            name="same-name",
            git_path="git://market/example",
            bolt_id="bot-a",
            env="dev",
        )
        other = Skill(
            name="same-name",
            git_path="git://other/example",
            bolt_id="bot-b",
            env="dev",
        )
        session.add_all([target, other])
        session.flush()

    repository = CapabilityDesiredStateRepository(db)

    assert repository.resolve_legacy_skill_id(
        bot_id="bot-a", identifier="same-name"
    ) == str(target.id)
    assert repository.resolve_legacy_skill_id(
        bot_id="bot-a", identifier="market/example"
    ) == str(target.id)
