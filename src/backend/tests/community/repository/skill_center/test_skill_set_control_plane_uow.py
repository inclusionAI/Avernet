"""Atomicity tests for the canonical SkillSet desired-state UoW."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
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
from agentclaw.community.core.skill_center.orm import (
    DefaultSkillsetMcpExclusion,
    DefaultSkillsetSkillExclusion,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_set_control_plane import (
    SkillSetControlPlaneRepository,
)
from agentclaw.community.core.skill_center.errors import (
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


class _InstallationRecorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def install(self, **kwargs) -> bool:
        self.calls.append(kwargs)
        return True


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

    repository = SkillSetControlPlaneRepository(db)

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

    items = SkillSetControlPlaneRepository(db).list_sets(
        bot_id="default", owner_id="owner-a", engine_type="openclaw"
    )

    assert [item["name"] for item in items] == ["system-default", "mine"]


def test_ensure_active_skillset_installations_only_materializes_active_ordinary_members():
    """Legacy active ordinary Sets gain missing active-only Installations once."""
    db = _Database()
    with db.transactional_orm_session() as session:
        active = SkillSet(
            name="active",
            bolt_id="bot",
            user_id="owner",
            is_active=True,
            engine_type="openclaw",
            env="dev",
        )
        inactive = SkillSet(
            name="inactive", bolt_id="bot", user_id="owner", is_active=False, env="dev"
        )
        other_engine = SkillSet(
            name="other-engine",
            bolt_id="bot",
            user_id="owner",
            is_active=True,
            engine_type="aicoding",
            env="dev",
        )
        default = SkillSet(
            name="default",
            bolt_id="bot",
            user_id="owner",
            is_default=True,
            is_active=True,
            env="dev",
        )
        active_skill = Skill(name="active", git_path="git://active", env="dev")
        inactive_skill = Skill(name="inactive", git_path="git://inactive", env="dev")
        other_engine_skill = Skill(
            name="other-engine", git_path="git://other-engine", env="dev"
        )
        default_skill = Skill(name="default", git_path="git://default", env="dev")
        session.add_all(
            [
                active,
                inactive,
                other_engine,
                default,
                active_skill,
                inactive_skill,
                other_engine_skill,
                default_skill,
            ]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=active.id, skill_id=active_skill.id, env="dev"),
                SkillSetSkill(
                    skill_set_id=inactive.id, skill_id=inactive_skill.id, env="dev"
                ),
                SkillSetSkill(
                    skill_set_id=other_engine.id,
                    skill_id=other_engine_skill.id,
                    env="dev",
                ),
                SkillSetSkill(
                    skill_set_id=default.id, skill_id=default_skill.id, env="dev"
                ),
            ]
        )

    repository = SkillSetControlPlaneRepository(db)

    assert (
        repository.ensure_active_skillset_installations(
            bot_id="bot", owner_id="owner", engine_type="openclaw"
        )
        == 1
    )
    assert (
        repository.ensure_active_skillset_installations(
            bot_id="bot", owner_id="owner", engine_type="openclaw"
        )
        == 0
    )
    with db.orm_session() as session:
        assert {row.skill_id for row in session.query(BotSkillInstallation).all()} == {1}


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

    repository = SkillSetControlPlaneRepository(db)

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


def test_remove_skill_from_global_default_writes_owner_bot_exclusion_only():
    db = _Database()
    with db.transactional_orm_session() as session:
        default = SkillSet(
            name="system-default", user_id="", bolt_id="", engine_type="openclaw",
            is_default=True, is_active=True, env="dev",
        )
        skill = Skill(name="default-skill", git_path="git://default-skill", env="dev")
        session.add_all([default, skill])
        session.flush()
        session.add_all([
            SkillSetSkill(skill_set_id=default.id, skill_id=skill.id, env="dev"),
            BotSkillInstallation(
                bot_id="bot", owner_id="owner", skill_id=skill.id, env="dev"
            ),
        ])
        set_id, skill_id = str(default.id), str(skill.id)

    repository = SkillSetControlPlaneRepository(db)
    first = repository.remove_skill(
        bot_id="bot", owner_id="owner", set_id=set_id, skill_id=skill_id,
        engine_type="openclaw",
    )
    second = repository.remove_skill(
        bot_id="bot", owner_id="owner", set_id=set_id, skill_id=skill_id,
        engine_type="openclaw",
    )

    assert first.changed is True
    assert second.changed is False
    with db.orm_session() as session:
        exclusion = session.query(DefaultSkillsetSkillExclusion).one()
        assert (
            exclusion.user_id, exclusion.bot_id,
            exclusion.skill_set_id, exclusion.skill_id,
        ) == ("owner", "bot", int(set_id), int(skill_id))
        assert session.query(SkillSetSkill).count() == 1
        assert session.query(BotSkillInstallation).count() == 0


def test_default_skill_exclusion_preserves_active_ordinary_set_installation():
    db = _Database()
    with db.transactional_orm_session() as session:
        default = SkillSet(
            name="system-default", user_id="", bolt_id="", engine_type="openclaw",
            is_default=True, is_active=True, env="dev",
        )
        ordinary = SkillSet(
            name="ordinary", user_id="owner", bolt_id="bot", engine_type="openclaw",
            is_active=True, env="dev",
        )
        skill = Skill(name="shared", git_path="git://shared", env="dev")
        session.add_all([default, ordinary, skill])
        session.flush()
        session.add_all([
            SkillSetSkill(skill_set_id=default.id, skill_id=skill.id, env="dev"),
            SkillSetSkill(skill_set_id=ordinary.id, skill_id=skill.id, env="dev"),
            BotSkillInstallation(
                bot_id="bot", owner_id="owner", skill_id=skill.id, env="dev"
            ),
        ])
        set_id, skill_id = str(default.id), str(skill.id)

    mutation = SkillSetControlPlaneRepository(db).remove_skill(
        bot_id="bot", owner_id="owner", set_id=set_id, skill_id=skill_id,
        engine_type="openclaw",
    )

    assert mutation.changed is True
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetSkillExclusion).count() == 1
        assert session.query(BotSkillInstallation).count() == 1


def test_remove_mcp_from_global_default_writes_dynamic_default_exclusion_only():
    """Default MCPs are projected, so no ac_skill_set_mcp row is required."""
    db = _Database()
    with db.transactional_orm_session() as session:
        default = SkillSet(
            name="system-default", user_id="", bolt_id="", engine_type="teclaw",
            is_default=True, is_active=True, env="dev",
        )
        session.add(default)
        session.flush()
        session.add(BotMCPInstallation(
            bot_id="bot", owner_id="owner",
            server_code="mcp.dynamic-default", env="dev",
        ))
        set_id = str(default.id)

    repository = SkillSetControlPlaneRepository(db)
    first = repository.remove_mcp(
        bot_id="bot", owner_id="owner", set_id=set_id,
        server_code="mcp.dynamic-default", engine_type="teclaw",
    )
    second = repository.remove_mcp(
        bot_id="bot", owner_id="owner", set_id=set_id,
        server_code="mcp.dynamic-default", engine_type="teclaw",
    )

    assert first.changed is True
    assert second.changed is False
    with db.orm_session() as session:
        exclusion = session.query(DefaultSkillsetMcpExclusion).one()
        assert (
            exclusion.user_id, exclusion.bot_id,
            exclusion.skill_set_id, exclusion.server_code,
        ) == ("owner", "bot", int(set_id), "mcp.dynamic-default")
        assert session.query(SkillSetMCPServer).count() == 0
        assert session.query(BotMCPInstallation).count() == 1


def test_restore_desired_state_restores_default_exclusions():
    db = _Database()
    with db.transactional_orm_session() as session:
        default = SkillSet(
            name="system-default", user_id="", bolt_id="", engine_type="openclaw",
            is_default=True, is_active=True, env="dev",
        )
        first = Skill(name="first", git_path="git://first", env="dev")
        second = Skill(name="second", git_path="git://second", env="dev")
        session.add_all([default, first, second])
        session.flush()
        session.add_all([
            SkillSetSkill(skill_set_id=default.id, skill_id=first.id, env="dev"),
            SkillSetSkill(skill_set_id=default.id, skill_id=second.id, env="dev"),
            DefaultSkillsetSkillExclusion(
                user_id="owner", bot_id="bot", skill_set_id=default.id,
                skill_id=first.id,
            ),
            DefaultSkillsetMcpExclusion(
                user_id="owner", bot_id="bot", skill_set_id=default.id,
                server_code="mcp.existing",
            ),
            BotSkillInstallation(
                bot_id="bot", owner_id="owner", skill_id=second.id, env="dev"
            ),
        ])
        set_id, second_id = str(default.id), str(second.id)

    repository = SkillSetControlPlaneRepository(db)
    mutation = repository.remove_skill(
        bot_id="bot", owner_id="owner", set_id=set_id, skill_id=second_id,
        engine_type="openclaw",
    )
    repository.remove_mcp(
        bot_id="bot", owner_id="owner", set_id=set_id,
        server_code="mcp.new", engine_type="openclaw",
    )
    repository.restore_desired_state(
        bot_id="bot", owner_id="owner", state=mutation.previous_state,
        engine_type="openclaw",
    )

    with db.orm_session() as session:
        assert {
            (row.skill_set_id, row.skill_id)
            for row in session.query(DefaultSkillsetSkillExclusion).all()
        } == {(int(set_id), 1)}
        assert {
            (row.skill_set_id, row.server_code)
            for row in session.query(DefaultSkillsetMcpExclusion).all()
        } == {(int(set_id), "mcp.existing")}
        assert {
            row.skill_id for row in session.query(BotSkillInstallation).all()
        } == {int(second_id)}

def test_ensure_active_skillset_installations_uses_install_repository_upsert_seam():
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
        skill = Skill(name="member", git_path="git://member", env="dev")
        session.add_all([skill_set, skill])
        session.flush()
        session.add(
            SkillSetSkill(skill_set_id=skill_set.id, skill_id=skill.id, env="dev")
        )

    installs = _InstallationRecorder()
    result = SkillSetControlPlaneRepository(
        db, installation_repository=installs
    ).ensure_active_skillset_installations(
        bot_id="bot", owner_id="owner", engine_type="openclaw"
    )

    assert result == 1
    assert installs.calls == [
        {"env": "dev", "owner_id": "owner", "bot_id": "bot", "skill_id": 1}
    ]


def test_ensure_active_skillset_installations_does_not_resurrect_deactivated_set_member():
    """A later projection cannot re-add a member after its Set is deactivated."""
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

    repository = SkillSetControlPlaneRepository(db)
    repository.set_active(bot_id="bot", owner_id="owner", set_id="1", active=False)

    assert (
        repository.ensure_active_skillset_installations(bot_id="bot", owner_id="owner")
        == 0
    )
    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).count() == 0


def test_cross_owner_set_id_is_not_readable_or_mutable_for_shared_default_bot_id():
    db = _Database()
    with db.transactional_orm_session() as session:
        session.add(
            SkillSet(name="owner-b-set", user_id="owner-b", bolt_id="default", env="dev")
        )

    repository = SkillSetControlPlaneRepository(db)
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

    repository = SkillSetControlPlaneRepository(db)
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

    items = SkillSetControlPlaneRepository(db).list_sets(
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

    result = SkillSetControlPlaneRepository(db).add_mcp(
        bot_id="default",
        owner_id="owner-a",
        set_id=str(owner_a.id),
        server_code="mcp.weather",
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
        SkillSetControlPlaneRepository(db).set_active(
            bot_id="bot", owner_id="owner", set_id="1", active=True
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

    repository = SkillSetControlPlaneRepository(db)
    assert repository.list_sets(
        bot_id="bot", owner_id="owner", engine_type="openclaw"
    )[0]["is_active"] is True
    result = repository.set_active(
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
    repository = SkillSetControlPlaneRepository(db)
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(name="set", user_id="owner", bolt_id="bot", is_active=True, env="dev")
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
        skill_set = SkillSet(name="set", user_id="owner", bolt_id="bot", is_active=False, env="dev")
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

    repository = SkillSetControlPlaneRepository(db)

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
    return SkillSetControlPlaneRepository(db).repair_bot_skillset_installations(
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
    assert bridge.members == frozenset({1, 2, 3})
    # own-default carries ``is_active=False`` and is still active here.
    assert bridge.activate == frozenset({1, 2, 3})
    assert bridge.deactivate == frozenset()


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
    assert bridge.members == frozenset({1})
    assert bridge.activate == frozenset()
    assert bridge.deactivate == frozenset({1})


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

    assert bridge.activate == frozenset({1})
    assert bridge.deactivate == frozenset()


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
    assert bridge.members == frozenset({1, 3})
    assert bridge.activate == frozenset({1, 3})
    # The excluded one is absent from the listing, and the repair speaks for it
    # in neither direction — it belongs to direct activate/deactivate now.
    assert bridge.deactivate == frozenset()


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

    assert bridge.members == frozenset({1, 2, 3})
    assert bridge.activate == frozenset({1, 2, 3})


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

    assert bridge.members == frozenset({1})
    assert bridge.activate == frozenset({1})


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

    repository = SkillSetControlPlaneRepository(db)
    bridge = repository.repair_bot_skillset_installations(
        bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
    )

    assert bridge.activate == frozenset({1, 2})
    assert bridge.deactivate == frozenset({3})
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

    repository = SkillSetControlPlaneRepository(db)
    repository.repair_bot_skillset_installations(
        bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
    )
    with db.orm_session() as session:
        after_first = {
            (row.bot_id, row.owner_id, row.skill_id)
            for row in session.query(BotSkillInstallation).all()
        }

    repository.repair_bot_skillset_installations(
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
    repository = SkillSetControlPlaneRepository(db)
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
        bridge = repository.repair_bot_skillset_installations(
            bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
        )
    finally:
        db.transactional_orm_session = original
        del db.orm_session

    assert bridge.activate == frozenset({1})
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

    repository = SkillSetControlPlaneRepository(db)
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
        bridge = repository.repair_bot_skillset_installations(
            bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
        )
    finally:
        db.transactional_orm_session = original

    # Re-resolved under lock: the Set is inactive now, so nothing is installed.
    assert bridge.activate == frozenset()
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

    repository = SkillSetControlPlaneRepository(db)
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
        bridge = repository.repair_bot_skillset_installations(
            bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
        )
    finally:
        db.transactional_orm_session = original

    assert bridge.activate == frozenset({1, 2})
    with db.orm_session() as session:
        # The lost insert is skipped, and Skill 2's still lands.
        assert {
            row.skill_id for row in session.query(BotSkillInstallation).all()
        } == {1, 2}


def test_repair_leaves_an_excluded_members_installation_to_direct_control():
    """An excluded Skill is no longer the Set's to speak for, either way.

    Removing it from a shared Default Set is what makes it directly
    controllable again — ``_set_governs`` stops refusing activate/deactivate
    for exactly that reason. So the repair must not take its Installation row
    away: doing so would undo the owner's command at the next listing, the
    same two-authorities defect this PR exists to remove.
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

    # Absent from the listing, and its Installation row left exactly as the
    # last direct command left it.
    assert bridge.members == frozenset({2})
    assert bridge.deactivate == frozenset()
    with db.orm_session() as session:
        assert {
            row.skill_id for row in session.query(BotSkillInstallation).all()
        } == {1, 2}


def test_an_active_ordinary_set_outranks_a_default_exclusion():
    """Exclusion removes a Skill from *that* Set, not from every Set."""
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

    assert bridge.activate == frozenset({1})
    assert bridge.deactivate == frozenset()
    assert bridge.members == frozenset({1})


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

    assert bridge.members == frozenset({1})
    assert bridge.activate == frozenset({1})
    with db.orm_session() as session:
        assert {
            row.skill_id for row in session.query(BotSkillInstallation).all()
        } == {1}


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

    repository = SkillSetControlPlaneRepository(db)
    original = db.transactional_orm_session

    @contextmanager
    def _failing_write():
        with original() as session:
            yield _FailingInsertSession(session)

    db.transactional_orm_session = _failing_write
    try:
        with pytest.raises(IntegrityError):
            repository.repair_bot_skillset_installations(
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

    assert bridge.members == frozenset({1})
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
    repository = SkillSetControlPlaneRepository(db)
    repository.repair_bot_skillset_installations(
        bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
    )
    with db.orm_session() as session:
        assert {
            row.skill_id for row in session.query(BotSkillInstallation).all()
        } == {1}

    repository.set_active(
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
    repository = SkillSetControlPlaneRepository(db)

    repository.set_active(
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

    SkillSetControlPlaneRepository(db).set_active(
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

    SkillSetControlPlaneRepository(db).set_active(
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

    SkillSetControlPlaneRepository(db).remove_skill(
        bot_id="bot",
        owner_id="owner",
        set_id="1",
        skill_id="1",
        engine_type="openclaw",
    )

    with db.orm_session() as session:
        assert session.query(SkillSetSkill).count() == 0
        assert session.query(BotSkillInstallation).count() == 0
