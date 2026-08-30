"""F01 additive Space Skill persistence contract tests."""

from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.repository.implementations.skill_center.space_skill import (
    SpaceSkillRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_editor_request import (
    SkillEditorRequestRepository,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    DraftEditLeaseRepository,
)
from agentclaw.community.core.models.space_skill import (
    SkillDraftEditLease,
    SkillGrant,
    SkillPublicationAttempt,
    SkillSpaceBinding,
    SkillVersion,
    Space,
    SpaceMember,
)
from agentclaw.community.core.skill_center.errors import (
    DraftEditLeaseConflictError,
    DraftEditLeaseNotFoundError,
    DraftEditLeaseTokenRejectedError,
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

    transactional_orm_session = orm_session


@pytest.fixture
def db() -> _Database:
    return _Database()


def _space_skills(db: _Database) -> SpaceSkillRepository:
    return SpaceSkillRepository(db, SkillEditorRequestRepository(db))


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
    repo = _space_skills(db)
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
    repo = _space_skills(db)
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
        session.add(
            SkillGrant(
                skill_id=8,
                user_id="owner-without-slot",
                role="OWNER",
                status="ACTIVE",
                owner_slot=None,
                granted_by="owner-without-slot",
                env="dev",
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
    repo = _space_skills(db)
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
    repo = _space_skills(db)
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

    repo = _space_skills(db)
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
    repo = _space_skills(db)
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

    repo = _space_skills(db)
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
    repo = _space_skills(db)
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


def _grant_fixture(db):
    repo = _space_skills(db)
    space = repo.create_space(
        {
            "space_code": "grant-team",
            "space_type": "TEAM",
            "name": "Grant Team",
            "created_by": "space-admin",
            "env": "dev",
        }
    )
    with db.orm_session() as session:
        session.add_all(
            [
                SpaceMember(
                    space_id=space["id"],
                    user_id=user_id,
                    role=role,
                    created_by="space-admin",
                    env="dev",
                )
                for user_id, role in (
                    ("space-admin", "ADMIN"),
                    ("owner-1", "MEMBER"),
                    ("manager-1", "MEMBER"),
                    ("member-2", "MEMBER"),
                )
            ]
        )
    skill_id = _add_bound_skill(
        db,
        space_id=space["id"],
        name="Grant Skill",
        grant_user_id="owner-1",
        grant_role="OWNER",
    )
    return repo, space["id"], skill_id


def test_grant_repository_add_remove_manager_is_idempotent(db):
    repo, space_id, skill_id = _grant_fixture(db)

    first = repo.add_manager(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        manager_user_id="manager-1",
        env="dev",
    )
    second = repo.add_manager(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        manager_user_id="manager-1",
        env="dev",
    )
    removed = repo.remove_manager(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        manager_user_id="manager-1",
        env="dev",
    )
    removed_again = repo.remove_manager(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        manager_user_id="manager-1",
        env="dev",
    )

    assert first == second == removed == removed_again == {
        "user_id": "manager-1",
        "role": "MANAGER",
    }
    assert repo.list_grants(
        space_id=space_id, skill_id=skill_id, actor_id="owner-1", env="dev"
    )["managers"] == []


def test_grant_repository_rejects_non_member_without_partial_write(db):
    from agentclaw.community.core.skill_center.errors import (
        SpaceSkillGrantMemberRequiredError,
    )

    repo, space_id, skill_id = _grant_fixture(db)

    with pytest.raises(SpaceSkillGrantMemberRequiredError):
        repo.add_manager(
            space_id=space_id,
            skill_id=skill_id,
            actor_id="owner-1",
            manager_user_id="outsider",
            env="dev",
        )

    grants = repo.list_grants(
        space_id=space_id, skill_id=skill_id, actor_id="owner-1", env="dev"
    )
    assert grants["owner"]["user_id"] == "owner-1"
    assert grants["managers"] == []


def test_owner_transfer_atomically_keeps_exactly_one_owner(db):
    repo, space_id, skill_id = _grant_fixture(db)
    repo.add_manager(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        manager_user_id="member-2",
        env="dev",
    )

    result = repo.transfer_owner(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        new_owner_user_id="member-2",
        reason=None,
        env="dev",
    )

    assert result["owner"] == {"user_id": "member-2", "role": "OWNER"}
    assert result["managers"] == []
    with db.orm_session() as session:
        active = session.query(SkillGrant).filter_by(
            skill_id=skill_id, status="ACTIVE", env="dev"
        ).all()
        assert [(grant.user_id, grant.role, grant.owner_slot) for grant in active] == [
            ("member-2", "OWNER", 1)
        ]


def test_space_admin_owner_transfer_persists_the_audit_reason(db):
    repo, space_id, skill_id = _grant_fixture(db)

    repo.transfer_owner(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="space-admin",
        new_owner_user_id="member-2",
        reason="handover approved by the space administrator",
        env="dev",
    )

    with db.orm_session() as session:
        owner = session.query(SkillGrant).filter_by(
            skill_id=skill_id, role="OWNER", status="ACTIVE", env="dev"
        ).one()
        assert owner.user_id == "member-2"
        assert owner.granted_by == "space-admin"
        assert owner.grant_reason == "handover approved by the space administrator"


def test_grant_write_rechecks_owner_membership_inside_the_transaction(db):
    from agentclaw.community.core.skill_center.errors import (
        SpaceSkillGrantForbiddenError,
    )

    repo, space_id, skill_id = _grant_fixture(db)
    with db.orm_session() as session:
        owner_member = session.query(SpaceMember).filter_by(
            space_id=space_id, user_id="owner-1", env="dev"
        ).one()
        owner_member.status = "INACTIVE"

    with pytest.raises(SpaceSkillGrantForbiddenError):
        repo.add_manager(
            space_id=space_id,
            skill_id=skill_id,
            actor_id="owner-1",
            manager_user_id="manager-1",
            env="dev",
        )

    assert repo.list_grants(
        space_id=space_id, skill_id=skill_id, actor_id="owner-1", env="dev"
    )["managers"] == []


def test_owner_transfer_rechecks_admin_reason_inside_the_transaction(db):
    from agentclaw.community.core.skill_center.errors import (
        SpaceSkillGrantReasonRequiredError,
    )

    repo, space_id, skill_id = _grant_fixture(db)

    with pytest.raises(SpaceSkillGrantReasonRequiredError):
        repo.transfer_owner(
            space_id=space_id,
            skill_id=skill_id,
            actor_id="space-admin",
            new_owner_user_id="member-2",
            reason=None,
            env="dev",
        )

    assert repo.list_grants(
        space_id=space_id, skill_id=skill_id, actor_id="owner-1", env="dev"
    )["owner"]["user_id"] == "owner-1"


def test_concurrent_owner_transfers_leave_one_owner_and_surface_the_loser(tmp_path):
    class _FileDatabase(_Database):
        def __init__(self, path: Path):
            self.engine = create_engine(
                f"sqlite:///{path}", connect_args={"timeout": 1}
            )
            Base.metadata.create_all(self.engine)
            self._factory = sessionmaker(bind=self.engine)

    concurrent_db = _FileDatabase(tmp_path / "grant-race.sqlite")
    repo, space_id, skill_id = _grant_fixture(concurrent_db)
    start = Barrier(2)

    def transfer(target: str):
        start.wait()
        try:
            return repo.transfer_owner(
                space_id=space_id,
                skill_id=skill_id,
                actor_id="owner-1",
                new_owner_user_id=target,
                reason=None,
                env="dev",
            )
        except Exception as exc:  # the losing transaction must stay observable
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(transfer, ("member-2", "manager-1")))

    assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, Exception) for outcome in outcomes) == 1
    with concurrent_db.orm_session() as session:
        active_owners = session.query(SkillGrant).filter_by(
            skill_id=skill_id,
            role="OWNER",
            status="ACTIVE",
            owner_slot=1,
            env="dev",
        ).all()
        assert len(active_owners) == 1


def test_draft_edit_lease_lifecycle_permanently_fences_released_tokens(db):
    repo, space_id, skill_id = _grant_fixture(db)
    assert isinstance(repo, DraftEditLeaseRepository)

    acquired = repo.acquire(
        space_id=space_id, skill_id=skill_id, actor_id="owner-1", env="dev"
    )
    acquired_again = repo.acquire(
        space_id=space_id, skill_id=skill_id, actor_id="owner-1", env="dev"
    )
    assert acquired == {"holder_user_id": "owner-1", "fencing_token": 1}
    assert acquired_again == {"holder_user_id": "owner-1", "fencing_token": 2}

    released = repo.release(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        fencing_token=2,
        env="dev",
    )
    assert released == {"holder_user_id": None, "fencing_token": 3}

    reacquired = repo.acquire(
        space_id=space_id, skill_id=skill_id, actor_id="owner-1", env="dev"
    )
    assert reacquired["fencing_token"] == 4
    with pytest.raises(DraftEditLeaseTokenRejectedError):
        repo.release(
            space_id=space_id,
            skill_id=skill_id,
            actor_id="owner-1",
            fencing_token=1,
            env="dev",
        )


def test_lease_read_rejects_a_bound_skill_without_an_editable_draft(db):
    repo, space_id, skill_id = _grant_fixture(db)
    with db.orm_session() as session:
        session.query(Skill).filter_by(id=skill_id, env="dev").one().draft_status = None

    with pytest.raises(DraftEditLeaseNotFoundError):
        repo.get_lease(space_id=space_id, skill_id=skill_id, env="dev")


def test_acquire_refuses_another_holder_but_takeover_fences_them(db):
    repo, space_id, skill_id = _grant_fixture(db)
    repo.add_manager(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        manager_user_id="manager-1",
        env="dev",
    )
    first = repo.acquire(
        space_id=space_id, skill_id=skill_id, actor_id="manager-1", env="dev"
    )

    with pytest.raises(DraftEditLeaseConflictError):
        repo.acquire(
            space_id=space_id, skill_id=skill_id, actor_id="owner-1", env="dev"
        )

    taken = repo.takeover(
        space_id=space_id, skill_id=skill_id, actor_id="owner-1", env="dev"
    )
    assert taken == {"holder_user_id": "owner-1", "fencing_token": 2}
    with pytest.raises(DraftEditLeaseTokenRejectedError):
        repo.release(
            space_id=space_id,
            skill_id=skill_id,
            actor_id="manager-1",
            fencing_token=first["fencing_token"],
            env="dev",
        )


def test_removing_manager_invalidates_held_lease_in_the_same_transaction(db):
    repo, space_id, skill_id = _grant_fixture(db)
    repo.add_manager(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        manager_user_id="manager-1",
        env="dev",
    )
    held = repo.acquire(
        space_id=space_id, skill_id=skill_id, actor_id="manager-1", env="dev"
    )

    repo.remove_manager(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        manager_user_id="manager-1",
        env="dev",
    )

    assert repo.get_lease(
        space_id=space_id, skill_id=skill_id, env="dev"
    ) == {"holder_user_id": None, "fencing_token": held["fencing_token"] + 1}


def test_owner_transfer_invalidates_any_existing_lease_atomically(db):
    repo, space_id, skill_id = _grant_fixture(db)
    held = repo.acquire(
        space_id=space_id, skill_id=skill_id, actor_id="owner-1", env="dev"
    )

    repo.transfer_owner(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        new_owner_user_id="member-2",
        reason=None,
        env="dev",
    )

    assert repo.get_lease(
        space_id=space_id, skill_id=skill_id, env="dev"
    ) == {"holder_user_id": None, "fencing_token": held["fencing_token"] + 1}


def test_lease_schema_contains_no_ttl_or_renewal_columns(db):
    columns = {column.name for column in SkillDraftEditLease.__table__.columns}

    assert "expires_at" not in columns
    assert "renewed_at" not in columns
    sql_dir = (
        Path(__file__).parents[4]
        / "src"
        / "agentclaw"
        / "community"
        / "core"
        / "skill_center"
        / "sql"
    )
    additive = (sql_dir / "2026_08_19_additive_space_skill_schema.sql").read_text()
    migration = (sql_dir / "2026_08_26_finalize_draft_edit_lease.sql").read_text()
    assert "expires_at TIMESTAMP" not in additive
    assert "renewed_at TIMESTAMP" not in additive
    assert "SET holder_user_id = NULL, fencing_token = fencing_token + 1" in migration
    assert "expires_at <= CURRENT_TIMESTAMP" in migration
    assert migration.index("finalize_expired_lease_stmt") < migration.index(
        "DROP COLUMN expires_at"
    )
    assert "DROP COLUMN expires_at" in migration
    assert "DROP COLUMN renewed_at" in migration


def test_concurrent_acquire_has_one_holder_and_surfaces_the_loser(tmp_path):
    class _FileDatabase(_Database):
        def __init__(self, path: Path):
            self.engine = create_engine(
                f"sqlite:///{path}", connect_args={"timeout": 1}
            )
            Base.metadata.create_all(self.engine)
            self._factory = sessionmaker(bind=self.engine)

    concurrent_db = _FileDatabase(tmp_path / "lease-acquire-race.sqlite")
    repo, space_id, skill_id = _grant_fixture(concurrent_db)
    repo.add_manager(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        manager_user_id="manager-1",
        env="dev",
    )
    start = Barrier(2)

    def acquire(actor_id: str):
        start.wait()
        try:
            return repo.acquire(
                space_id=space_id,
                skill_id=skill_id,
                actor_id=actor_id,
                env="dev",
            )
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(acquire, ("owner-1", "manager-1")))

    assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, Exception) for outcome in outcomes) == 1
    with concurrent_db.orm_session() as session:
        lease = (
            session.query(SkillDraftEditLease)
            .filter_by(skill_id=skill_id, env="dev")
            .one()
        )
        assert lease.holder_user_id in {"owner-1", "manager-1"}
        assert lease.fencing_token == 1


def test_database_failure_rolls_back_grant_revocation_and_lease_invalidation(db):
    class _FailNextCommitDatabase:
        def __init__(self, inner):
            self._inner = inner
            self.fail_next_commit = False

        @contextmanager
        def orm_session(self):
            session = self._inner._factory()
            try:
                yield session
                if self.fail_next_commit:
                    self.fail_next_commit = False
                    raise RuntimeError("database commit failed")
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        transactional_orm_session = orm_session

    controlled_db = _FailNextCommitDatabase(db)
    repo, space_id, skill_id = _grant_fixture(controlled_db)
    repo.add_manager(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        manager_user_id="manager-1",
        env="dev",
    )
    held = repo.acquire(
        space_id=space_id, skill_id=skill_id, actor_id="manager-1", env="dev"
    )
    controlled_db.fail_next_commit = True

    with pytest.raises(RuntimeError, match="database commit failed"):
        repo.remove_manager(
            space_id=space_id,
            skill_id=skill_id,
            actor_id="owner-1",
            manager_user_id="manager-1",
            env="dev",
        )

    grants = repo.list_grants(
        space_id=space_id, skill_id=skill_id, actor_id="owner-1", env="dev"
    )
    assert grants["managers"] == [{"user_id": "manager-1", "role": "MANAGER"}]
    assert (
        repo.get_lease(
            space_id=space_id, skill_id=skill_id, env="dev"
        )
        == held
    )


def test_concurrent_takeovers_never_reuse_a_successful_fencing_token(tmp_path):
    class _FileDatabase(_Database):
        def __init__(self, path: Path):
            self.engine = create_engine(
                f"sqlite:///{path}", connect_args={"timeout": 1}
            )
            Base.metadata.create_all(self.engine)
            self._factory = sessionmaker(bind=self.engine)

    concurrent_db = _FileDatabase(tmp_path / "lease-takeover-race.sqlite")
    repo, space_id, skill_id = _grant_fixture(concurrent_db)
    for manager_id in ("manager-1", "member-2"):
        repo.add_manager(
            space_id=space_id,
            skill_id=skill_id,
            actor_id="owner-1",
            manager_user_id=manager_id,
            env="dev",
        )
    repo.acquire(
        space_id=space_id, skill_id=skill_id, actor_id="owner-1", env="dev"
    )
    start = Barrier(2)

    def takeover(actor_id: str):
        start.wait()
        try:
            return repo.takeover(
                space_id=space_id,
                skill_id=skill_id,
                actor_id=actor_id,
                env="dev",
            )
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(takeover, ("manager-1", "member-2")))

    successful_tokens = [
        outcome["fencing_token"]
        for outcome in outcomes
        if not isinstance(outcome, Exception)
    ]
    assert successful_tokens
    assert len(successful_tokens) == len(set(successful_tokens))
    current = repo.get_lease(
        space_id=space_id, skill_id=skill_id, env="dev"
    )
    assert current["fencing_token"] == 1 + len(successful_tokens)
