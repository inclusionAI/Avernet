"""Shared database and seed fixtures for desired-state repository tests."""

from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime
from sqlalchemy import create_engine
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
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
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
        session.add_all([
            SkillSetSkill(
                skill_set_id=1, skill_id=999, skill_uuid="uuid-a", env="dev"
            ),
            SkillVersion(
                skill_id=1, publication_attempt_id=None, version_ordinal=2,
                status="PUBLISHED", sc_version_number="v2.0",
                sc_skill_id=1001, sc_version_id=2002,
                name="center-current", description="Current Center version",
                metadata_json='{"mcp_dependencies":[]}',
                published_at=datetime(2026, 9, 1), created_by="owner", env="dev",
            ),
        ])


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


_APPLY_SCOPE = dict(bot_id="bot", owner_id="owner", engine_type="openclaw", default_engine_types=("openclaw",))


def _ordinary_set(db, *, active: bool = True, skills=(), mcp_codes=()):
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="ordinary", user_id="owner", bolt_id="bot",
            engine_type="openclaw", is_active=active, env="dev",
        )
        session.add(skill_set)
        session.flush()
        session.add_all([SkillSetSkill(skill_set_id=skill_set.id, skill_id=skill.id, env="dev") for skill in skills])
        session.add_all([
            SkillSetMCPServer(skill_set_id=skill_set.id, server_code=code, name=code, env="dev")
            for code in mcp_codes
        ])
    return skill_set
