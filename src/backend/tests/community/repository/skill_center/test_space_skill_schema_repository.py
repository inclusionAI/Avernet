"""F01 additive Space Skill persistence contract tests."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.repository.implementations.skill_center.space_skill import (
    SpaceSkillRepository,
)
from agentclaw.community.core.models.space_skill import (
    SkillGrant,
    SkillPublicationAttempt,
    SkillSpaceBinding,
    SkillVersion,
    Space,
    SpaceMember,
)
from agentclaw.community.core.models.skill import Skill, SkillSetSkill
from agentclaw.community.core.models.skill_center_sync_log import SkillCenterSyncLog
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


class _Database:
    def __init__(self):
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


@pytest.fixture
def db() -> _Database:
    return _Database()


def test_additive_schema_registers_space_and_skill_fact_scope(db):
    tables = set(inspect(db.engine).get_table_names())

    assert {
        "ac_space",
        "ac_space_member",
        "ac_skill_space_binding",
        "ac_skill_grant",
        "ac_skill_draft_edit_lease",
        "ac_skill_version",
        "ac_skill_publication_attempt",
    } <= tables
    assert Space is SpaceModel
    assert SpaceMember is SpaceMemberModel

    for model in (Space, SpaceMember):
        columns = {column.name: column for column in model.__table__.columns}
        assert "avernet_tenant" not in columns
        assert columns["env"].nullable is False

    for model in (
        SkillSpaceBinding,
        SkillGrant,
        SkillVersion,
        SkillPublicationAttempt,
    ):
        columns = {column.name: column for column in model.__table__.columns}
        assert columns["avernet_tenant"].nullable is False
        assert columns["env"].nullable is False

    assert {
        "description",
        "sc_mapping_status",
        "updated_by",
        "deleted_at",
        "deleted_by",
    } <= {column.name for column in SpaceModel.__table__.columns}
    assert {"status", "removed_at", "removed_by"} <= {
        column.name for column in SpaceMemberModel.__table__.columns
    }


def test_additive_orm_contract_extends_only_the_documented_legacy_tables(db):
    assert {
        "draft_target_version",
        "draft_status",
        "retired_at",
        "retired_by",
        "source_repo_url",
        "source_branch",
        "source_subdir",
        "source_commit_sha",
    } <= {column.name for column in Skill.__table__.columns}
    assert {"avernet_tenant", "skill_version_id"} <= {
        column.name for column in SkillCenterSyncLog.__table__.columns
    }

    unique_names = {
        constraint.name
        for constraint in SkillSetSkill.__table__.constraints
        if getattr(constraint, "unique", False)
        or constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert "uk_skill_set_skill" in unique_names


def test_space_repository_is_tenant_env_scoped_and_unique(db):
    repo = SpaceSkillRepository(db)
    created = repo.create_space(
        {
            "space_code": "team-a",
            "space_type": "TEAM",
            "name": "Team A",
            "created_by": "u-1",
            "env": "dev",
        }
    )

    assert created["space_code"] == "team-a"
    assert repo.get_space(created["id"], env="dev")["name"] == "Team A"
    assert repo.get_space(created["id"], env="prod") is None

    with pytest.raises(IntegrityError):
        repo.create_space(
            {
                "space_code": "team-a",
                "space_type": "TEAM",
                "name": "A duplicate",
                "created_by": "u-1",
                "env": "dev",
            }
        )


def test_space_repository_scope_is_env_only(db):
    repo = SpaceSkillRepository(db)
    with avernet_tenant_scope("tenant-a"):
        created = repo.create_space(
            {
                "space_code": "isolated",
                "space_type": "TEAM",
                "name": "Tenant A",
                "created_by": "u-1",
                "env": "dev",
            }
        )

    with avernet_tenant_scope("tenant-b"):
        assert repo.get_space(created["id"], env="dev")["space_code"] == "isolated"


def test_schema_rejects_empty_env_and_duplicate_active_owner(db):
    with db.orm_session() as session:
        session.add(
            Space(
                space_code="invalid",
                space_type="TEAM",
                name="Invalid",
                created_by="u-1",
                updated_by="u-1",
                env="",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    with db.orm_session() as session:
        session.add_all(
            [
                SkillGrant(
                    skill_id=7,
                    user_id="owner-1",
                    role="OWNER",
                    owner_slot=1,
                    granted_by="owner-1",
                    env="dev",
                ),
                SkillGrant(
                    skill_id=7,
                    user_id="owner-2",
                    role="OWNER",
                    owner_slot=1,
                    granted_by="owner-1",
                    env="dev",
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_repository_creates_stable_identity_ownership_and_owner_grant_atomically(db):
    repo = SpaceSkillRepository(db)
    space = repo.create_space(
        {
            "space_code": "team-b",
            "space_type": "TEAM",
            "name": "Team B",
            "created_by": "owner-1",
            "env": "dev",
        }
    )
    with db.orm_session() as session:
        session.add(
            SpaceMember(
                space_id=space["id"],
                user_id="owner-1",
                role="ADMINISTRATOR",
                created_by="owner-1",
                env="dev",
            )
        )

    created = repo.create_space_skill(
        skill_data={
            "name": "risk-review",
            "env": "dev",
        },
        ownership_data={"space_id": space["id"], "created_by": "owner-1", "env": "dev"},
        owner_grant_data={
            "user_id": "owner-1",
            "role": "OWNER",
            "status": "ACTIVE",
            "owner_slot": 1,
            "granted_by": "owner-1",
            "env": "dev",
        },
    )

    assert created["skill"]["id"] is not None
    assert UUID(created["skill"]["skill_uuid"]).version == 4
    assert created["ownership"]["skill_id"] == created["skill"]["id"]
    assert created["owner_grant"]["owner_slot"] == 1


def test_repository_rejects_space_skill_without_an_active_owner_membership(db):
    repo = SpaceSkillRepository(db)
    space = repo.create_space(
        {
            "space_code": "team-c",
            "space_type": "TEAM",
            "name": "Team C",
            "created_by": "creator",
            "env": "dev",
        }
    )

    with pytest.raises(ValueError, match="active Space Member"):
        repo.create_space_skill(
            skill_data={"name": "unowned", "env": "dev"},
            ownership_data={
                "space_id": space["id"],
                "created_by": "creator",
                "env": "dev",
            },
            owner_grant_data={
                "user_id": "missing-member",
                "granted_by": "creator",
                "env": "dev",
            },
        )


def test_additive_migration_is_repeat_safe_and_requires_reviewed_duplicate_cleanup():
    sql_dir = (
        Path(__file__).parents[4]
        / "src"
        / "agentclaw"
        / "community"
        / "core"
        / "skill_center"
        / "sql"
    )
    ddl = (sql_dir / "2026_08_19_additive_space_skill_schema.sql").read_text()
    verify = (sql_dir / "2026_08_19_additive_space_skill_schema_verify.sql").read_text()
    spaces_sql = (
        Path(__file__).parents[4]
        / "src"
        / "agentclaw"
        / "community"
        / "core"
        / "spaces"
        / "sql"
        / "2026_08_17_spaces.sql"
    ).read_text()

    assert ddl.count("CREATE TABLE IF NOT EXISTS") == 5
    assert "CREATE TABLE IF NOT EXISTS ac_space" not in ddl
    assert "ALTER TABLE ac_space" in spaces_sql
    assert "ALTER TABLE ac_space_member" in spaces_sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uk_skill_set_skill" in ddl
    assert "DELETE FROM" not in ddl
    assert "ac_skill_set_skill duplicate" in verify
    assert "ac_skill_set_skill orphan skill" in verify


def _add_bound_skill(
    db,
    *,
    space_id,
    name,
    description=None,
    env="dev",
    modified_at=None,
    retired=False,
    grant_user_id=None,
    grant_role="MANAGER",
    grant_status="ACTIVE",
):
    from datetime import datetime

    timestamp = modified_at or datetime(2026, 8, 20, 3, 40)
    with db.orm_session() as session:
        skill = Skill(
            name=name,
            description=description,
            env=env,
            skill_uuid=f"uuid-{space_id}-{name}-{env}",
            status="DEVELOPING",
            draft_status="EDITING",
            retired_at=timestamp if retired else None,
            gmt_created=timestamp,
            gmt_modified=timestamp,
        )
        session.add(skill)
        session.flush()
        session.add(
            SkillSpaceBinding(
                skill_id=skill.id,
                space_id=space_id,
                created_by="owner-1",
                env=env,
            )
        )
        if grant_user_id is not None:
            session.add(
                SkillGrant(
                    skill_id=skill.id,
                    user_id=grant_user_id,
                    role=grant_role,
                    status=grant_status,
                    owner_slot=(
                        1
                        if grant_role == "OWNER" and grant_status == "ACTIVE"
                        else None
                    ),
                    granted_by="owner-1",
                    env=env,
                )
            )
        return skill.id


def test_list_space_skills_filters_scope_env_and_retired_rows(db):
    from datetime import datetime, timedelta

    repo = SpaceSkillRepository(db)
    space = repo.create_space(
        {
            "space_code": "list-a",
            "space_type": "TEAM",
            "name": "List A",
            "created_by": "owner-1",
            "env": "dev",
        }
    )
    other = repo.create_space(
        {
            "space_code": "list-b",
            "space_type": "TEAM",
            "name": "List B",
            "created_by": "owner-1",
            "env": "dev",
        }
    )
    base = datetime(2026, 8, 20, 3, 40)
    older_id = _add_bound_skill(
        db,
        space_id=space["id"],
        name="Older",
        description="first",
        modified_at=base,
    )
    newer_id = _add_bound_skill(
        db,
        space_id=space["id"],
        name="Newer",
        description="second",
        modified_at=base + timedelta(minutes=1),
    )
    _add_bound_skill(db, space_id=other["id"], name="Other Space")
    _add_bound_skill(db, space_id=space["id"], name="Other Env", env="prod")
    _add_bound_skill(db, space_id=space["id"], name="Retired", retired=True)

    total, records = repo.list_space_skills(
        space_id=space["id"],
        actor_id="viewer",
        env="dev",
        keyword=None,
        offset=0,
        limit=20,
    )

    assert total == 2
    assert [record["id"] for record in records] == [newer_id, older_id]


def test_list_space_skills_searches_name_and_description_case_insensitively(db):
    repo = SpaceSkillRepository(db)
    space = repo.create_space(
        {
            "space_code": "search-a",
            "space_type": "TEAM",
            "name": "Search A",
            "created_by": "owner-1",
            "env": "dev",
        }
    )
    _add_bound_skill(
        db,
        space_id=space["id"],
        name="Smart Form Parser",
        description="structured input",
    )
    _add_bound_skill(
        db,
        space_id=space["id"],
        name="Extractor",
        description="Parse Complex Forms",
    )
    _add_bound_skill(db, space_id=space["id"], name="Unrelated")

    name_total, name_records = repo.list_space_skills(
        space_id=space["id"],
        actor_id="viewer",
        env="dev",
        keyword="FORM",
        offset=0,
        limit=20,
    )
    description_total, description_records = repo.list_space_skills(
        space_id=space["id"],
        actor_id="viewer",
        env="dev",
        keyword="complex",
        offset=0,
        limit=20,
    )
    empty_total, empty_records = repo.list_space_skills(
        space_id=space["id"],
        actor_id="viewer",
        env="dev",
        keyword="missing",
        offset=0,
        limit=20,
    )

    assert name_total == 2
    assert {record["name"] for record in name_records} == {
        "Smart Form Parser",
        "Extractor",
    }
    assert description_total == 1
    assert description_records[0]["name"] == "Extractor"
    assert empty_total == 0
    assert empty_records == []


def test_list_space_skills_uses_stable_database_pagination(db):
    from datetime import datetime

    repo = SpaceSkillRepository(db)
    space = repo.create_space(
        {
            "space_code": "page-a",
            "space_type": "TEAM",
            "name": "Page A",
            "created_by": "owner-1",
            "env": "dev",
        }
    )
    timestamp = datetime(2026, 8, 20, 3, 40)
    ids = [
        _add_bound_skill(
            db,
            space_id=space["id"],
            name=f"Skill {index}",
            modified_at=timestamp,
        )
        for index in range(3)
    ]

    total, records = repo.list_space_skills(
        space_id=space["id"],
        actor_id="viewer",
        env="dev",
        keyword=None,
        offset=1,
        limit=1,
    )

    assert total == 3
    assert [record["id"] for record in records] == [sorted(ids, reverse=True)[1]]


def test_list_space_skills_projects_space_type_and_only_the_actor_active_grant(db):
    repo = SpaceSkillRepository(db)
    personal = repo.create_space(
        {
            "space_code": "personal-list",
            "space_type": "PERSONAL",
            "name": "Personal",
            "personal_owner_id": "owner-1",
            "created_by": "owner-1",
            "env": "dev",
        }
    )
    team = repo.create_space(
        {
            "space_code": "team-list",
            "space_type": "TEAM",
            "name": "Team",
            "created_by": "owner-1",
            "env": "dev",
        }
    )
    _add_bound_skill(
        db,
        space_id=personal["id"],
        name="Personal Skill",
        grant_user_id="owner-1",
        grant_role="OWNER",
    )
    _add_bound_skill(
        db,
        space_id=team["id"],
        name="Managed Skill",
        grant_user_id="member-1",
        grant_role="MANAGER",
    )
    _add_bound_skill(
        db,
        space_id=team["id"],
        name="Revoked Skill",
        grant_user_id="member-1",
        grant_role="MANAGER",
        grant_status="REVOKED",
    )
    _add_bound_skill(
        db,
        space_id=team["id"],
        name="Other User Skill",
        grant_user_id="other-member",
        grant_role="MANAGER",
    )

    _, personal_records = repo.list_space_skills(
        space_id=personal["id"],
        actor_id="owner-1",
        env="dev",
        keyword=None,
        offset=0,
        limit=20,
    )
    _, team_records = repo.list_space_skills(
        space_id=team["id"],
        actor_id="member-1",
        env="dev",
        keyword=None,
        offset=0,
        limit=20,
    )

    assert personal_records[0]["space_type"] == "PERSONAL"
    assert personal_records[0]["current_user_skill_role"] == "OWNER"
    team_roles = {
        record["name"]: record["current_user_skill_role"] for record in team_records
    }
    assert team_roles == {
        "Managed Skill": "MANAGER",
        "Revoked Skill": None,
        "Other User Skill": None,
    }
    assert {record["space_type"] for record in team_records} == {"TEAM"}
