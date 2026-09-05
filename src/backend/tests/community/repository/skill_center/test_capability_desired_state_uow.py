"""Atomicity tests for the canonical SkillSet desired-state UoW."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.models.mcp import (
    BotMCPInstallation,
    SkillSetMCPServer,
)
from agentclaw.community.core.models.space_skill import SkillVersion
from agentclaw.community.core.skill_center.orm import (
    DefaultSkillsetMcpExclusion,
    DefaultSkillsetSkillExclusion,
)
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.skill_center.errors import (
    SkillOfflineError,
    SkillRuntimeNameConflictError,
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.core.skill_center.legacy_skill_set_compatibility import (
    LegacySkillSetScope,
)


class _Database:
    def __init__(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def orm_session(self):
        with self.transactional_orm_session() as session:
            yield session

    @contextmanager
    def transactional_orm_session(self):
        session = self.sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


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
    assert plan.skills_to_uninstall == frozenset({excluded_skill.id})
    assert plan.mcps_to_install == frozenset({"mcp.default", "mcp.shared"})
    assert plan.mcps_to_uninstall == frozenset({"mcp.excluded"})
    with db.orm_session() as session:
        assert {row.skill_id for row in session.query(BotSkillInstallation).all()} == {
            default_skill.id, shared_skill.id, direct_skill.id, former_default_skill.id,
        }
        assert {row.server_code for row in session.query(BotMCPInstallation).all()} == {
            "mcp.default", "mcp.shared",
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


def test_active_skill_set_mutates_mcp_membership_and_installation_atomically():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(name="set", user_id="owner", bolt_id="bot", is_active=True, env="dev")
        session.add(skill_set)
        session.flush()

    added = repository.add_mcp(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id), server_code="mcp.weather",
        name="Weather MCP", description="Weather tools",
        icon="https://example.test/weather.png",
    )
    assert added.changed is True
    assert added.mcp_codes == frozenset({"mcp.weather"})
    unchanged_add = repository.add_mcp(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        server_code="mcp.weather", name="Weather MCP",
        description="Weather tools", icon="https://example.test/weather.png",
    )
    assert unchanged_add.changed is False
    assert unchanged_add.mcp_codes == frozenset()
    with db.orm_session() as session:
        membership = session.query(SkillSetMCPServer).one()
        assert (membership.name, membership.description, membership.icon) == (
            "Weather MCP", "Weather tools", "https://example.test/weather.png",
        )
        assert {
            row.server_code for row in session.query(BotMCPInstallation).all()
        } == {"mcp.weather"}

    removed = repository.remove_mcp(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id), server_code="mcp.weather"
    )
    assert removed.changed is True
    assert removed.mcp_codes == frozenset({"mcp.weather"})
    unchanged_remove = repository.remove_mcp(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        server_code="mcp.weather",
    )
    assert unchanged_remove.changed is False
    assert unchanged_remove.mcp_codes == frozenset()
    with db.orm_session() as session:
        assert session.query(SkillSetMCPServer).count() == 0
        assert session.query(BotMCPInstallation).count() == 0


def test_mcp_direct_and_skill_set_ownership_conflicts_are_enforced():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(name="set", user_id="owner", bolt_id="bot", is_active=False, env="dev")
        session.add(skill_set)
        session.flush()

    assert repository.install_mcp(
        bot_id="bot", owner_id="owner", server_code="mcp.weather",
        platform_default_codes=frozenset(),
    ).changed
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="RESOURCE_DIRECT_ACTIVE"
    ):
        repository.add_mcp(
            bot_id="bot", owner_id="owner", set_id=str(skill_set.id), server_code="mcp.weather",
            name="Weather", description=None, icon=None,
        )
    assert repository.uninstall_mcp(
        bot_id="bot", owner_id="owner", server_code="mcp.weather",
        platform_default_codes=frozenset(),
    ).changed
    assert repository.add_mcp(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id), server_code="mcp.weather",
        name="Weather", description=None, icon=None,
    ).changed
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="RESOURCE_MANAGED_BY_SKILL_SET"
    ):
        repository.install_mcp(
            bot_id="bot", owner_id="owner", server_code="mcp.weather",
            platform_default_codes=frozenset(),
        )


def test_platform_default_mcp_refuses_direct_install_and_uninstall():
    repository = CapabilityDesiredStateRepository(_Database())

    for command in (repository.install_mcp, repository.uninstall_mcp):
        with pytest.raises(
            SkillSetControlPlaneConflictError,
            match="RESOURCE_MANAGED_BY_PLATFORM_POLICY",
        ):
            command(
                bot_id="bot",
                owner_id="owner",
                server_code="mcp.policy",
                platform_default_codes=frozenset({"mcp.policy"}),
            )


def test_direct_mcp_installation_isolated_by_owner_for_shared_bot_id():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)

    assert repository.install_mcp(
        bot_id="default", owner_id="owner-a", server_code="mcp.weather",
        platform_default_codes=frozenset(),
    ).changed
    assert repository.install_mcp(
        bot_id="default", owner_id="owner-b", server_code="mcp.weather",
        platform_default_codes=frozenset(),
    ).changed
    assert repository.uninstall_mcp(
        bot_id="default", owner_id="owner-a", server_code="mcp.weather",
        platform_default_codes=frozenset(),
    ).changed

    with db.orm_session() as session:
        rows = session.query(BotMCPInstallation).all()
        assert [(row.owner_id, row.bot_id, row.server_code) for row in rows] == [
            ("owner-b", "default", "mcp.weather")
        ]


def test_direct_mcp_mutations_name_only_the_code_they_changed():
    repository = CapabilityDesiredStateRepository(_Database())

    installed = repository.install_mcp(
        bot_id="bot", owner_id="owner", server_code="mcp.weather",
        platform_default_codes=frozenset(),
    )
    unchanged_install = repository.install_mcp(
        bot_id="bot", owner_id="owner", server_code="mcp.weather",
        platform_default_codes=frozenset(),
    )
    uninstalled = repository.uninstall_mcp(
        bot_id="bot", owner_id="owner", server_code="mcp.weather",
        platform_default_codes=frozenset(),
    )
    unchanged_uninstall = repository.uninstall_mcp(
        bot_id="bot", owner_id="owner", server_code="mcp.weather",
        platform_default_codes=frozenset(),
    )

    assert installed.mcp_codes == frozenset({"mcp.weather"})
    assert unchanged_install.mcp_codes == frozenset()
    assert uninstalled.mcp_codes == frozenset({"mcp.weather"})
    assert unchanged_uninstall.mcp_codes == frozenset()


def test_skill_set_control_plane_sql_only_adds_owner_scoped_mcp_installation():
    sql_path = (
        Path(__file__).parents[4]
        / "src"
        / "agentclaw"
        / "community"
        / "core"
        / "skill_center"
        / "sql"
        / "2026_08_20_skill_set_control_plane.sql"
    )
    sql = sql_path.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS ac_bot_mcp_installation" in sql
    assert "owner_id VARCHAR(128) NOT NULL" in sql
    assert "(avernet_tenant, env, owner_id, bot_id, server_code)" in sql
    assert "ac_skill_set_create_idempotency" not in sql
    assert "ALTER TABLE ac_skill_set_skill" not in sql
    assert "ALTER TABLE ac_skill_set_mcp" not in sql


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


def _bridge_fixture(db) -> None:
    """One Bot with every kind of Set that reaches it, and two that do not."""
    with db.transactional_orm_session() as session:
        own_active = SkillSet(
            name="own-active",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        # Seeded inactive on purpose: a Default Set is active regardless, the
        # same rule ``skill_set_item`` publishes.
        own_default = SkillSet(
            name="own-default",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_default=True,
            is_active=False,
            env="dev",
        )
        platform_default = SkillSet(
            name="platform-default",
            bolt_id="",
            user_id="",
            engine_type="openclaw",
            is_default=True,
            env="dev",
        )
        other_owner = SkillSet(
            name="other-owner",
            bolt_id="bot",
            user_id="someone-else",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        other_bot = SkillSet(
            name="other-bot",
            bolt_id="another-bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        skills = [
            Skill(name=name, git_path=f"git://{name}", env="dev")
            for name in (
                "in-own-active",
                "in-own-default",
                "in-platform-default",
                "in-other-owner",
                "in-other-bot",
            )
        ]
        session.add_all(
            [own_active, own_default, platform_default, other_owner, other_bot, *skills]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=owner.id, skill_id=skill.id, env="dev")
                for owner, skill in zip(
                    [own_active, own_default, platform_default, other_owner, other_bot],
                    skills,
                    strict=True,
                )
            ]
        )


def _bridge(db, **overrides):
    """Drive the repair, which is what the listing calls, and read its answer."""
    return CapabilityDesiredStateRepository(db).flush_installations(
        **{
            "bot_id": "bot",
            "owner_id": "owner",
            "env": "dev",
            "engine_type": "openclaw",
            **overrides,
        }
    )


def test_repair_reads_every_set_that_reaches_the_bot_and_no_other():
    """Own ordinary, own Default, and the platform Default — nobody else's."""
    db = _Database()
    _bridge_fixture(db)

    bridge = _bridge(db)

    # 1/2/3 are the members of own-active, own-default and platform-default;
    # 4 and 5 belong to another owner's Bot and another Bot, so neither the
    # listing nor the repair may see them.
    assert bridge.member_skill_ids == frozenset({1, 2, 3})
    # own-default carries ``is_active=False`` and is still active here.
    assert bridge.skills_to_install == frozenset({1, 2, 3})
    assert bridge.skills_to_uninstall == frozenset()


def test_repair_marks_an_inactive_ordinary_sets_members_for_removal():
    """An inactive Set's members must not hold an Installation row."""
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="inactive",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=False,
            env="dev",
        )
        skill = Skill(name="member", git_path="git://member", env="dev")
        session.add_all([skill_set, skill])
        session.flush()
        session.add(
            SkillSetSkill(skill_set_id=skill_set.id, skill_id=skill.id, env="dev")
        )

    bridge = _bridge(db)

    # Still listed — an inactive Skill is a Skill the Bot has.
    assert bridge.member_skill_ids == frozenset({1})
    assert bridge.skills_to_install == frozenset()
    assert bridge.skills_to_uninstall == frozenset({1})


def test_repair_lets_an_active_claim_win_over_an_inactive_one():
    """A stale inactive membership must not uninstall a live member."""
    db = _Database()
    with db.transactional_orm_session() as session:
        active = SkillSet(
            name="active",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        inactive = SkillSet(
            name="inactive",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=False,
            env="dev",
        )
        skill = Skill(name="shared", git_path="git://shared", env="dev")
        session.add_all([active, inactive, skill])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=active.id, skill_id=skill.id, env="dev"),
                SkillSetSkill(skill_set_id=inactive.id, skill_id=skill.id, env="dev"),
            ]
        )

    bridge = _bridge(db)

    assert bridge.skills_to_install == frozenset({1})
    assert bridge.skills_to_uninstall == frozenset()


def test_repair_drops_only_the_owners_own_default_exclusions():
    """The exclusion table is the one thing that removes a Skill."""
    db = _Database()
    with db.transactional_orm_session() as session:
        platform_default = SkillSet(
            name="platform-default",
            bolt_id="",
            user_id="",
            engine_type="openclaw",
            is_default=True,
            env="dev",
        )
        ordinary = SkillSet(
            name="ordinary",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        kept = Skill(name="kept", git_path="git://kept", env="dev")
        excluded = Skill(name="excluded", git_path="git://excluded", env="dev")
        ordinary_member = Skill(name="ordinary", git_path="git://ordinary", env="dev")
        session.add_all(
            [platform_default, ordinary, kept, excluded, ordinary_member]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=platform_default.id, skill_id=kept.id, env="dev"
                ),
                SkillSetSkill(
                    skill_set_id=platform_default.id, skill_id=excluded.id, env="dev"
                ),
                SkillSetSkill(
                    skill_set_id=ordinary.id, skill_id=ordinary_member.id, env="dev"
                ),
                DefaultSkillsetSkillExclusion(
                    user_id="owner",
                    bot_id="bot",
                    skill_set_id=platform_default.id,
                    skill_id=excluded.id,
                ),
                # Another Bot's exclusion of the same shared Skill.
                DefaultSkillsetSkillExclusion(
                    user_id="owner",
                    bot_id="another-bot",
                    skill_set_id=platform_default.id,
                    skill_id=kept.id,
                ),
                # An exclusion row naming an ordinary Set. That table is only
                # for Default Sets, so this must not remove anything.
                DefaultSkillsetSkillExclusion(
                    user_id="owner",
                    bot_id="bot",
                    skill_set_id=ordinary.id,
                    skill_id=ordinary_member.id,
                ),
            ]
        )

    bridge = _bridge(db)

    # 1 kept, 2 excluded by this Bot's own row, 3 kept because the exclusion
    # naming an ordinary Set has no effect.
    assert bridge.member_skill_ids == frozenset({1, 3})
    assert bridge.skills_to_install == frozenset({1, 3})
    # Exclusion is the Default Set's per-Bot deactivation: the excluded member
    # is absent from the listing AND must not hold an Installation row
    # (spec 2026-08-24-installation-single-source-of-truth, Key domain rules).
    assert bridge.skills_to_uninstall == frozenset({2})


def test_repair_does_not_branch_on_a_skills_source_prefix():
    """No prefix rule anywhere: a member is a member.

    A Default SkillSet is a legacy internal-API construct carrying ``git://``
    Repo Skills, so a Local member of one does not arise in practice. This
    pins that no source-prefix special case is reintroduced on either kind of
    Set, which would silently change which Skills the repair writes.
    """
    db = _Database()
    with db.transactional_orm_session() as session:
        default = SkillSet(
            name="platform-default",
            bolt_id="",
            user_id="",
            engine_type="openclaw",
            is_default=True,
            env="dev",
        )
        ordinary = SkillSet(
            name="ordinary",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        session.add_all(
            [
                default,
                ordinary,
                Skill(name="local-in-default", git_path="local://a", env="dev"),
                Skill(name="local-in-ordinary", git_path="local://b", env="dev"),
                # A real Center member: identified by uuid, resolved to its
                # highest PUBLISHED version.
                Skill(
                    name="center-in-ordinary",
                    git_path="center://c",
                    skill_uuid="uuid-c",
                    version=1,
                    status="PUBLISHED",
                    env="dev",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=default.id, skill_id=1, env="dev"),
                SkillSetSkill(skill_set_id=ordinary.id, skill_id=2, env="dev"),
                SkillSetSkill(
                    skill_set_id=ordinary.id,
                    skill_id=3,
                    skill_uuid="uuid-c",
                    env="dev",
                ),
            ]
        )

    bridge = _bridge(db)

    assert bridge.member_skill_ids == frozenset({1, 2, 3})
    assert bridge.skills_to_install == frozenset({1, 2, 3})


def test_repair_ignores_a_membership_pointing_outside_the_env():
    """The repair must never install a Skill the listing would refuse to show."""
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="active",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        session.add_all(
            [
                skill_set,
                Skill(name="here", git_path="git://here", env="dev"),
                Skill(name="elsewhere", git_path="git://elsewhere", env="prod"),
            ]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=skill_set.id, skill_id=1, env="dev"),
                SkillSetSkill(skill_set_id=skill_set.id, skill_id=2, env="dev"),
            ]
        )

    bridge = _bridge(db)

    assert bridge.member_skill_ids == frozenset({1})
    assert bridge.skills_to_install == frozenset({1})


def test_repair_writes_exactly_the_difference_membership_implies():
    """Install what an active Set claims, remove what an inactive one lost."""
    db = _Database()
    with db.transactional_orm_session() as session:
        active = SkillSet(
            name="active",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        inactive = SkillSet(
            name="inactive",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=False,
            env="dev",
        )
        session.add_all(
            [
                active,
                inactive,
                Skill(name="missing", git_path="git://missing", env="dev"),
                Skill(name="already", git_path="git://already", env="dev"),
                Skill(name="stale", git_path="git://stale", env="dev"),
            ]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=active.id, skill_id=1, env="dev"),
                SkillSetSkill(skill_set_id=active.id, skill_id=2, env="dev"),
                SkillSetSkill(skill_set_id=inactive.id, skill_id=3, env="dev"),
                # 2 is already installed and 3 should not be; 4 belongs to no
                # Set at all, so the repair must leave it exactly as it is.
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=2, env="dev"
                ),
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=3, env="dev"
                ),
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=4, env="dev"
                ),
            ]
        )

    repository = CapabilityDesiredStateRepository(db)
    bridge = repository.flush_installations(
        bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
    )

    assert bridge.skills_to_install == frozenset({1, 2})
    assert bridge.skills_to_uninstall == frozenset({3})
    with db.orm_session() as session:
        assert {
            row.skill_id for row in session.query(BotSkillInstallation).all()
        } == {1, 2, 4}


def test_repair_is_convergent_and_leaves_another_bots_rows_alone():
    """A second call writes nothing, and the scope never widens past this Bot."""
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="active",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        session.add_all(
            [skill_set, Skill(name="member", git_path="git://member", env="dev")]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=skill_set.id, skill_id=1, env="dev"),
                # Another owner's Bot holds a row for the very same Skill.
                BotSkillInstallation(
                    bot_id="another-bot", owner_id="someone-else", skill_id=1, env="dev"
                ),
            ]
        )

    repository = CapabilityDesiredStateRepository(db)
    repository.flush_installations(
        bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
    )
    with db.orm_session() as session:
        after_first = {
            (row.bot_id, row.owner_id, row.skill_id)
            for row in session.query(BotSkillInstallation).all()
        }

    repository.flush_installations(
        bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
    )
    with db.orm_session() as session:
        after_second = {
            (row.bot_id, row.owner_id, row.skill_id)
            for row in session.query(BotSkillInstallation).all()
        }

    assert after_first == {("bot", "owner", 1), ("another-bot", "someone-else", 1)}
    assert after_second == after_first


def test_repair_takes_no_lock_and_opens_no_transaction_when_converged():
    """The common case is a Bot whose desired state already agrees.

    Phase two exists to serialize against SkillSet mutations, which lock the
    Set row they edit. Paying that on every page of a hot listing would make
    two concurrent reads contend for no reason, so a converged Bot must answer
    from the unlocked read alone.
    """
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="active",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        session.add_all(
            [skill_set, Skill(name="member", git_path="git://member", env="dev")]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=skill_set.id, skill_id=1, env="dev"),
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=1, env="dev"
                ),
            ]
        )

    writes: list[str] = []
    repository = CapabilityDesiredStateRepository(db)
    original = db.transactional_orm_session

    @contextmanager
    def _read_only():
        # This double's orm_session delegates to transactional_orm_session, so
        # pin it to the real one first or the probe below counts reads too.
        with original() as session:
            yield session

    @contextmanager
    def _recording():
        writes.append("transaction")
        with original() as session:
            yield session

    db.orm_session = _read_only
    db.transactional_orm_session = _recording
    try:
        bridge = repository.flush_installations(
            bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
        )
    finally:
        db.transactional_orm_session = original
        del db.orm_session

    assert bridge.skills_to_install == frozenset({1})
    assert writes == []


def test_repair_resolves_again_under_lock_before_it_writes():
    """A Set deactivated after the unlocked read must not be repaired back.

    Phase one can resolve a stale answer. Phase two re-resolves holding the
    Set rows, so what gets written is the membership as it stands at write
    time, not as it stood at read time.
    """
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="active",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        session.add_all(
            [skill_set, Skill(name="member", git_path="git://member", env="dev")]
        )
        session.flush()
        session.add(SkillSetSkill(skill_set_id=skill_set.id, skill_id=1, env="dev"))

    repository = CapabilityDesiredStateRepository(db)
    original = db.transactional_orm_session

    @contextmanager
    def _deactivate_first():
        # Stands in for a concurrent set_active(False) committing between the
        # unlocked resolution and the locked one.
        with original() as session:
            session.query(SkillSet).filter(SkillSet.id == 1).update(
                {"is_active": False}
            )
        db.transactional_orm_session = original
        with original() as session:
            yield session


    db.transactional_orm_session = _deactivate_first
    try:
        bridge = repository.flush_installations(
            bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
        )
    finally:
        db.transactional_orm_session = original

    # Re-resolved under lock: the Set is inactive now, so nothing is installed.
    assert bridge.skills_to_install == frozenset()
    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).count() == 0


def test_repair_survives_a_concurrent_listing_winning_the_same_insert():
    """A lost race is the state the repair wanted, not a 500.

    The row lock serializes two repairs on InnoDB, whose ``FOR UPDATE`` scan
    gap-locks a prefix of ``uk_bot_skill_installation``. Postgres is supported
    too, and its ``READ COMMITTED`` ``FOR UPDATE`` locks only rows that already
    exist — so two listings really can both decide to insert the same missing
    row. ``IntegrityError`` is not in ``ENVELOPE_ERRORS``, so letting it
    propagate would answer an idempotent read with a 500.
    """
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="active",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        session.add_all(
            [
                skill_set,
                Skill(name="raced", git_path="git://raced", env="dev"),
                Skill(name="mine", git_path="git://mine", env="dev"),
            ]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=skill_set.id, skill_id=1, env="dev"),
                SkillSetSkill(skill_set_id=skill_set.id, skill_id=2, env="dev"),
            ]
        )

    repository = CapabilityDesiredStateRepository(db)
    original = db.transactional_orm_session

    @contextmanager
    def _rival_inserts_first():
        # Stands in for a concurrent listing that committed the row for Skill
        # 1 after this repair read the table and before it writes.
        with original() as session:
            session.add(
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=1, env="dev"
                )
            )
        db.transactional_orm_session = original
        with original() as session:
            yield session

    db.transactional_orm_session = _rival_inserts_first
    try:
        bridge = repository.flush_installations(
            bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
        )
    finally:
        db.transactional_orm_session = original

    assert bridge.skills_to_install == frozenset({1, 2})
    with db.orm_session() as session:
        # The lost insert is skipped, and Skill 2's still lands.
        assert {
            row.skill_id for row in session.query(BotSkillInstallation).all()
        } == {1, 2}


def test_flush_removes_an_excluded_members_installation_row():
    """Exclusion deactivates the member — the flush takes its row away.

    An excluded Default-Set member still belongs to the Set (it is NOT handed
    back to direct control); re-activating it means removing the exclusion
    row. Supersedes the 2026-08-23 "left alone in both directions" decision,
    per spec 2026-08-24-installation-single-source-of-truth Key domain rules.
    """
    db = _Database()
    with db.transactional_orm_session() as session:
        platform_default = SkillSet(
            name="platform-default",
            bolt_id="",
            user_id="",
            engine_type="openclaw",
            is_default=True,
            env="dev",
        )
        session.add_all(
            [
                platform_default,
                Skill(name="excluded", git_path="git://excluded", env="dev"),
                Skill(name="kept", git_path="git://kept", env="dev"),
            ]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=platform_default.id, skill_id=1, env="dev"
                ),
                SkillSetSkill(
                    skill_set_id=platform_default.id, skill_id=2, env="dev"
                ),
                DefaultSkillsetSkillExclusion(
                    user_id="owner",
                    bot_id="bot",
                    skill_set_id=platform_default.id,
                    skill_id=1,
                ),
                # The row a racing repair inserted before the exclusion landed.
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=1, env="dev"
                ),
            ]
        )

    bridge = _bridge(db)

    # Absent from the listing, claimed inactive, and its stray Installation
    # row removed; the kept member gains the row it was missing.
    assert bridge.member_skill_ids == frozenset({2})
    assert bridge.skills_to_uninstall == frozenset({1})
    with db.orm_session() as session:
        assert {
            row.skill_id for row in session.query(BotSkillInstallation).all()
        } == {2}


def test_an_active_ordinary_set_outranks_a_default_exclusion():
    """Err-safe on malformed two-Set data: an active claim keeps the row.

    R3 keeps a capability in at most one Set, so this state should not arise;
    when historical data presents it anyway, the flush must not uninstall a
    member a live Set accounts for.
    """
    db = _Database()
    with db.transactional_orm_session() as session:
        platform_default = SkillSet(
            name="platform-default",
            bolt_id="",
            user_id="",
            engine_type="openclaw",
            is_default=True,
            env="dev",
        )
        ordinary = SkillSet(
            name="mine",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        session.add_all(
            [
                platform_default,
                ordinary,
                Skill(name="shared", git_path="git://shared", env="dev"),
            ]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=platform_default.id, skill_id=1, env="dev"
                ),
                SkillSetSkill(skill_set_id=ordinary.id, skill_id=1, env="dev"),
                DefaultSkillsetSkillExclusion(
                    user_id="owner",
                    bot_id="bot",
                    skill_set_id=platform_default.id,
                    skill_id=1,
                ),
            ]
        )

    bridge = _bridge(db)

    assert bridge.skills_to_install == frozenset({1})
    assert bridge.skills_to_uninstall == frozenset()
    assert bridge.member_skill_ids == frozenset({1})


def test_repair_resolves_a_center_member_by_uuid_not_by_stored_skill_id():
    """A Center membership names a versioned identity, not a row.

    ``get_skills_in_set_for_env`` — which the runtime projection uses — joins a
    ``center://`` membership by ``skill_uuid``, because the association's
    ``skill_id`` goes stale when the row behind that identity is replaced.
    ``uk_skill_uuid`` keeps one row per identity per env, so resolving by id
    finds nothing rather than the wrong version — the Skill would vanish from
    the listing while the runtime kept running it.
    """
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="active",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        session.add_all(
            [
                skill_set,
                Skill(
                    name="center-current",
                    git_path="center://uuid-a",
                    skill_uuid="uuid-a",
                    version=2,
                    status="PUBLISHED",
                    env="dev",
                ),
            ]
        )
        session.flush()
        # The association still carries the id of the row this identity had
        # before it was replaced.
        session.add(
            SkillSetSkill(
                skill_set_id=skill_set.id,
                skill_id=999,
                skill_uuid="uuid-a",
                env="dev",
            )
        )

    bridge = _bridge(db)

    assert bridge.member_skill_ids == frozenset({1})
    assert bridge.skills_to_install == frozenset({1})
    with db.orm_session() as session:
        assert {
            row.skill_id for row in session.query(BotSkillInstallation).all()
        } == {1}


def test_repair_rejects_a_center_membership_without_its_stable_uuid():
    """A malformed Center membership must not disappear from desired state.

    Runtime projection selects mapping-v3 only after Installation exposes a
    Center asset. Silently resolving this row to no Skill would let an active
    SkillSet report success while publishing only its Local/Repo mappings.
    """
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="active",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        skill = Skill(
            name="center",
            git_path="center://public-center",
            skill_uuid="stable-center-uuid",
            version=1,
            status="PUBLISHED",
            env="dev",
        )
        session.add_all([skill_set, skill])
        session.flush()
        session.add(
            SkillSetSkill(
                skill_set_id=skill_set.id,
                skill_id=skill.id,
                skill_uuid=None,
                env="dev",
            )
        )

    repository = CapabilityDesiredStateRepository(db)

    with pytest.raises(
        SkillSetControlPlaneConflictError,
        match="CENTER_MEMBERSHIP_IDENTITY_MISSING",
    ):
        repository.flush_installations(
            bot_id="bot",
            owner_id="owner",
            env="dev",
            engine_type="openclaw",
        )

    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).count() == 0


class _FailingInsertSession:
    """A session whose BotSkillInstallation insert fails without persisting.

    Stands in for an integrity failure that is *not* a lost race — the
    referenced Skill deleted between resolution and insert, say. Simulated
    rather than provoked through SQLite foreign keys, which are off by default
    and whose enforcement would otherwise make the failure come from the wrong
    statement.
    """

    def __init__(self, session) -> None:
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def add(self, instance):
        if isinstance(instance, BotSkillInstallation):
            raise IntegrityError("insert", {}, Exception("not a duplicate"))
        return self._session.add(instance)

    @contextmanager
    def begin_nested(self):
        yield


def test_repair_re_raises_an_integrity_error_that_is_not_a_lost_race():
    """Only a concurrent winner is recoverable; anything else must surface.

    Swallowing every ``IntegrityError`` would let the repair report success for
    a row it never wrote — the transaction commits its other changes, the GET
    answers normally, and the ``active`` it returns contradicts the bridge it
    just returned.
    """
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="active",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        session.add_all(
            [skill_set, Skill(name="member", git_path="git://member", env="dev")]
        )
        session.flush()
        session.add(SkillSetSkill(skill_set_id=skill_set.id, skill_id=1, env="dev"))

    repository = CapabilityDesiredStateRepository(db)
    original = db.transactional_orm_session

    @contextmanager
    def _failing_write():
        with original() as session:
            yield _FailingInsertSession(session)

    db.transactional_orm_session = _failing_write
    try:
        with pytest.raises(IntegrityError):
            repository.flush_installations(
                bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
            )
    finally:
        db.transactional_orm_session = original

    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).count() == 0


def test_repair_ignores_a_membership_row_from_another_environment():
    """The association carries its own env, and the runtime honours it.

    ``get_skills_in_set_for_env`` filters ``SkillSetSkill.env``; bridging a row
    it ignores would materialize an Installation the runtime's Set projection
    does not recognise, which the direct-install half would then act on.
    """
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="active",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        session.add_all(
            [
                skill_set,
                Skill(name="here", git_path="git://here", env="dev"),
                Skill(name="also-here", git_path="git://also-here", env="dev"),
            ]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=skill_set.id, skill_id=1, env="dev"),
                # Same Set, same-env Skill, but the association is another
                # environment's row.
                SkillSetSkill(skill_set_id=skill_set.id, skill_id=2, env="prod"),
            ]
        )

    bridge = _bridge(db)

    assert bridge.member_skill_ids == frozenset({1})
    with db.orm_session() as session:
        assert {
            row.skill_id for row in session.query(BotSkillInstallation).all()
        } == {1}


def _stale_center_membership(db) -> None:
    """One ordinary Set whose Center membership points at a replaced row."""
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                SkillSet(
                    name="active",
                    bolt_id="bot",
                    user_id="owner",
                    engine_type="openclaw",
                    is_active=True,
                    env="dev",
                ),
                Skill(
                    name="center-current",
                    git_path="center://uuid-a",
                    skill_uuid="uuid-a",
                    version=2,
                    status="PUBLISHED",
                    env="dev",
                ),
            ]
        )
        session.flush()
        session.add(
            SkillSetSkill(
                skill_set_id=1, skill_id=999, skill_uuid="uuid-a", env="dev"
            )
        )


def test_deactivating_a_set_removes_the_center_version_the_repair_installed():
    """Otherwise a successful deactivation leaves the Skill running.

    The repair installs the row ``skill_uuid`` resolves to. Reading the id off
    the association instead would delete the replaced version's id — a row that
    does not exist — and leave the current one installed, which the runtime's
    direct-install half then keeps active.
    """
    db = _Database()
    _stale_center_membership(db)
    repository = CapabilityDesiredStateRepository(db)
    repository.flush_installations(
        bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
    )
    with db.orm_session() as session:
        assert {
            row.skill_id for row in session.query(BotSkillInstallation).all()
        } == {1}

    repository.set_skill_set_active(
        bot_id="bot", owner_id="owner", set_id=1, active=False, engine_type="openclaw"
    )

    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).count() == 0


def test_activating_a_set_installs_the_center_version_the_repair_would():
    """The mutation and the repair must name the same row, in both directions."""
    db = _Database()
    _stale_center_membership(db)
    with db.transactional_orm_session() as session:
        session.query(SkillSet).filter(SkillSet.id == 1).one().is_active = False
    repository = CapabilityDesiredStateRepository(db)

    repository.set_skill_set_active(
        bot_id="bot", owner_id="owner", set_id=1, active=True, engine_type="openclaw"
    )

    with db.orm_session() as session:
        assert {
            row.skill_id for row in session.query(BotSkillInstallation).all()
        } == {1}


def _offlined_center_member(db) -> None:
    """An active Set holding a Center member that has since gone OFFLINE."""
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                SkillSet(
                    name="active",
                    bolt_id="bot",
                    user_id="owner",
                    engine_type="openclaw",
                    is_active=True,
                    env="dev",
                ),
                Skill(
                    name="center-a",
                    git_path="center://uuid-a",
                    skill_uuid="uuid-a",
                    mcp_dependencies="{malformed",
                    version=1,
                    status="PUBLISHED",
                    env="dev",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=1, skill_id=1, skill_uuid="uuid-a", env="dev"
                ),
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=1, env="dev"
                ),
            ]
        )
    with db.transactional_orm_session() as session:
        session.query(Skill).filter(Skill.id == 1).one().status = "OFFLINE"


def test_deactivating_a_set_retires_a_member_that_no_longer_resolves():
    """Otherwise the Skill is stuck on, with nothing able to turn it off.

    An `OFFLINE` Center member resolves to nothing, so a teardown driven by the
    resolver alone skips its Installation row — and the runtime keeps
    projecting that row as a direct install. The membership still stands, so
    the guards refuse to let its owner deactivate it by hand either.
    """
    db = _Database()
    _offlined_center_member(db)

    CapabilityDesiredStateRepository(db).set_skill_set_active(
        bot_id="bot",
        owner_id="owner",
        set_id="1",
        active=False,
        engine_type="openclaw",
    )

    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).count() == 0


def test_activating_a_set_still_installs_only_what_resolves():
    """The other half of that asymmetry: never install an unpublished Skill."""
    db = _Database()
    _offlined_center_member(db)
    with db.transactional_orm_session() as session:
        session.query(SkillSet).filter(SkillSet.id == 1).one().is_active = False
        session.query(BotSkillInstallation).delete()

    CapabilityDesiredStateRepository(db).set_skill_set_active(
        bot_id="bot",
        owner_id="owner",
        set_id="1",
        active=True,
        engine_type="openclaw",
    )

    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).count() == 0


def test_removing_a_member_that_no_longer_resolves_retires_its_row():
    """Same asymmetry on the per-Skill path."""
    db = _Database()
    _offlined_center_member(db)

    CapabilityDesiredStateRepository(db).remove_skill(
        bot_id="bot",
        owner_id="owner",
        set_id="1",
        skill_id="1",
        engine_type="openclaw",
    )

    with db.orm_session() as session:
        assert session.query(SkillSetSkill).count() == 0
        assert session.query(BotSkillInstallation).count() == 0


def test_flush_gives_and_takes_mcp_rows_with_set_activation():
    """MCP Installation follows Set membership exactly as skills do.

    An active Set's MCP member gains its row; an inactive Set's MCP member
    loses the stale one; a directly-installed MCP no Set explains is left
    alone in both directions; a second flush writes nothing.
    """
    db = _Database()
    with db.transactional_orm_session() as session:
        active = SkillSet(
            name="active",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        inactive = SkillSet(
            name="inactive",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=False,
            env="dev",
        )
        session.add_all([active, inactive])
        session.flush()
        session.add_all(
            [
                SkillSetMCPServer(
                    skill_set_id=active.id,
                    server_code="missing-mcp",
                    name="missing-mcp",
                    env="dev",
                ),
                SkillSetMCPServer(
                    skill_set_id=inactive.id,
                    server_code="stale-mcp",
                    name="stale-mcp",
                    env="dev",
                ),
                BotMCPInstallation(
                    bot_id="bot", owner_id="owner", server_code="stale-mcp", env="dev"
                ),
                # Direct desired state: no membership row anywhere.
                BotMCPInstallation(
                    bot_id="bot", owner_id="owner", server_code="direct-mcp", env="dev"
                ),
            ]
        )

    plan = _bridge(db)

    assert plan.mcps_to_install == frozenset({"missing-mcp"})
    assert plan.mcps_to_uninstall == frozenset({"stale-mcp"})
    with db.orm_session() as session:
        assert {
            row.server_code for row in session.query(BotMCPInstallation).all()
        } == {"missing-mcp", "direct-mcp"}

    _bridge(db)
    with db.orm_session() as session:
        assert {
            row.server_code for row in session.query(BotMCPInstallation).all()
        } == {"missing-mcp", "direct-mcp"}


def test_flush_removes_an_excluded_default_mcp_members_row():
    """Default-Set MCP exclusion deactivates the member, like skills."""
    db = _Database()
    with db.transactional_orm_session() as session:
        platform_default = SkillSet(
            name="platform-default",
            bolt_id="",
            user_id="",
            engine_type="openclaw",
            is_default=True,
            env="dev",
        )
        session.add(platform_default)
        session.flush()
        session.add_all(
            [
                SkillSetMCPServer(
                    skill_set_id=platform_default.id,
                    server_code="kept-mcp",
                    name="kept-mcp",
                    env="dev",
                ),
                SkillSetMCPServer(
                    skill_set_id=platform_default.id,
                    server_code="excluded-mcp",
                    name="excluded-mcp",
                    env="dev",
                ),
                DefaultSkillsetMcpExclusion(
                    user_id="owner",
                    bot_id="bot",
                    skill_set_id=platform_default.id,
                    server_code="excluded-mcp",
                ),
                # The row a racing flush inserted before the exclusion landed.
                BotMCPInstallation(
                    bot_id="bot",
                    owner_id="owner",
                    server_code="excluded-mcp",
                    env="dev",
                ),
            ]
        )

    plan = _bridge(db)

    assert plan.mcps_to_install == frozenset({"kept-mcp"})
    assert plan.mcps_to_uninstall == frozenset({"excluded-mcp"})
    with db.orm_session() as session:
        assert {
            row.server_code for row in session.query(BotMCPInstallation).all()
        } == {"kept-mcp"}


def test_a_default_set_member_cannot_join_an_ordinary_set():
    """R3 covers ANY Set: the Default included, its members excluded or not."""
    db = _Database()
    with db.transactional_orm_session() as session:
        default_set = SkillSet(
            name="defaults",
            user_id="",
            bolt_id="",
            engine_type="openclaw",
            is_default=True,
            is_active=True,
            env="dev",
        )
        ordinary = SkillSet(
            name="mine",
            user_id="owner",
            bolt_id="bot",
            engine_type="openclaw",
            is_active=False,
            env="dev",
        )
        member = Skill(name="member", git_path="git://defaults/member", env="dev")
        excluded = Skill(
            name="excluded", git_path="git://defaults/excluded", env="dev"
        )
        session.add_all([default_set, ordinary, member, excluded])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=default_set.id, skill_id=member.id, env="dev"
                ),
                SkillSetSkill(
                    skill_set_id=default_set.id, skill_id=excluded.id, env="dev"
                ),
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=member.id, env="dev"
                ),
                DefaultSkillsetSkillExclusion(
                    user_id="owner",
                    bot_id="bot",
                    skill_set_id=int(default_set.id),
                    skill_id=int(excluded.id),
                ),
            ]
        )

    repository = CapabilityDesiredStateRepository(db)

    for skill_id in ("1", "2"):
        with pytest.raises(SkillSetControlPlaneConflictError) as error:
            repository.add_skill(
                bot_id="bot",
                owner_id="owner",
                set_id=str(ordinary.id),
                skill_id=skill_id,
                engine_type="openclaw",
                default_engine_types=("openclaw",),
            )
        assert "RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET" in str(error.value)


def test_direct_active_precedes_membership_when_both_forbid_joining():
    """R2 before R3 — today's error precedence, now encoded in the policy."""
    db = _Database()
    with db.transactional_orm_session() as session:
        other = SkillSet(
            name="other",
            user_id="owner",
            bolt_id="bot",
            engine_type="openclaw",
            is_active=False,
            env="dev",
        )
        target = SkillSet(
            name="target",
            user_id="owner",
            bolt_id="bot",
            engine_type="openclaw",
            is_active=False,
            env="dev",
        )
        skill = Skill(name="both", git_path="git://team/both", env="dev")
        session.add_all([other, target, skill])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=other.id, skill_id=skill.id, env="dev"),
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=skill.id, env="dev"
                ),
            ]
        )

    with pytest.raises(SkillSetControlPlaneConflictError) as error:
        CapabilityDesiredStateRepository(db).add_skill(
            bot_id="bot",
            owner_id="owner",
            set_id=str(target.id),
            skill_id="1",
            engine_type="openclaw",
        )
    assert "RESOURCE_DIRECT_ACTIVE" in str(error.value)


def test_a_default_set_mcp_member_cannot_join_an_ordinary_set():
    """The MCP twin of R3's any-Set coverage."""
    db = _Database()
    with db.transactional_orm_session() as session:
        default_set = SkillSet(
            name="defaults",
            user_id="",
            bolt_id="",
            engine_type="openclaw",
            is_default=True,
            is_active=True,
            env="dev",
        )
        ordinary = SkillSet(
            name="mine",
            user_id="owner",
            bolt_id="bot",
            engine_type="openclaw",
            is_active=False,
            env="dev",
        )
        session.add_all([default_set, ordinary])
        session.flush()
        session.add(
            SkillSetMCPServer(
                skill_set_id=default_set.id,
                server_code="mcp.default-member",
                name="mcp.default-member",
                env="dev",
            )
        )
        session.add(
            BotMCPInstallation(
                bot_id="bot", owner_id="owner", server_code="mcp.default-member", env="dev"
            )
        )

    with pytest.raises(SkillSetControlPlaneConflictError) as error:
        CapabilityDesiredStateRepository(db).add_mcp(
            bot_id="bot",
            owner_id="owner",
            set_id=str(ordinary.id),
            server_code="mcp.default-member",
            name="mcp.default-member",
            description=None,
            icon=None,
            engine_type="openclaw",
            default_engine_types=("openclaw",),
        )
    assert "RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET" in str(error.value)


def test_direct_skill_installation_mirrors_the_mcp_pair():
    """install/uninstall_skill carry the exact contract of the MCP twin."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        skill = Skill(name="tool", git_path="git://tool", env="dev")
        skill_set = SkillSet(
            name="set", user_id="owner", bolt_id="bot", is_active=False, env="dev"
        )
        session.add_all([skill, skill_set])
        session.flush()

    assert repository.install_skill(
        bot_id="bot", owner_id="owner", skill_id=str(skill.id)
    ).changed
    assert not repository.install_skill(
        bot_id="bot", owner_id="owner", skill_id=str(skill.id)
    ).changed
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="RESOURCE_DIRECT_ACTIVE"
    ):
        repository.add_skill(
            bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
            skill_id=str(skill.id),
        )
    assert repository.uninstall_skill(
        bot_id="bot", owner_id="owner", skill_id=str(skill.id)
    ).changed
    assert not repository.uninstall_skill(
        bot_id="bot", owner_id="owner", skill_id=str(skill.id)
    ).changed
    assert repository.add_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    ).changed
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="RESOURCE_MANAGED_BY_SKILL_SET"
    ):
        repository.install_skill(
            bot_id="bot", owner_id="owner", skill_id=str(skill.id)
        )
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="RESOURCE_MANAGED_BY_SKILL_SET"
    ):
        repository.uninstall_skill(
            bot_id="bot", owner_id="owner", skill_id=str(skill.id)
        )


def test_an_excluded_default_member_refuses_direct_control_for_skills_and_mcps():
    """Exclusion is not a hand-back: the member stays Set-managed (R1)."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        default = SkillSet(
            name="default", user_id="", bolt_id="", engine_type="openclaw",
            is_default=True, env="dev",
        )
        skill = Skill(name="member", git_path="git://member", env="dev")
        session.add_all([default, skill])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=default.id, skill_id=skill.id, env="dev"
                ),
                SkillSetMCPServer(
                    skill_set_id=default.id, server_code="mcp.member",
                    name="member", env="dev",
                ),
                DefaultSkillsetSkillExclusion(
                    user_id="owner", bot_id="bot", skill_set_id=default.id,
                    skill_id=skill.id,
                ),
                DefaultSkillsetMcpExclusion(
                    user_id="owner", bot_id="bot", skill_set_id=default.id,
                    server_code="mcp.member",
                ),
            ]
        )

    scope = dict(engine_type="openclaw", default_engine_types=("openclaw",))
    for command, address in [
        (repository.install_skill, dict(skill_id=str(skill.id))),
        (repository.uninstall_skill, dict(skill_id=str(skill.id))),
        (
            repository.install_mcp,
            dict(server_code="mcp.member", platform_default_codes=frozenset()),
        ),
        (
            repository.uninstall_mcp,
            dict(server_code="mcp.member", platform_default_codes=frozenset()),
        ),
    ]:
        with pytest.raises(
            SkillSetControlPlaneConflictError, match="RESOURCE_MANAGED_BY_SKILL_SET"
        ):
            command(bot_id="bot", owner_id="owner", **scope, **address)


def test_direct_skill_installation_validates_existence_and_name_uniqueness():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        active = Skill(name="dup", git_path="git://active", env="dev")
        rival = Skill(name="dup", git_path="git://rival", env="dev")
        foreign = Skill(
            name="foreign", git_path="local://foreign", bolt_id="another-bot",
            user_id="someone", env="dev",
        )
        session.add_all([active, rival, foreign])
        session.flush()

    assert repository.install_skill(
        bot_id="bot", owner_id="owner", skill_id=str(active.id)
    ).changed
    with pytest.raises(SkillRuntimeNameConflictError):
        repository.install_skill(
            bot_id="bot", owner_id="owner", skill_id=str(rival.id)
        )
    with pytest.raises(SkillSetControlPlaneNotFoundError):
        repository.install_skill(bot_id="bot", owner_id="owner", skill_id="999999")
    with pytest.raises(SkillSetControlPlaneNotFoundError):
        repository.install_skill(
            bot_id="bot", owner_id="owner", skill_id=str(foreign.id)
        )


def _seed_default_with_member(db):
    """A platform Default whose member is flushed into Installation."""
    with db.transactional_orm_session() as session:
        default = SkillSet(
            name="default", user_id="", bolt_id="", engine_type="openclaw",
            is_default=True, env="dev",
        )
        skill = Skill(name="member", git_path="git://member", env="dev")
        session.add_all([default, skill])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=default.id, skill_id=skill.id, env="dev"
                ),
                SkillSetMCPServer(
                    skill_set_id=default.id, server_code="mcp.member",
                    name="member", env="dev",
                ),
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=skill.id, env="dev"
                ),
                BotMCPInstallation(
                    bot_id="bot", owner_id="owner", server_code="mcp.member",
                    env="dev",
                ),
            ]
        )
    return default, skill


_DEFAULT_SCOPE = {
    "bot_id": "bot",
    "owner_id": "owner",
    "engine_type": "openclaw",
    "default_engine_types": ("openclaw",),
}


def test_exclusion_retires_the_installation_row_in_one_command():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, skill = _seed_default_with_member(db)

    excluded = repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )

    assert excluded.changed is True
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetSkillExclusion).count() == 1
        assert session.query(BotSkillInstallation).count() == 0
    assert repository.excluded_default_skill_ids(
        bot_id="bot", owner_id="owner", set_id=str(default.id)
    ) == {int(skill.id)}
    # The flush agrees: an excluded member is an inactive claim.
    plan = repository.flush_installations(
        bot_id="bot", owner_id="owner", env="dev",
        engine_type="openclaw", default_engine_types=("openclaw",),
    )
    assert int(skill.id) not in plan.skills_to_install
    # Idempotent retry owns neither half.
    assert not repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    ).changed


def test_unexclusion_restores_the_installation_row_in_one_command():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, skill = _seed_default_with_member(db)
    repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )

    restored = repository.unexclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )

    assert restored.changed is True
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetSkillExclusion).count() == 0
        assert [
            int(row.skill_id) for row in session.query(BotSkillInstallation).all()
        ] == [int(skill.id)]
    assert not repository.unexclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    ).changed


def test_offline_skill_rejects_membership_direct_and_default_restore_before_writes():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, skill = _seed_default_with_member(db)
    repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )
    with db.transactional_orm_session() as session:
        row = session.query(Skill).filter_by(id=skill.id).one()
        row.offline_at = datetime(2026, 8, 30)
        ordinary = SkillSet(
            name="ordinary",
            user_id="owner",
            bolt_id="bot",
            is_active=True,
            env="dev",
        )
        session.add(ordinary)
        session.flush()
        ordinary_id = ordinary.id

    with pytest.raises(SkillOfflineError, match="SKILL_OFFLINE"):
        repository.add_skill(
            bot_id="bot",
            owner_id="owner",
            set_id=str(ordinary_id),
            skill_id=str(skill.id),
        )
    with pytest.raises(SkillOfflineError, match="SKILL_OFFLINE"):
        repository.install_skill(
            bot_id="bot", owner_id="owner", skill_id=str(skill.id)
        )
    with pytest.raises(SkillOfflineError, match="SKILL_OFFLINE"):
        repository.unexclude_default_skill(
            set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
        )

    with db.orm_session() as session:
        assert session.query(SkillSetSkill).filter_by(skill_set_id=ordinary_id).count() == 0
        assert session.query(BotSkillInstallation).count() == 0
        assert session.query(DefaultSkillsetSkillExclusion).count() == 1


def test_unexclusion_fails_closed_on_a_runtime_name_conflict():
    """The name guard aborts the whole command: the exclusion stays."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, skill = _seed_default_with_member(db)
    repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )
    with db.transactional_orm_session() as session:
        rival = Skill(name="member", git_path="git://rival", env="dev")
        session.add(rival)
        session.flush()
        session.add(
            BotSkillInstallation(
                bot_id="bot", owner_id="owner", skill_id=rival.id, env="dev"
            )
        )

    with pytest.raises(SkillRuntimeNameConflictError):
        repository.unexclude_default_skill(
            set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
        )

    with db.orm_session() as session:
        assert session.query(DefaultSkillsetSkillExclusion).count() == 1


def test_mcp_exclusion_mirrors_the_skill_pair():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, _skill = _seed_default_with_member(db)

    excluded = repository.exclude_default_mcp(
        set_id=str(default.id), server_code="mcp.member", **_DEFAULT_SCOPE
    )
    assert excluded.changed is True
    assert excluded.mcp_codes == frozenset({"mcp.member"})
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetMcpExclusion).count() == 1
        assert session.query(BotMCPInstallation).count() == 0
    assert repository.excluded_default_mcp_codes(
        bot_id="bot", owner_id="owner", set_id=str(default.id)
    ) == {"mcp.member"}
    unchanged_exclusion = repository.exclude_default_mcp(
        set_id=str(default.id), server_code="mcp.member", **_DEFAULT_SCOPE
    )
    assert unchanged_exclusion.changed is False
    assert unchanged_exclusion.mcp_codes == frozenset()

    restored = repository.unexclude_default_mcp(
        set_id=str(default.id), server_code="mcp.member", **_DEFAULT_SCOPE
    )
    assert restored.changed is True
    assert restored.mcp_codes == frozenset({"mcp.member"})
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetMcpExclusion).count() == 0
        assert [
            row.server_code for row in session.query(BotMCPInstallation).all()
        ] == ["mcp.member"]


def test_ordinary_remove_mcp_refuses_a_default_set_address() -> None:
    """Default opt-out has one command; membership removal must not duplicate it."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, _skill = _seed_default_with_member(db)

    with pytest.raises(
        SkillSetControlPlaneConflictError, match="SYSTEM_DEFAULT_IMMUTABLE"
    ):
        repository.remove_mcp(
            set_id=str(default.id), server_code="mcp.member", **_DEFAULT_SCOPE
        )

    assert (
        repository.excluded_default_mcp_codes(
            bot_id="bot", owner_id="owner", set_id=str(default.id)
        )
        == set()
    )


def test_exclusion_commands_refuse_an_ordinary_set_address():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        ordinary = SkillSet(
            name="ordinary", user_id="owner", bolt_id="bot",
            engine_type="openclaw", env="dev",
        )
        session.add(ordinary)
        session.flush()

    with pytest.raises(SkillSetControlPlaneNotFoundError):
        repository.exclude_default_skill(
            set_id=str(ordinary.id), skill_id="1", **_DEFAULT_SCOPE
        )
    with pytest.raises(SkillSetControlPlaneNotFoundError):
        repository.unexclude_default_mcp(
            set_id=str(ordinary.id), server_code="mcp.x", **_DEFAULT_SCOPE
        )


def test_excluding_a_never_member_owns_neither_half():
    """No dangling exclusion row, and the wire must not report a change."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, _skill = _seed_default_with_member(db)
    with db.transactional_orm_session() as session:
        stranger = Skill(name="stranger", git_path="git://stranger", env="dev")
        session.add(stranger)
        session.flush()

    for skill_id in (str(stranger.id), "424242", "not-a-number"):
        assert not repository.exclude_default_skill(
            set_id=str(default.id), skill_id=skill_id, **_DEFAULT_SCOPE
        ).changed
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetSkillExclusion).count() == 0
        assert session.query(BotSkillInstallation).count() == 1


def test_excluding_an_unresolvable_member_still_retires_its_installation():
    """An OFFLINE center member's row has no other off switch (R1 refuses the
    direct command); the exclusion is the Default Set's removal path."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        default = SkillSet(
            name="default", user_id="", bolt_id="", engine_type="openclaw",
            is_default=True, env="dev",
        )
        offline = Skill(
            name="offline", git_path="center://offline-uuid",
            skill_uuid="offline-uuid", status="OFFLINE", env="dev",
            mcp_dependencies="{malformed",
        )
        session.add_all([default, offline])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=default.id, skill_id=offline.id,
                    skill_uuid="offline-uuid", env="dev",
                ),
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=offline.id,
                    env="dev",
                ),
            ]
        )

    excluded = repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(offline.id), **_DEFAULT_SCOPE
    )

    assert excluded.changed is True
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetSkillExclusion).count() == 1
        assert session.query(BotSkillInstallation).count() == 0


def test_restore_desired_state_compensates_exclusion_commands():
    """The compensation contract: a restore from the command's own snapshot
    undoes both halves, so a failed projection cannot half-apply — and the
    flush cannot re-apply — an exclusion."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, skill = _seed_default_with_member(db)

    # Exclude, then compensate as the mutation flow would.
    excluded = repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )
    assert excluded.previous_state.skill_exclusions == frozenset()
    repository.restore_desired_state(
        bot_id="bot", owner_id="owner", state=excluded.previous_state,
        engine_type="openclaw",
    )
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetSkillExclusion).count() == 0
        assert session.query(BotSkillInstallation).count() == 1
    plan = repository.flush_installations(
        bot_id="bot", owner_id="owner", env="dev",
        engine_type="openclaw", default_engine_types=("openclaw",),
    )
    assert int(skill.id) in plan.skills_to_install

    # Un-exclude, then compensate: the exclusion row must come back.
    repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )
    restored = repository.unexclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )
    assert restored.previous_state.skill_exclusions == frozenset(
        {(int(default.id), int(skill.id))}
    )
    repository.restore_desired_state(
        bot_id="bot", owner_id="owner", state=restored.previous_state,
        engine_type="openclaw",
    )
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetSkillExclusion).count() == 1
        assert session.query(BotSkillInstallation).count() == 0
    # And the retry the user reaches for after a failed command now works.
    assert repository.unexclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    ).changed


def test_compensation_cannot_restore_reference_after_offline_wins():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, skill = _seed_default_with_member(db)
    previous = repository.snapshot_desired_state(
        bot_id="bot", owner_id="owner", engine_type="openclaw"
    )
    with db.transactional_orm_session() as session:
        session.query(BotSkillInstallation).filter_by(skill_id=skill.id).delete()
        persisted = session.query(Skill).filter_by(id=skill.id).one()
        persisted.offline_at = datetime(2026, 8, 30, 12, 0)
        persisted.offline_by = "owner"

    with pytest.raises(SkillOfflineError, match="SKILL_OFFLINE"):
        repository.restore_desired_state(
            bot_id="bot",
            owner_id="owner",
            state=previous,
            engine_type="openclaw",
        )

    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).count() == 0


def test_excluding_a_stray_mcp_code_owns_neither_half():
    """The MCP twin of the skill never-member gate: no dangling row.

    A code that is neither an association row nor a platform default is a
    stray — a typo or stale UI state. Writing its exclusion anyway would
    pre-exclude the server for this Bot if the platform ever adds it to the
    shared Default Set.
    """
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, _skill = _seed_default_with_member(db)

    refused = repository.exclude_default_mcp(
        set_id=str(default.id), server_code="mcp.stray", **_DEFAULT_SCOPE
    )

    assert refused.changed is False
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetMcpExclusion).count() == 0
        assert session.query(BotMCPInstallation).count() == 1


def test_excluding_a_platform_default_mcp_retires_a_legacy_direct_row():
    """Policy exclusion converges rows written before Direct control was banned.

    Engine/template default MCPs are policy, not association rows, so the
    caller names them; excluding one writes the exclusion row — that row is
    exactly how a Bot opts out of a platform default. Any Installation row for
    the same code is a legacy Direct-control artifact and must be removed or it
    would immediately bypass the exclusion through the installed union half.
    """
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, _skill = _seed_default_with_member(db)
    with db.transactional_orm_session() as session:
        session.add(
            BotMCPInstallation(
                bot_id="bot", owner_id="owner",
                server_code="mcp.platform", env="dev",
            )
        )

    excluded = repository.exclude_default_mcp(
        set_id=str(default.id), server_code="mcp.platform",
        platform_default_codes=frozenset({"mcp.platform"}), **_DEFAULT_SCOPE
    )

    assert excluded.changed is True
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetMcpExclusion).count() == 1
        assert {
            row.server_code for row in session.query(BotMCPInstallation).all()
        } == {"mcp.member"}
    assert repository.excluded_default_mcp_codes(
        bot_id="bot", owner_id="owner", set_id=str(default.id)
    ) == {"mcp.platform"}

    # Idempotent exclusion is also a repair point for a row written by an old
    # process racing this deployment.
    with db.transactional_orm_session() as session:
        session.add(
            BotMCPInstallation(
                bot_id="bot", owner_id="owner",
                server_code="mcp.platform", env="dev",
            )
        )
    repaired = repository.exclude_default_mcp(
        set_id=str(default.id), server_code="mcp.platform",
        platform_default_codes=frozenset({"mcp.platform"}), **_DEFAULT_SCOPE
    )
    assert repaired.changed is True
    with db.orm_session() as session:
        assert {
            row.server_code for row in session.query(BotMCPInstallation).all()
        } == {"mcp.member"}

    restored = repository.unexclude_default_mcp(
        set_id=str(default.id), server_code="mcp.platform", **_DEFAULT_SCOPE
    )
    assert restored.changed is True
    assert restored.mcp_codes == frozenset({"mcp.platform"})


def test_restore_desired_state_preserves_mcp_membership_metadata():
    """Compensation recreates the association row, not a husk of it.

    A failed projection triggers restore for every ordinary Set the Bot has;
    losing name/description/icon there would turn a transient runtime
    failure into permanent metadata corruption on memberships the mutation
    never touched.
    """
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        ordinary = SkillSet(
            name="tools", user_id="owner", bolt_id="bot",
            engine_type="openclaw", is_active=True, env="dev",
        )
        session.add(ordinary)
        session.flush()
        session.add(
            SkillSetMCPServer(
                skill_set_id=ordinary.id, server_code="mcp.rich",
                name="Rich MCP", description="does rich things",
                icon="https://icons.example/rich.png", user_id="owner",
                env="dev",
            )
        )

    removed = repository.remove_mcp(
        set_id=str(ordinary.id), server_code="mcp.rich", **_DEFAULT_SCOPE
    )
    assert removed.changed is True
    repository.restore_desired_state(
        bot_id="bot", owner_id="owner", state=removed.previous_state,
        engine_type="openclaw",
    )

    with db.orm_session() as session:
        row = session.query(SkillSetMCPServer).one()
        assert (row.server_code, row.name, row.description, row.icon, row.user_id) == (
            "mcp.rich",
            "Rich MCP",
            "does rich things",
            "https://icons.example/rich.png",
            "owner",
        )


def test_restore_desired_state_compensates_mcp_exclusion_commands():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, _skill = _seed_default_with_member(db)

    excluded = repository.exclude_default_mcp(
        set_id=str(default.id), server_code="mcp.member", **_DEFAULT_SCOPE
    )
    repository.restore_desired_state(
        bot_id="bot", owner_id="owner", state=excluded.previous_state,
        engine_type="openclaw",
    )
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetMcpExclusion).count() == 0
        assert [
            row.server_code for row in session.query(BotMCPInstallation).all()
        ] == ["mcp.member"]


# ── A Skill mutation names the MCP dependencies it moves ──────────────
#
# The Skill's ``mcp_dependencies`` join or leave the Bot's projected MCP set
# along with the Skill, and the command can only scope its projection if the
# mutation names them. Read under the row lock the transaction already holds,
# for the same reason activation reads its member codes there.


def _seed_skill_with_dependencies(db, dependencies: str | None):
    with db.transactional_orm_session() as session:
        skill = Skill(
            name="dependent",
            git_path="git://dependent",
            env="dev",
            mcp_dependencies=dependencies,
        )
        skill_set = SkillSet(
            name="set", user_id="owner", bolt_id="bot", is_active=True, env="dev"
        )
        session.add_all([skill, skill_set])
        session.flush()
    return skill, skill_set


def _seed_center_skill_with_dependencies(db, dependencies: str):
    with db.transactional_orm_session() as session:
        skill = Skill(
            name="center-dependent",
            git_path="center://center-dependent",
            env="dev",
            status="PUBLISHED",
            skill_uuid="00000000-0000-4000-8000-000000000123",
            mcp_dependencies=None,
        )
        skill_set = SkillSet(
            name="set", user_id="owner", bolt_id="bot", is_active=True, env="dev"
        )
        session.add_all([skill, skill_set])
        session.flush()
        session.add(
            SkillVersion(
                skill_id=skill.id,
                publication_attempt_id=None,
                version_ordinal=1,
                status="PUBLISHED",
                sc_version_number="v1.0",
                sc_skill_id=1001,
                sc_version_id=2001,
                name=skill.name,
                description="Center dependency fixture",
                metadata_json=dependencies,
                published_at=datetime(2026, 9, 1),
                created_by="owner",
                env="dev",
            )
        )
        session.flush()
    return skill, skill_set


def test_add_skill_reports_the_skill_s_mcp_dependencies():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    skill, skill_set = _seed_skill_with_dependencies(
        db, '["mcp.weather", {"server_code": "mcp.maps"}]'
    )

    result = repository.add_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )

    # Both stored shapes decode, through the same decoder the projection uses.
    assert result.mcp_codes == frozenset({"mcp.weather", "mcp.maps"})


def test_add_center_skill_persists_uuid_and_later_activation_installs_it():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        skill = Skill(
            name="center",
            git_path="center://public-center",
            skill_uuid="stable-center-uuid",
            version=1,
            status="PUBLISHED",
            env="dev",
        )
        skill_set = SkillSet(
            name="set",
            user_id="owner",
            bolt_id="bot",
            engine_type="openclaw",
            is_active=False,
            env="dev",
        )
        session.add_all([skill, skill_set])
        session.flush()
        skill_id = str(skill.id)
        set_id = str(skill_set.id)

    repository.add_skill(
        bot_id="bot",
        owner_id="owner",
        set_id=set_id,
        skill_id=skill_id,
        engine_type="openclaw",
    )

    with db.orm_session() as session:
        membership = session.query(SkillSetSkill).one()
        assert membership.skill_uuid == "stable-center-uuid"

    repository.set_skill_set_active(
        bot_id="bot",
        owner_id="owner",
        set_id=set_id,
        active=True,
        engine_type="openclaw",
    )

    with db.orm_session() as session:
        assert {
            installation.skill_id
            for installation in session.query(BotSkillInstallation).all()
        } == {int(skill_id)}


def test_add_skill_reports_no_dependencies_when_the_skill_declares_none():
    """What lets a dependency-free Skill mutation skip the MCP projection."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    skill, skill_set = _seed_skill_with_dependencies(db, None)

    result = repository.add_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )

    assert result.mcp_codes == frozenset()


def test_center_skill_add_and_remove_use_latest_published_version_dependencies():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    skill, skill_set = _seed_center_skill_with_dependencies(
        db,
        '{"mcp_dependencies":[{"code":"mcp.old"}]}',
    )
    with db.transactional_orm_session() as session:
        session.add(
            SkillVersion(
                skill_id=skill.id,
                publication_attempt_id=None,
                version_ordinal=2,
                status="PUBLISHED",
                sc_version_number="v2.0",
                sc_skill_id=1001,
                sc_version_id=2002,
                name=skill.name,
                description="Latest Center dependency fixture",
                metadata_json=(
                    '{"mcp_dependencies":[{"code":"mcp.center"}]}'
                ),
                published_at=datetime(2026, 9, 2),
                created_by="owner",
                env="dev",
            )
        )

    added = repository.add_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )
    removed = repository.remove_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )

    assert added.mcp_codes == frozenset({"mcp.center"})
    assert removed.mcp_codes == frozenset({"mcp.center"})


def test_center_direct_install_and_uninstall_use_version_dependencies():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    skill, _skill_set = _seed_center_skill_with_dependencies(
        db,
        '{"mcp_dependencies":[{"code":"mcp.center"}]}',
    )

    installed = repository.install_skill(
        bot_id="bot", owner_id="owner", skill_id=str(skill.id)
    )
    uninstalled = repository.uninstall_skill(
        bot_id="bot", owner_id="owner", skill_id=str(skill.id)
    )

    assert installed.mcp_codes == frozenset({"mcp.center"})
    assert uninstalled.mcp_codes == frozenset({"mcp.center"})


def test_a_no_op_add_skill_claims_nothing():
    """Re-adding an existing member changes no MCP, so it claims none."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    skill, skill_set = _seed_skill_with_dependencies(db, '["mcp.weather"]')
    repository.add_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )

    result = repository.add_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )

    assert not result.changed
    assert result.mcp_codes == frozenset()


def test_remove_skill_reports_the_dependencies_it_releases():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    skill, skill_set = _seed_skill_with_dependencies(db, '["mcp.weather"]')
    repository.add_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )

    result = repository.remove_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )

    assert result.changed
    assert result.mcp_codes == frozenset({"mcp.weather"})


def test_a_no_op_remove_skill_releases_nothing():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    skill, skill_set = _seed_skill_with_dependencies(db, '["mcp.weather"]')

    result = repository.remove_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )

    assert not result.changed
    assert result.mcp_codes == frozenset()


def test_excluding_a_default_member_releases_its_dependencies():
    """Exclusion is the Default Set's per-Bot deactivation of one member, so
    it moves that member's dependencies exactly as an ordinary remove does."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        default = SkillSet(
            name="default", user_id="", bolt_id="", engine_type="openclaw",
            is_default=True, env="dev",
        )
        skill = Skill(
            name="member", git_path="git://member", env="dev",
            mcp_dependencies='["mcp.weather"]',
        )
        session.add_all([default, skill])
        session.flush()
        session.add_all([
            SkillSetSkill(skill_set_id=default.id, skill_id=skill.id, env="dev"),
            BotSkillInstallation(
                bot_id="bot", owner_id="owner", skill_id=skill.id, env="dev"
            ),
        ])

    excluded = repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )
    assert excluded.mcp_codes == frozenset({"mcp.weather"})

    restored = repository.unexclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )
    assert restored.mcp_codes == frozenset({"mcp.weather"})


def test_center_default_exclusion_uses_published_version_dependencies():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        default = SkillSet(
            name="default", user_id="", bolt_id="", engine_type="openclaw",
            is_default=True, env="dev",
        )
        skill = Skill(
            name="center-default",
            git_path="center://center-default",
            env="dev",
            status="PUBLISHED",
            skill_uuid="00000000-0000-4000-8000-000000000124",
        )
        session.add_all([default, skill])
        session.flush()
        session.add_all([
            SkillVersion(
                skill_id=skill.id,
                publication_attempt_id=None,
                version_ordinal=1,
                status="PUBLISHED",
                sc_version_number="v1.0",
                sc_skill_id=1002,
                sc_version_id=2002,
                name=skill.name,
                description="Center default fixture",
                metadata_json=(
                    '{"mcp_dependencies":[{"code":"mcp.center-default"}]}'
                ),
                published_at=datetime(2026, 9, 1),
                created_by="owner",
                env="dev",
            ),
            SkillSetSkill(
                skill_set_id=default.id,
                skill_id=skill.id,
                skill_uuid=skill.skill_uuid,
                env="dev",
            ),
            BotSkillInstallation(
                bot_id="bot", owner_id="owner", skill_id=skill.id, env="dev"
            ),
        ])

    excluded = repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )
    restored = repository.unexclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )

    assert excluded.mcp_codes == frozenset({"mcp.center-default"})
    assert restored.mcp_codes == frozenset({"mcp.center-default"})
