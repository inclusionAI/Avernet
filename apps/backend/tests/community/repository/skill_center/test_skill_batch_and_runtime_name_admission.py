"""Preserve upstream admission and batch-transaction regression coverage."""

from threading import Event, Thread, current_thread

import pytest
from sqlalchemy import event

from agentclaw.community.core.base import Base
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.skill_center.errors import SkillRuntimeNameConflictError
from .capability_desired_state_support import _Database


def test_skill_batch_rolls_back_prior_members_on_infrastructure_failure():
    db = _Database()
    with db.transactional_orm_session() as session:
        draft = SkillSet(
            name="draft", user_id="owner", bolt_id="bot", is_active=False, env="dev"
        )
        skill = Skill(name="validator", git_path="git://validator", env="dev")
        session.add_all([draft, skill])
        session.flush()
        set_id, skill_id = draft.id, skill.id
    repository = CapabilityDesiredStateRepository(db)
    with pytest.raises(RuntimeError, match="database unavailable"):
        with repository.skill_batch(bot_id="bot", owner_id="owner"):
            repository.add_skill(
                bot_id="bot",
                owner_id="owner",
                set_id=str(set_id),
                skill_id=str(skill_id),
            )
            raise RuntimeError("database unavailable")
    with db.orm_session() as session:
        assert session.query(SkillSetSkill).count() == 0


def test_skill_batch_uses_real_sqlite_plugin_transaction(tmp_path):
    from agentclaw.community.plugins.community.sqlite_database import (
        CommunitySqliteDatabase,
    )

    db = CommunitySqliteDatabase(f"sqlite:///{tmp_path / 'skill-batch.db'}")
    Base.metadata.create_all(db._engine)
    with db.transactional_orm_session() as session:
        session.add(
            BotModel(
                bot_id="bot",
                entity_id="owner",
                owner_id="owner",
                entity_type="staff",
                creator_id="owner",
                env="dev",
            )
        )
        draft = SkillSet(
            name="draft", user_id="owner", bolt_id="bot", is_active=False, env="dev"
        )
        skill = Skill(name="validator", git_path="git://validator", env="dev")
        session.add_all([draft, skill])
        session.flush()
        set_id, skill_id = draft.id, skill.id
    repository = CapabilityDesiredStateRepository(db)
    with pytest.raises(RuntimeError):
        with repository.skill_batch(bot_id="bot", owner_id="owner"):
            repository.add_skill(
                bot_id="bot",
                owner_id="owner",
                set_id=str(set_id),
                skill_id=str(skill_id),
            )
            raise RuntimeError("fail")
    with db.orm_session() as session:
        assert session.query(SkillSetSkill).count() == 0
    with repository.skill_batch(bot_id="bot", owner_id="owner"):
        repository.add_skill(
            bot_id="bot", owner_id="owner", set_id=str(set_id), skill_id=str(skill_id)
        )
    with db.orm_session() as session:
        assert session.query(SkillSetSkill).count() == 1


def test_real_sqlite_plugin_serializes_competing_skill_writes(tmp_path):
    from agentclaw.community.plugins.community.sqlite_database import (
        CommunitySqliteDatabase,
    )

    db = CommunitySqliteDatabase(f"sqlite:///{tmp_path / 'concurrency.db'}")
    Base.metadata.create_all(db._engine)
    with db.transactional_orm_session() as session:
        session.add(
            BotModel(
                bot_id="bot",
                entity_id="owner",
                owner_id="owner",
                entity_type="staff",
                creator_id="owner",
                env="dev",
            )
        )
        active = SkillSet(
            name="active", user_id="owner", bolt_id="bot", is_active=True, env="dev"
        )
        first = Skill(name="validator", git_path="git://first", env="dev")
        second = Skill(name="validator", git_path="git://second", env="dev")
        session.add_all([active, first, second])
        session.flush()
        set_id, first_id, second_id = active.id, first.id, second.id
    repository = CapabilityDesiredStateRepository(db)
    attempted, acquired = Event(), Event()
    results = []

    def install():
        try:
            repository.install_skill(
                bot_id="bot", owner_id="owner", skill_id=str(second_id)
            )
        except Exception as exc:
            results.append(exc)

    worker = Thread(target=install)

    def before_execute(connection, cursor, statement, parameters, context, many):
        if current_thread() is worker and statement == "BEGIN IMMEDIATE":
            attempted.set()

    def after_execute(connection, cursor, statement, parameters, context, many):
        if current_thread() is worker and statement == "BEGIN IMMEDIATE":
            acquired.set()

    event.listen(db._engine, "before_cursor_execute", before_execute)
    event.listen(db._engine, "after_cursor_execute", after_execute)
    try:
        with repository.skill_batch(bot_id="bot", owner_id="owner"):
            repository.add_skill(
                bot_id="bot", owner_id="owner", set_id=str(set_id), skill_id=str(first_id)
            )
            worker.start()
            # Observe the real competing SQL, not merely Thread.start or a sleep.
            assert attempted.wait(30), "Competing writer never attempted its transaction"
            assert not acquired.is_set(), "Competing writer entered an uncommitted batch"
    finally:
        # Release the batch before joining, including on assertion failure. The
        # watchdog allows CI scheduling/coverage overhead, not a performance SLA.
        if worker.ident is not None:
            worker.join(30)
        event.remove(db._engine, "before_cursor_execute", before_execute)
        event.remove(db._engine, "after_cursor_execute", after_execute)
    assert not worker.is_alive(), "Competing writer did not finish after batch commit"
    assert acquired.is_set()
    assert len(results) == 1 and isinstance(results[0], SkillRuntimeNameConflictError)
    with db.orm_session() as session:
        installed = {row.skill_id for row in session.query(BotSkillInstallation).all()}
        assert installed == {first_id}


def test_inactive_set_add_rejects_conflict_with_an_installed_skill():
    db = _Database()
    with db.transactional_orm_session() as session:
        draft = SkillSet(
            name="draft", user_id="owner", bolt_id="bot", is_active=False, env="dev"
        )
        existing = Skill(name="validator", git_path="git://validator", env="dev")
        candidate = Skill(
            name="validator",
            git_path="local://validator",
            user_id="owner",
            bolt_id="bot",
            env="dev",
        )
        session.add_all([draft, existing, candidate])
        session.flush()
        session.add(
            BotSkillInstallation(
                bot_id="bot", owner_id="owner", skill_id=existing.id, env="dev"
            )
        )
        set_id, skill_id = draft.id, candidate.id
    with pytest.raises(SkillRuntimeNameConflictError):
        CapabilityDesiredStateRepository(db).add_skill(
            bot_id="bot", owner_id="owner", set_id=str(set_id), skill_id=str(skill_id)
        )
    with db.orm_session() as session:
        assert session.query(SkillSetSkill).count() == 0
        assert session.query(BotSkillInstallation).count() == 1


def test_inactive_set_add_checks_active_members_before_lazy_backfill():
    db = _Database()
    with db.transactional_orm_session() as session:
        active = SkillSet(
            name="active", user_id="owner", bolt_id="bot", is_active=True, env="dev"
        )
        draft = SkillSet(
            name="draft", user_id="owner", bolt_id="bot", is_active=False, env="dev"
        )
        existing = Skill(name="validator", git_path="git://validator", env="dev")
        candidate = Skill(
            name="validator",
            git_path="local://validator",
            user_id="owner",
            bolt_id="bot",
            env="dev",
        )
        session.add_all([active, draft, existing, candidate])
        session.flush()
        session.add(
            SkillSetSkill(skill_set_id=active.id, skill_id=existing.id, env="dev")
        )
        set_id, skill_id = draft.id, candidate.id
    with pytest.raises(SkillRuntimeNameConflictError):
        CapabilityDesiredStateRepository(db).add_skill(
            bot_id="bot", owner_id="owner", set_id=str(set_id), skill_id=str(skill_id)
        )
    with db.orm_session() as session:
        assert session.query(SkillSetSkill).count() == 1
        assert session.query(BotSkillInstallation).count() == 0


def test_admission_ignores_unrelated_malformed_inactive_set():
    db = _Database()
    with db.transactional_orm_session() as session:
        old = SkillSet(
            name="old", user_id="owner", bolt_id="bot", is_active=False, env="dev"
        )
        malformed = Skill(name="old-center", git_path="center://old", env="dev")
        candidate = Skill(name="new", git_path="git://new", env="dev")
        session.add_all([old, malformed, candidate])
        session.flush()
        session.add(
            SkillSetSkill(skill_set_id=old.id, skill_id=malformed.id, env="dev")
        )
        candidate_id = candidate.id
    result = CapabilityDesiredStateRepository(db).install_skill(
        bot_id="bot", owner_id="owner", skill_id=str(candidate_id)
    )
    assert result.changed


def test_inactive_set_add_rejects_a_conflicting_draft_member():
    db = _Database()
    with db.transactional_orm_session() as session:
        draft = SkillSet(
            name="draft", user_id="owner", bolt_id="bot", is_active=False, env="dev"
        )
        first = Skill(name="validator", git_path="git://validator", env="dev")
        second = Skill(
            name="validator",
            git_path="local://validator",
            user_id="owner",
            bolt_id="bot",
            env="dev",
        )
        session.add_all([draft, first, second])
        session.flush()
        session.add(SkillSetSkill(skill_set_id=draft.id, skill_id=first.id, env="dev"))
        set_id, skill_id = draft.id, second.id
    with pytest.raises(SkillRuntimeNameConflictError):
        CapabilityDesiredStateRepository(db).add_skill(
            bot_id="bot", owner_id="owner", set_id=str(set_id), skill_id=str(skill_id)
        )
    with db.orm_session() as session:
        assert session.query(SkillSetSkill).count() == 1
        assert session.query(BotSkillInstallation).count() == 0


def test_inactive_set_allows_same_runtime_identity_and_unrelated_historical_conflict():
    db = _Database()
    with db.transactional_orm_session() as session:
        draft = SkillSet(
            name="draft", user_id="owner", bolt_id="bot", is_active=False, env="dev"
        )
        same_a = Skill(name="validator", git_path="git://validator", env="dev")
        same_b = Skill(name="validator", git_path="git://validator", env="dev")
        old_a = Skill(name="old", git_path="git://old-a", env="dev")
        old_b = Skill(name="old", git_path="git://old-b", env="dev")
        session.add_all([draft, same_a, same_b, old_a, old_b])
        session.flush()
        session.add_all(
            [
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=old_a.id, env="dev"
                ),
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=old_b.id, env="dev"
                ),
                SkillSetSkill(skill_set_id=draft.id, skill_id=same_a.id, env="dev"),
            ]
        )
        set_id, skill_id = draft.id, same_b.id
    result = CapabilityDesiredStateRepository(db).add_skill(
        bot_id="bot", owner_id="owner", set_id=str(set_id), skill_id=str(skill_id)
    )
    assert result.changed
    with db.orm_session() as session:
        assert session.query(SkillSetSkill).count() == 2


def test_activation_accepts_two_rows_with_same_runtime_mapping():
    db = _Database()
    with db.transactional_orm_session() as session:
        draft = SkillSet(
            name="draft", user_id="owner", bolt_id="bot", is_active=False, env="dev"
        )
        first = Skill(name="validator", git_path="git://validator", env="dev")
        second = Skill(name="validator", git_path="git://validator", env="dev")
        session.add_all([draft, first, second])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=draft.id, skill_id=first.id, env="dev"),
                SkillSetSkill(skill_set_id=draft.id, skill_id=second.id, env="dev"),
            ]
        )
        set_id = draft.id
    result = CapabilityDesiredStateRepository(db).set_skill_set_active(
        bot_id="bot", owner_id="owner", set_id=str(set_id), active=True
    )
    assert result.changed
    with db.orm_session() as session:
        assert session.query(SkillSet).one().is_active
        assert session.query(BotSkillInstallation).count() == 2


def test_unresolvable_center_candidate_conflict_fails_without_writes():
    db = _Database()
    with db.transactional_orm_session() as session:
        draft = SkillSet(
            name="draft", user_id="owner", bolt_id="bot", is_active=False, env="dev"
        )
        local = Skill(
            name="validator",
            git_path="local://validator",
            user_id="owner",
            bolt_id="bot",
            env="dev",
        )
        center = Skill(
            name="validator",
            git_path="center://validator",
            skill_uuid="center-validator",
            env="dev",
        )
        session.add_all([draft, local, center])
        session.flush()
        session.add(SkillSetSkill(skill_set_id=draft.id, skill_id=local.id, env="dev"))
        set_id, skill_id = draft.id, center.id
    with pytest.raises(SkillRuntimeNameConflictError):
        CapabilityDesiredStateRepository(db).add_skill(
            bot_id="bot", owner_id="owner", set_id=str(set_id), skill_id=str(skill_id)
        )
    with db.orm_session() as session:
        assert session.query(SkillSetSkill).count() == 1
        assert session.query(BotSkillInstallation).count() == 0
