"""Atomicity tests for the canonical SkillSet desired-state UoW."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.skill import (
    BotSkillInstallation, Skill, SkillSet, SkillSetSkill,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_set_control_plane import (
    SkillSetControlPlaneRepository,
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


def test_activation_rolls_back_all_membership_installations_when_nth_insert_fails():
    """No half-selected set can survive a storage failure at member N."""
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(name="set", bolt_id="bot", is_active=False, env="dev")
        first = Skill(name="one", git_path="git://one", env="dev")
        second = Skill(name="two", git_path="git://two", env="dev")
        session.add_all([skill_set, first, second])
        session.flush()
        session.add_all([
            SkillSetSkill(skill_set_id=skill_set.id, skill_id=first.id, env="dev"),
            SkillSetSkill(skill_set_id=skill_set.id, skill_id=second.id, env="dev"),
        ])

    inserts = 0

    def fail_second_install(_mapper, _connection, _target):
        nonlocal inserts
        inserts += 1
        if inserts == 2:
            raise RuntimeError("second installation write failed")

    event.listen(BotSkillInstallation, "before_insert", fail_second_install)
    try:
        with pytest.raises(RuntimeError, match="second installation"):
            SkillSetControlPlaneRepository(db).set_active(bot_id="bot", set_id="1", active=True)
    finally:
        event.remove(BotSkillInstallation, "before_insert", fail_second_install)

    with db.orm_session() as session:
        assert session.query(SkillSet).one().is_active is False
        assert session.query(SkillSetSkill).count() == 2
        assert session.query(BotSkillInstallation).count() == 0


def test_create_idempotency_replays_the_original_set_without_a_second_row():
    db = _Database()
    repository = SkillSetControlPlaneRepository(db)

    first = repository.create_set(
        bot_id="bot", owner_id="owner", name="set", description="description",
        idempotency_key="request-1",
    )
    replay = repository.create_set(
        bot_id="bot", owner_id="owner", name="set", description="description",
        idempotency_key="request-1",
    )

    assert replay == first
    with db.orm_session() as session:
        assert session.query(SkillSet).count() == 1
