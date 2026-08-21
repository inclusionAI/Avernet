"""Atomicity tests for the canonical SkillSet desired-state UoW."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
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
from agentclaw.community.core.repository.implementations.skill_center.skill_set_control_plane import (
    SkillSetControlPlaneRepository,
)
from agentclaw.community.core.skill_center.errors import (
    SkillRuntimeNameConflictError,
    SkillSetControlPlaneConflictError,
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

    items = SkillSetControlPlaneRepository(db).list_sets(
        bot_id="default", owner_id="owner-a", engine_type="openclaw"
    )

    assert [item["name"] for item in items] == ["system-default", "mine"]


def test_activation_rolls_back_all_membership_installations_when_nth_insert_fails():
    """No half-selected set can survive a storage failure at member N."""
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(name="set", bolt_id="bot", is_active=False, env="dev")
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
            SkillSetControlPlaneRepository(db).set_active(
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
        skill_set = SkillSet(name="set", bolt_id="bot", is_active=False, env="dev")
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
        SkillSetControlPlaneRepository(db).set_active(
            bot_id="bot", owner_id="owner", set_id="1", active=True
        )

    with db.orm_session() as session:
        assert session.query(SkillSet).one().is_active is False
        installations = session.query(BotSkillInstallation).all()
        assert [row.skill_id for row in installations] == [1]


def test_legacy_switch_rejects_direct_runtime_name_conflict_before_writes():
    db = _Database()
    with db.transactional_orm_session() as session:
        target = SkillSet(name="target", bolt_id="bot", is_active=False, env="dev")
        direct = Skill(name="same", git_path="git://direct", env="dev")
        candidate = Skill(name="same", git_path="git://candidate", env="dev")
        session.add_all([target, direct, candidate])
        session.flush()
        session.add_all(
            [
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=direct.id, env="dev"
                ),
                SkillSetSkill(
                    skill_set_id=target.id,
                    skill_id=candidate.id,
                    env="dev",
                ),
            ]
        )

    with pytest.raises(SkillRuntimeNameConflictError):
        SkillSetControlPlaneRepository(db).replace_active_set(
            bot_id="bot", owner_id="owner", set_id="1"
        )

    with db.orm_session() as session:
        assert session.query(SkillSet).one().is_active is False
        installations = session.query(BotSkillInstallation).all()
        assert [row.skill_id for row in installations] == [1]


def test_create_rejects_a_duplicate_name_without_a_durable_replay_record():
    db = _Database()
    repository = SkillSetControlPlaneRepository(db)

    first = repository.create_set(
        bot_id="bot",
        owner_id="owner",
        name="set",
        description="description",
    )
    assert first["name"] == "set"
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
                user_id="owner",
                bolt_id="bot",
                is_default=True,
                is_active=False,
                env="dev",
            )
        )

    repository = SkillSetControlPlaneRepository(db)
    assert repository.list_sets(bot_id="bot", owner_id="owner")[0]["is_active"] is True
    result = repository.set_active(
        bot_id="bot", owner_id="owner", set_id="1", active=True
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
    repository = SkillSetControlPlaneRepository(db)
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(name="set", bolt_id="bot", is_active=True, env="dev")
        session.add(skill_set)
        session.flush()

    added = repository.add_mcp(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id), server_code="mcp.weather"
    )
    assert added.changed is True
    with db.orm_session() as session:
        assert session.query(SkillSetMCPServer).count() == 1
        assert {
            row.server_code for row in session.query(BotMCPInstallation).all()
        } == {"mcp.weather"}

    removed = repository.remove_mcp(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id), server_code="mcp.weather"
    )
    assert removed.changed is True
    with db.orm_session() as session:
        assert session.query(SkillSetMCPServer).count() == 0
        assert session.query(BotMCPInstallation).count() == 0


def test_mcp_direct_and_skill_set_ownership_conflicts_are_enforced():
    db = _Database()
    repository = SkillSetControlPlaneRepository(db)
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(name="set", bolt_id="bot", is_active=False, env="dev")
        session.add(skill_set)
        session.flush()

    assert repository.activate_mcp_direct(
        bot_id="bot", owner_id="owner", server_code="mcp.weather"
    ).changed
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="RESOURCE_DIRECT_ACTIVE"
    ):
        repository.add_mcp(
            bot_id="bot", owner_id="owner", set_id=str(skill_set.id), server_code="mcp.weather"
        )
    assert repository.deactivate_mcp_direct(
        bot_id="bot", owner_id="owner", server_code="mcp.weather"
    ).changed
    assert repository.add_mcp(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id), server_code="mcp.weather"
    ).changed
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="RESOURCE_MANAGED_BY_SKILL_SET"
    ):
        repository.activate_mcp_direct(
            bot_id="bot", owner_id="owner", server_code="mcp.weather"
        )


def test_direct_mcp_installation_isolated_by_owner_for_shared_bot_id():
    db = _Database()
    repository = SkillSetControlPlaneRepository(db)

    assert repository.activate_mcp_direct(
        bot_id="default", owner_id="owner-a", server_code="mcp.weather"
    ).changed
    assert repository.activate_mcp_direct(
        bot_id="default", owner_id="owner-b", server_code="mcp.weather"
    ).changed
    assert repository.deactivate_mcp_direct(
        bot_id="default", owner_id="owner-a", server_code="mcp.weather"
    ).changed

    with db.orm_session() as session:
        rows = session.query(BotMCPInstallation).all()
        assert [(row.owner_id, row.bot_id, row.server_code) for row in rows] == [
            ("owner-b", "default", "mcp.weather")
        ]


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
    repository = SkillSetControlPlaneRepository(db)
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
    repository = SkillSetControlPlaneRepository(db)
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
            set_id="2",
            name="openclaw-set",
            description=None,
            engine_type="hermes",
        )


def test_legacy_switch_replaces_all_ordinary_active_sets_in_one_uow():
    """The compatibility switch cannot expose a deactivate/activate gap."""
    db = _Database()
    with db.transactional_orm_session() as session:
        old_set = SkillSet(name="old", bolt_id="bot", is_active=True, env="dev")
        target_set = SkillSet(name="target", bolt_id="bot", is_active=False, env="dev")
        old_skill = Skill(name="old-skill", git_path="git://old", env="dev")
        target_skill = Skill(name="target-skill", git_path="git://target", env="dev")
        session.add_all([old_set, target_set, old_skill, target_skill])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=old_set.id, skill_id=old_skill.id, env="dev"
                ),
                SkillSetSkill(
                    skill_set_id=target_set.id, skill_id=target_skill.id, env="dev"
                ),
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=old_skill.id, env="dev"
                ),
            ]
        )

    result = SkillSetControlPlaneRepository(db).replace_active_set(
        bot_id="bot", owner_id="owner", set_id="2"
    )

    assert result.changed is True
    assert result.item["id"] == "2"
    assert result.details == {"activated": ["2"], "deactivated": ["1"]}
    with db.orm_session() as session:
        sets = {row.name: row.is_active for row in session.query(SkillSet).all()}
        installed = {row.skill_id for row in session.query(BotSkillInstallation).all()}
    assert sets == {"old": False, "target": True}
    assert installed == {2}


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

    repository = SkillSetControlPlaneRepository(db)

    assert repository.resolve_legacy_skill_id(
        bot_id="bot-a", identifier="same-name"
    ) == str(target.id)
    assert repository.resolve_legacy_skill_id(
        bot_id="bot-a", identifier="market/example"
    ) == str(target.id)
