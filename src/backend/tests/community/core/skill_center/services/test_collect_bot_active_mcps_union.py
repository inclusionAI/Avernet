"""collect_bot_active_mcps = default policy ∪ installed, over a real DB."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.mcp import BotMCPInstallation, SkillSetMCPServer
from agentclaw.community.core.models.skill import BotSkillInstallation, Skill, SkillSet
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.skill import (
    SkillRepository,
    SkillSetRepository,
)
from agentclaw.community.core.skill_center.orm import DefaultSkillsetMcpExclusion
from agentclaw.community.core.skill_center.services.bot_capability_state_reader import (
    BotCapabilityStateReader,
)
from agentclaw.community.core.skill_center.services.skill_set_service import (
    SkillSetService,
)
from tests.community.skill_version_fakes import PassthroughSkillVersionResolver

pytestmark = pytest.mark.unit

# ``moltis`` carries no static engine defaults, so the union's halves stay
# visible: what appears is exactly Default-Set rows and Installation rows.
_ENGINE = "moltis"


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


class _Bots:
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict:
        return {
            "bot_id": bot_id,
            "owner_id": owner_id,
            "env": "dev",
            "active_engine": _ENGINE,
        }


def _service(db: _Database, tmp_path, *, ext_info_provider=None) -> SkillSetService:
    skills = SkillRepository(db)
    return SkillSetService(
        skill_repo=skills,
        skill_set_repo=SkillSetRepository(db),
        mcp_center=MagicMock(),
        mcp_config_service=MagicMock(),
        skill_service=MagicMock(),
        bot_repo=MagicMock(),
        skills_dir=tmp_path / "skills",
        repo_dir=tmp_path / "skills-repo",
        local_dir=tmp_path / "skills-local",
        entity_id="owner",
        bot_id="bot",
        engine_type=_ENGINE,
        ext_info_provider=ext_info_provider,
        path_factory=MagicMock(),
        reader=BotCapabilityStateReader(
            repository=CapabilityDesiredStateRepository(db),
            bot_repo=_Bots(),
            pool_skills=skills,
            version_resolver=PassthroughSkillVersionResolver(),
        ),
    )


def test_an_ordinary_sets_mcp_arrives_through_installation(tmp_path):
    """An active ordinary Set's MCP is not walked — the flush installs it, the
    reader answers it, and its metadata comes from the membership row."""
    db = _Database()
    with db.transactional_orm_session() as session:
        ordinary = SkillSet(
            name="tools",
            user_id="owner",
            bolt_id="bot",
            engine_type=_ENGINE,
            is_active=True,
            env="dev",
        )
        session.add(ordinary)
        session.flush()
        session.add(
            SkillSetMCPServer(
                skill_set_id=ordinary.id,
                server_code="mcp.weather",
                name="Weather",
                env="dev",
            )
        )

    result = _service(db, tmp_path).collect_bot_active_mcps(
        entity_id="owner", bot_id="bot", user_id="owner"
    )

    assert [(m["server_code"], m["name"]) for m in result] == [
        ("mcp.weather", "Weather")
    ]
    with db.orm_session() as session:
        assert [
            row.server_code for row in session.query(BotMCPInstallation).all()
        ] == ["mcp.weather"]


def test_an_excluded_default_mcp_is_absent_from_the_union(tmp_path):
    """Exclusion silences both halves: the Default projection filters it and
    the flush never installs it."""
    db = _Database()
    with db.transactional_orm_session() as session:
        default_set = SkillSet(
            name="defaults",
            user_id="owner",
            bolt_id="bot",
            engine_type=_ENGINE,
            is_default=True,
            is_active=True,
            env="dev",
        )
        session.add(default_set)
        session.flush()
        session.add_all(
            [
                SkillSetMCPServer(
                    skill_set_id=default_set.id,
                    server_code="mcp.included",
                    name="Included",
                    env="dev",
                ),
                SkillSetMCPServer(
                    skill_set_id=default_set.id,
                    server_code="mcp.excluded",
                    name="Excluded",
                    env="dev",
                ),
                DefaultSkillsetMcpExclusion(
                    user_id="owner",
                    bot_id="bot",
                    skill_set_id=int(default_set.id),
                    server_code="mcp.excluded",
                ),
            ]
        )

    result = _service(db, tmp_path).collect_bot_active_mcps(
        entity_id="owner", bot_id="bot", user_id="owner"
    )

    assert [m["server_code"] for m in result] == ["mcp.included"]
    with db.orm_session() as session:
        assert [
            row.server_code for row in session.query(BotMCPInstallation).all()
        ] == ["mcp.included"]


def test_a_direct_installation_appears_with_minimal_metadata(tmp_path):
    """A directly installed MCP has no membership row anywhere; the union
    still answers it, minimally shaped."""
    db = _Database()
    with db.transactional_orm_session() as session:
        session.add(
            BotMCPInstallation(
                bot_id="bot",
                owner_id="owner",
                server_code="mcp.direct",
                env="dev",
            )
        )

    result = _service(db, tmp_path).collect_bot_active_mcps(
        entity_id="owner", bot_id="bot", user_id="owner"
    )

    assert result == [
        {
            "id": None,
            "server_code": "mcp.direct",
            "name": "mcp.direct",
            "description": "",
            "icon": None,
            "status": "ONLINE",
            "is_default": False,
        }
    ]


def test_an_installed_skills_mcp_dependency_is_effective_without_mcp_installation(
    tmp_path,
):
    """Skill Installation supplies its declared MCP dependency without turning
    that derived supply into an explicit MCP Installation fact."""
    db = _Database()
    with db.transactional_orm_session() as session:
        skill = Skill(
            name="weather-skill",
            git_path="git://team/weather-skill",
            mcp_dependencies='["mcp.weather"]',
            env="dev",
        )
        session.add(skill)
        session.flush()
        session.add(
            BotSkillInstallation(
                bot_id="bot",
                owner_id="owner",
                skill_id=skill.id,
                env="dev",
            )
        )

    result = _service(db, tmp_path).collect_bot_active_mcps(
        entity_id="owner", bot_id="bot", user_id="owner"
    )

    assert [entry["server_code"] for entry in result] == ["mcp.weather"]
    with db.orm_session() as session:
        assert session.query(BotMCPInstallation).all() == []


def test_strict_runtime_policy_context_propagates_provider_failure(tmp_path):
    db = _Database()

    def broken_provider(_bot_id: str):
        raise RuntimeError("template policy unavailable")

    service = _service(db, tmp_path, ext_info_provider=broken_provider)

    with pytest.raises(RuntimeError, match="template policy unavailable"):
        service.collect_bot_active_mcps(
            entity_id="owner",
            bot_id="bot",
            user_id="owner",
            strict_policy_context=True,
        )


def test_strict_runtime_policy_context_propagates_template_lookup_failure(tmp_path):
    db = _Database()
    service = _service(db, tmp_path, ext_info_provider=lambda _bot_id: None)
    service._bot_repo.get_by_id_and_owner.side_effect = RuntimeError(
        "bot repository unavailable"
    )

    with pytest.raises(RuntimeError, match="bot repository unavailable"):
        service.collect_bot_active_mcps(
            entity_id="owner",
            bot_id="bot",
            user_id="owner",
            strict_policy_context=True,
        )


def test_display_read_still_degrades_when_template_lookup_fails(tmp_path):
    db = _Database()
    service = _service(db, tmp_path, ext_info_provider=lambda _bot_id: None)
    service._bot_repo.get_by_id_and_owner.side_effect = RuntimeError(
        "bot repository unavailable"
    )

    assert service.collect_bot_active_mcps(
        entity_id="owner",
        bot_id="bot",
        user_id="owner",
    ) == []
