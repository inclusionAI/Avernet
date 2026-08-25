"""The per-table Installation command modules — the tables' only writers."""

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.models.mcp import BotMCPInstallation
from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.repository.implementations.skill_center.skill import (
    SkillRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.tables import (
    mcp_installations,
    skill_installations,
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


def test_skill_installation_is_idempotent_and_tenant_scoped(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'installation.db'}")
    BotSkillInstallation.__table__.create(engine)
    db = _Database(engine)
    scope = {"env": "pre", "owner_id": "owner-a", "bot_id": "bot-1"}

    with avernet_tenant_scope("tenant-a"), db.orm_session() as session:
        assert skill_installations.install(session, **scope, skill_id=42) is True
        assert skill_installations.install(session, **scope, skill_id=42) is False
        assert skill_installations.installed_ids(session, **scope) == {42}
        assert skill_installations.installed_ids(
            session, env="pre", owner_id="owner-b", bot_id="bot-1"
        ) == set()

    with avernet_tenant_scope("tenant-b"), db.orm_session() as session:
        assert skill_installations.installed_ids(session, **scope) == set()
        assert skill_installations.install(session, **scope, skill_id=42) is True

    with avernet_tenant_scope("tenant-a"), db.orm_session() as session:
        assert skill_installations.uninstall(session, **scope, skill_ids={42}) == 1
        assert skill_installations.uninstall(session, **scope, skill_ids={42}) == 0
        assert skill_installations.installed_ids(session, **scope) == set()


def test_mcp_installation_mirrors_the_skill_table_contract(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'mcp-installation.db'}")
    BotMCPInstallation.__table__.create(engine)
    db = _Database(engine)
    scope = {"env": "pre", "owner_id": "owner-a", "bot_id": "bot-1"}

    with avernet_tenant_scope("tenant-a"), db.orm_session() as session:
        assert mcp_installations.install(session, **scope, server_code="mcp.a") is True
        assert mcp_installations.install(session, **scope, server_code="mcp.a") is False
        assert mcp_installations.installed_codes(session, **scope) == {"mcp.a"}
        assert (
            mcp_installations.uninstall(session, **scope, server_codes={"mcp.a"}) == 1
        )
        assert mcp_installations.installed_codes(session, **scope) == set()


def test_direct_installation_is_included_in_the_existing_runtime_mapping_query(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime-mapping.db'}")
    for model in (Skill, SkillSet, SkillSetSkill, BotSkillInstallation):
        model.__table__.create(engine)
    db = _Database(engine)
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
        with db.orm_session() as session:
            assert skill_installations.install(
                session, env="dev", owner_id="owner", bot_id="bot-1",
                skill_id=int(skill["id"]),
            )

        projected = skills.list_bot_installed_assets(
            env="dev", bot_id="bot-1", owner_id="owner"
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
    assert "owner_id VARCHAR(128) NOT NULL" in installation_ddl
    assert "FOREIGN KEY(skill_id) REFERENCES ac_skill (id)" in installation_ddl
