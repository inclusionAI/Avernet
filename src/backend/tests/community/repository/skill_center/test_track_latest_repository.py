"""Persistence rules for Track Latest candidate discovery and MCP delta."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.models.space_skill import SkillVersion
from agentclaw.community.core.repository.implementations.skill_center.track_latest import (
    TrackLatestRepository,
)
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


class _Database:
    def __init__(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self._factory = sessionmaker(bind=self.engine)

    @contextmanager
    def orm_session(self):
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _bot(
    bot_id: str,
    owner_id: str,
    *,
    engine: str = "openclaw",
    bot_type: str = "personal",
) -> BotModel:
    return BotModel(
        bot_id=bot_id,
        bot_name=bot_id,
        entity_id=owner_id,
        entity_type="staff",
        creator_id=owner_id,
        owner_id=owner_id,
        bot_type=bot_type,
        active_engine=engine,
        engine_types=f'["{engine}"]',
        status="ACTIVE",
        is_delete=0,
        env="pre",
        avernet_tenant="teamclaw",
    )


def test_candidates_include_direct_and_active_ordinary_but_skip_default_only() -> None:
    db = _Database()
    with avernet_tenant_scope("teamclaw"), db.orm_session() as session:
        session.add(
            Skill(
                id=10,
                name="weather",
                git_path="center://public-weather",
                is_public=True,
                env="pre",
                avernet_tenant="teamclaw",
            )
        )
        session.add_all(
            [
                _bot("bot-direct", "owner-direct", engine="hermes"),
                _bot("bot-ordinary", "owner-ordinary"),
                _bot("bot-default", "owner-default"),
                _bot("service-draft", "owner-service", bot_type="service"),
            ]
        )
        session.flush()
        session.add_all(
            [
                BotSkillInstallation(
                    bot_id="bot-direct",
                    owner_id="owner-direct",
                    skill_id=10,
                    env="pre",
                    avernet_tenant="teamclaw",
                ),
                BotSkillInstallation(
                    bot_id="bot-ordinary",
                    owner_id="owner-ordinary",
                    skill_id=10,
                    env="pre",
                    avernet_tenant="teamclaw",
                ),
                BotSkillInstallation(
                    bot_id="bot-default",
                    owner_id="owner-default",
                    skill_id=10,
                    env="pre",
                    avernet_tenant="teamclaw",
                ),
                BotSkillInstallation(
                    bot_id="service-draft",
                    owner_id="owner-service",
                    skill_id=10,
                    env="pre",
                    avernet_tenant="teamclaw",
                ),
            ]
        )
        ordinary = SkillSet(
            name="ordinary",
            is_default=False,
            is_active=True,
            user_id="owner-ordinary",
            bolt_id="bot-ordinary",
            engine_type="openclaw",
            env="pre",
            avernet_tenant="teamclaw",
        )
        service_draft = SkillSet(
            name="service-draft",
            is_default=False,
            is_active=True,
            user_id="owner-service",
            bolt_id="service-draft",
            engine_type="openclaw",
            env="pre",
            avernet_tenant="teamclaw",
        )
        default = SkillSet(
            name="default",
            is_default=True,
            is_active=True,
            user_id=None,
            bolt_id="default",
            engine_type="openclaw",
            env="pre",
            avernet_tenant="teamclaw",
        )
        session.add_all([ordinary, service_draft, default])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=ordinary.id,
                    skill_id=10,
                    user_id="owner-ordinary",
                    env="pre",
                    avernet_tenant="teamclaw",
                ),
                SkillSetSkill(
                    skill_set_id=default.id,
                    skill_id=10,
                    user_id=None,
                    env="pre",
                    avernet_tenant="teamclaw",
                ),
                SkillSetSkill(
                    skill_set_id=service_draft.id,
                    skill_id=10,
                    user_id="owner-service",
                    env="pre",
                    avernet_tenant="teamclaw",
                ),
            ]
        )

    with avernet_tenant_scope("teamclaw"):
        candidates = TrackLatestRepository(db).list_candidates(env="pre", skill_id=10)

    assert {(item.owner_id, item.bot_id) for item in candidates} == {
        ("owner-direct", "bot-direct"),
        ("owner-ordinary", "bot-ordinary"),
        ("owner-service", "service-draft"),
    }


def test_dependency_delta_uses_execution_time_latest_published_version() -> None:
    db = _Database()
    with avernet_tenant_scope("teamclaw"), db.orm_session() as session:
        for version_id, ordinal, status, dependencies in (
            (101, 1, "PUBLISHED", [{"code": "mcp.old"}, {"code": "mcp.keep"}]),
            (102, 2, "PUBLISHED", [{"code": "mcp.new"}, {"code": "mcp.keep"}]),
            (103, 3, "MATERIALIZING", [{"code": "mcp.future"}]),
        ):
            session.add(
                SkillVersion(
                    id=version_id,
                    skill_id=10,
                    publication_attempt_id=None,
                    version_ordinal=ordinal,
                    status=status,
                    sc_version_number=f"{ordinal}.0.0",
                    sc_skill_id=9001,
                    sc_version_id=10000 + version_id,
                    name="weather",
                    description=None,
                    metadata_json=(
                        '{"mcp_dependencies":'
                        + __import__("json").dumps(dependencies)
                        + "}"
                    ),
                    published_at=(
                        datetime(2026, 8, 30, tzinfo=UTC)
                        if status == "PUBLISHED"
                        else None
                    ),
                    created_by="owner",
                    env="pre",
                    avernet_tenant="teamclaw",
                )
            )

    with avernet_tenant_scope("teamclaw"):
        delta = TrackLatestRepository(db).latest_dependency_delta(
            env="pre", skill_id=10
        )

    assert delta.skill_version_id == 102
    assert delta.claimed_mcp == frozenset({"mcp.new"})
    assert delta.released_mcp == frozenset({"mcp.old"})
