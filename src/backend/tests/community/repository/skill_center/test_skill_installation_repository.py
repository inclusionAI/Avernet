"""Desired-state persistence for Bot Skill Installations."""

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.repository.implementations.skill_center.installation import (
    SkillInstallationRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.skill import (
    SkillRepository,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


class _Database:
    def __init__(self, engine) -> None:
        self._factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

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


def test_skill_installation_is_active_only_idempotent_and_tenant_scoped(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'installation.db'}")
    BotSkillInstallation.__table__.create(engine)
    installations = SkillInstallationRepository(_Database(engine))

    with avernet_tenant_scope("tenant-a"):
        assert installations.install(env="pre", bot_id="bot-1", skill_id="42") is True
        assert installations.install(env="pre", bot_id="bot-1", skill_id="42") is False
        assert installations.list_installed_skill_ids(env="pre", bot_id="bot-1") == {42}

    with avernet_tenant_scope("tenant-b"):
        assert installations.list_installed_skill_ids(env="pre", bot_id="bot-1") == set()
        assert installations.install(env="pre", bot_id="bot-1", skill_id="42") is True

    with avernet_tenant_scope("tenant-a"):
        assert installations.uninstall(env="pre", bot_id="bot-1", skill_id="42") is True
        assert installations.uninstall(env="pre", bot_id="bot-1", skill_id="42") is False
        assert installations.list_installed_skill_ids(env="pre", bot_id="bot-1") == set()


def test_direct_installation_is_included_in_the_existing_runtime_mapping_query(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime-mapping.db'}")
    for model in (Skill, SkillSet, SkillSetSkill, BotSkillInstallation):
        model.__table__.create(engine)
    db = _Database(engine)
    installations = SkillInstallationRepository(db)
    skills = SkillRepository(db)

    with avernet_tenant_scope("tenant-a"):
        skill = skills.create(
            {
                "name": "local-one",
                "git_path": "local://local-one",
                "user_id": "owner",
                "bolt_id": "bot-1",
            }
        )
        assert installations.install(env="dev", bot_id="bot-1", skill_id=skill["id"])

        projected = skills.list_bot_active_assets(
            env="dev", bot_id="bot-1", user_id="owner", engine="openclaw"
        )

    assert [(asset.skill_id, asset.git_path) for asset in projected] == [
        (int(skill["id"]), "local://local-one")
    ]


def test_skill_installation_fk_matches_production_unsigned_skill_identity() -> None:
    """OceanBase production reports ``ac_skill.id bigint unsigned``."""
    skill_ddl = str(CreateTable(Skill.__table__).compile(dialect=mysql.dialect()))
    installation_ddl = str(
        CreateTable(BotSkillInstallation.__table__).compile(dialect=mysql.dialect())
    )

    assert "id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT" in skill_ddl
    assert "skill_id BIGINT UNSIGNED NOT NULL" in installation_ddl
    assert "FOREIGN KEY(skill_id) REFERENCES ac_skill (id)" in installation_ddl
