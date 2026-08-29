"""Verify get_symlink_mappings emits skill_uuid + version for center:// skills."""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.skill import (
    SkillRepository,
)
from agentclaw.community.core.skill_center.orm import DefaultSkillsetSkillExclusion
from agentclaw.community.core.skill_center.services.bot_capability_state_reader import (
    BotCapabilityStateReader,
)
from agentclaw.community.core.skill_center.services.skill_set_service import (
    SkillSetService,
    SynlinkMappingInfo,
)
from tests.community.skill_version_fakes import PassthroughSkillVersionResolver

pytestmark = pytest.mark.unit


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
            "active_engine": "openclaw",
        }


def test_an_excluded_default_member_is_no_longer_symlinked(tmp_path):
    """Exclusion is the Default Set's per-Bot deactivation, all the way to the
    engine: the flush inside the reader never installs the excluded member, so
    the symlink projection stops carrying it."""
    db = _Database()
    with db.transactional_orm_session() as session:
        default_set = SkillSet(
            name="OpenClaw defaults",
            user_id="",
            bolt_id="",
            engine_type="openclaw",
            is_default=True,
            is_active=True,
            env="dev",
        )
        enabled = Skill(name="enabled", git_path="git://defaults/enabled", env="dev")
        excluded = Skill(
            name="excluded", git_path="git://defaults/excluded", env="dev"
        )
        session.add_all([default_set, enabled, excluded])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=default_set.id, skill_id=enabled.id, env="dev"
                ),
                SkillSetSkill(
                    skill_set_id=default_set.id, skill_id=excluded.id, env="dev"
                ),
                DefaultSkillsetSkillExclusion(
                    user_id="owner",
                    bot_id="bot",
                    skill_set_id=int(default_set.id),
                    skill_id=int(excluded.id),
                ),
            ]
        )

    skills = SkillRepository(db)
    reader = BotCapabilityStateReader(
        repository=CapabilityDesiredStateRepository(db),
        bot_repo=_Bots(),
        pool_skills=skills,
        version_resolver=PassthroughSkillVersionResolver(),
    )
    service = SkillSetService(
        skill_repo=skills,
        skill_set_repo=MagicMock(),
        mcp_center=MagicMock(),
        mcp_config_service=MagicMock(),
        skill_service=MagicMock(),
        bot_repo=MagicMock(),
        skills_dir=tmp_path / "skills",
        repo_dir=tmp_path / "skills-repo",
        local_dir=tmp_path / "skills-local",
        engine_type="openclaw",
        runtime_engine_type="openclaw",
        path_factory=MagicMock(),
        reader=reader,
    )

    mappings = service.get_symlink_mappings(user_id="owner", bolt_id="bot")

    assert [mapping.target.rsplit("/", 1)[-1] for mapping in mappings] == [
        "enabled"
    ]


def test_synlink_mapping_info_has_uuid_and_version_fields():
    m = SynlinkMappingInfo(
        source="/a", target="/b", skill_uuid="u1", version=2,
    )
    assert m.skill_uuid == "u1"
    assert m.version == 2


def test_synlink_mapping_info_to_dict_includes_uuid_and_version():
    m = SynlinkMappingInfo(
        source="/a", target="/b", skill_uuid="u1", version=2,
    )
    d = m.to_dict()
    assert d["skill_uuid"] == "u1"
    assert d["version"] == 2


def test_synlink_mapping_info_optional_fields_default_none():
    m = SynlinkMappingInfo(source="/a", target="/b")
    assert m.skill_uuid is None
    assert m.version is None
    d = m.to_dict()
    # to_dict should still emit keys (so engine schema is stable)
    assert "skill_uuid" in d
    assert "version" in d
