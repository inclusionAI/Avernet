"""Staged Skill AUTO completion against real SQLite persistence."""

import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import SkillGrant, SkillSpaceBinding
from agentclaw.community.core.repository.implementations.skill_center.skill_editor_request import (
    SkillEditorRequestRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.space_skill import (
    SpaceSkillRepository,
)
from agentclaw.community.core.repository.implementations.spaces.space import (
    SpaceRepository,
)
from agentclaw.community.core.repository.implementations.work_orders.work_order import (
    WorkOrderRepository,
)
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.core.spaces.repository.models import SpaceMemberModel
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAlreadyProcessedError,
    WorkOrderSkillApplicantAlreadyEditorError,
    WorkOrderSkillEditorRequestNotAllowedError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApproverStatus,
    WorkOrderBizType,
    WorkOrderStatus,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderApproverModel,
    WorkOrderModel,
    WorkOrderNotificationModel,
)
from agentclaw.community.plugins.local.database import SqliteDB, reset_for_tests


@pytest.fixture
def db():
    reset_for_tests()
    plugin = SqliteDB()
    asyncio.run(plugin.bootstrap())
    yield plugin
    reset_for_tests()


def _skill_editor_requests(db):
    return SkillEditorRequestRepository(db)


def _work_orders(db):
    return WorkOrderRepository(db, _skill_editor_requests(db))


def _space_skills(db):
    return SpaceSkillRepository(db, _skill_editor_requests(db))


def _space_skill(db):
    spaces = SpaceRepository(db)
    with spaces.create_team_transaction(
        name="Team", creator_id="owner-1", creator_user_name=None, env="dev"
    ) as team:
        team.sc_team_id = "sc-Team-owner-1"
        team_id = team.id
    spaces.add_member(
        space_id=team_id,
        user_id="applicant-1",
        role=SpaceRole.MEMBER,
        creator_id="owner-1",
        env="dev",
    )
    created = _space_skills(db).create_space_skill(
        skill_data={
            "name": "review-skill",
            "description": None,
            "env": "dev",
            "skill_uuid": "11111111-1111-4111-8111-111111111111",
            "zip_url": (
                "draft://11111111-1111-4111-8111-111111111111/"
                "v1/22222222-2222-4222-8222-222222222222"
            ),
            "draft_target_version": 1,
            "draft_status": "EDITING",
            "draft_description": "Review skill",
            "draft_source_kind": "FOLDER",
            "creation_request_id": "review-skill-create",
            "creation_request_hash": "a" * 64,
            "source_type": "FOLDER",
        },
        ownership_data={"space_id": team_id, "created_by": "owner-1", "env": "dev"},
        owner_grant_data={
            "user_id": "owner-1",
            "role": "OWNER",
            "granted_by": "owner-1",
            "env": "dev",
        },
    )
    return team_id, created["skill"]["id"]


def _claimed_auto_skill_order(db, *, enabled=True):
    space_id, skill_id = _space_skill(db)
    with db.orm_session() as session:
        binding = (
            session.query(SkillSpaceBinding)
            .filter(
                SkillSpaceBinding.space_id == space_id,
                SkillSpaceBinding.skill_id == skill_id,
                SkillSpaceBinding.env == "dev",
            )
            .one()
        )
        binding.auto_approve_editor_requests = enabled
        order = WorkOrderModel(
            work_order_no="WO-AUTO-1",
            biz_type=WorkOrderBizType.SKILL_COLLABORATOR.value,
            biz_id=str(skill_id),
            biz_data=json.dumps({"space_id": space_id, "skill_id": skill_id}),
            applicant_user_id="applicant-1",
            apply_reason="maintain together",
            status="PROCESSING",
            env="dev",
        )
        session.add(order)
        session.flush()
        order_id = order.id
    return space_id, skill_id, order_id


def test_policy_column_defaults_to_manual_and_migration_covers_existing_bindings(db):
    space_id, skill_id = _space_skill(db)
    with db.orm_session() as session:
        binding = (
            session.query(SkillSpaceBinding)
            .filter(
                SkillSpaceBinding.space_id == space_id,
                SkillSpaceBinding.skill_id == skill_id,
            )
            .one()
        )
        assert binding.auto_approve_editor_requests is False

    migration = (
        Path(__file__).parents[5]
        / "src/agentclaw/community/core/skill_center/sql/2026_10_08_skill_editor_auto_approval.sql"
    ).read_text()
    assert "ADD COLUMN auto_approve_editor_requests" in migration
    assert "NOT NULL DEFAULT 0" in migration

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE ac_skill_space_binding (id INTEGER PRIMARY KEY)"
        )
        connection.exec_driver_sql("INSERT INTO ac_skill_space_binding (id) VALUES (1)")
        connection.exec_driver_sql(migration)
        connection.exec_driver_sql("INSERT INTO ac_skill_space_binding (id) VALUES (2)")
        rows = connection.exec_driver_sql(
            "SELECT id, auto_approve_editor_requests "
            "FROM ac_skill_space_binding ORDER BY id"
        ).all()
    assert rows == [(1, 0), (2, 0)]


def test_skill_editor_request_fails_closed_before_auto_integration(db) -> None:
    space_id, skill_id = _space_skill(db)
    with db.orm_session() as session:
        binding = (
            session.query(SkillSpaceBinding)
            .filter(
                SkillSpaceBinding.space_id == space_id,
                SkillSpaceBinding.skill_id == skill_id,
                SkillSpaceBinding.env == "dev",
            )
            .one()
        )
        binding.auto_approve_editor_requests = True
    with pytest.raises(
        WorkOrderSkillEditorRequestNotAllowedError,
        match="trusted WorkOrder integration",
    ):
        _work_orders(db).create_skill_editor_request(
            space_id=space_id,
            skill_id=skill_id,
            applicant_user_id="applicant-1",
            applicant_name="Applicant",
            apply_reason="maintain together",
            env="dev",
        )
    with db.orm_session() as session:
        assert session.query(WorkOrderModel).count() == 0


def test_auto_skill_editor_approval_grants_manager_and_notifies_once(db) -> None:
    _, skill_id, order_id = _claimed_auto_skill_order(db)
    repository = _skill_editor_requests(db)

    result = repository.approve_auto_skill_editor_request(
        work_order_id=order_id, env="dev"
    )
    retry = repository.approve_auto_skill_editor_request(
        work_order_id=order_id, env="dev"
    )

    assert result == retry
    assert result.status is WorkOrderStatus.APPROVED
    assert result.reviewer_user_id == "SYSTEM"
    with db.orm_session() as session:
        order = session.get(WorkOrderModel, order_id)
        order_status = order.status
        reviewer_user_id = order.reviewer_user_id
        grants = (
            session.query(SkillGrant)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == "applicant-1",
                SkillGrant.env == "dev",
            )
            .all()
        )
        notices = (
            session.query(WorkOrderNotificationModel)
            .filter(
                WorkOrderNotificationModel.work_order_id == order_id,
                WorkOrderNotificationModel.env == "dev",
            )
            .all()
        )
        approvers = (
            session.query(WorkOrderApproverModel)
            .filter(
                WorkOrderApproverModel.work_order_id == order_id,
                WorkOrderApproverModel.env == "dev",
            )
            .all()
        )
        grant_facts = [(grant.role, grant.status, grant.granted_by) for grant in grants]
        notice_facts = [
            (notice.recipient_user_id, notice.notification_category, notice.content)
            for notice in notices
        ]
    assert order_status == WorkOrderStatus.APPROVED.value
    assert reviewer_user_id == "SYSTEM"
    assert len(grant_facts) == 1
    assert grant_facts[0] == ("MANAGER", "ACTIVE", "SYSTEM")
    assert len(notice_facts) == 1
    assert notice_facts[0][:2] == ("applicant-1", NotificationCategory.NOTICE.value)
    assert json.loads(notice_facts[0][2]) == {
        "text": "你共同编辑 Skill「review-skill」的申请已通过。"
    }
    assert approvers == []


def test_auto_skill_editor_approval_rechecks_toggle_and_rolls_back(db) -> None:
    _, skill_id, order_id = _claimed_auto_skill_order(db, enabled=False)

    with pytest.raises(WorkOrderSkillEditorRequestNotAllowedError, match="disabled"):
        _skill_editor_requests(db).approve_auto_skill_editor_request(
            work_order_id=order_id, env="dev"
        )

    with db.orm_session() as session:
        assert session.get(WorkOrderModel, order_id).status == "PROCESSING"
        assert (
            session.query(SkillGrant)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == "applicant-1",
            )
            .count()
            == 0
        )
        assert (
            session.query(WorkOrderNotificationModel)
            .filter(WorkOrderNotificationModel.work_order_id == order_id)
            .count()
            == 0
        )


def test_auto_skill_editor_approval_rechecks_active_member(db) -> None:
    space_id, skill_id, order_id = _claimed_auto_skill_order(db)
    with db.orm_session() as session:
        member = (
            session.query(SpaceMemberModel)
            .filter(
                SpaceMemberModel.space_id == space_id,
                SpaceMemberModel.user_id == "applicant-1",
            )
            .one()
        )
        member.status = "INACTIVE"

    with pytest.raises(WorkOrderSkillEditorRequestNotAllowedError, match="active"):
        _skill_editor_requests(db).approve_auto_skill_editor_request(
            work_order_id=order_id, env="dev"
        )

    with db.orm_session() as session:
        assert session.get(WorkOrderModel, order_id).status == "PROCESSING"
        assert (
            session.query(SkillGrant)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == "applicant-1",
            )
            .count()
            == 0
        )


def test_auto_skill_editor_approval_rejects_offline_skill(db) -> None:
    _, skill_id, order_id = _claimed_auto_skill_order(db)
    with db.orm_session() as session:
        session.get(Skill, skill_id).offline_at = datetime(2026, 10, 8)

    with pytest.raises(WorkOrderSkillEditorRequestNotAllowedError, match="offline"):
        _skill_editor_requests(db).approve_auto_skill_editor_request(
            work_order_id=order_id, env="dev"
        )

    with db.orm_session() as session:
        assert session.get(WorkOrderModel, order_id).status == "PROCESSING"
        assert (
            session.query(SkillGrant)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == "applicant-1",
            )
            .count()
            == 0
        )


def test_auto_skill_editor_approval_preserves_existing_manager_audit(db) -> None:
    space_id, skill_id, order_id = _claimed_auto_skill_order(db)
    _space_skills(db).add_manager(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        manager_user_id="applicant-1",
        env="dev",
    )
    with db.orm_session() as session:
        grant = (
            session.query(SkillGrant)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == "applicant-1",
            )
            .one()
        )
        before = (grant.id, grant.granted_by, grant.grant_reason, grant.gmt_modified)

    _skill_editor_requests(db).approve_auto_skill_editor_request(
        work_order_id=order_id, env="dev"
    )

    with db.orm_session() as session:
        grant = (
            session.query(SkillGrant)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == "applicant-1",
            )
            .one()
        )
        assert (
            grant.id,
            grant.granted_by,
            grant.grant_reason,
            grant.gmt_modified,
        ) == before


def test_auto_skill_editor_approval_never_downgrades_owner(db) -> None:
    _, _, order_id = _claimed_auto_skill_order(db)
    with db.orm_session() as session:
        session.get(WorkOrderModel, order_id).applicant_user_id = "owner-1"

    with pytest.raises(WorkOrderSkillApplicantAlreadyEditorError):
        _skill_editor_requests(db).approve_auto_skill_editor_request(
            work_order_id=order_id, env="dev"
        )

    with db.orm_session() as session:
        assert session.get(WorkOrderModel, order_id).status == "PROCESSING"
        owner = (
            session.query(SkillGrant)
            .filter(SkillGrant.user_id == "owner-1", SkillGrant.status == "ACTIVE")
            .one()
        )
        assert owner.role == "OWNER"


def test_auto_skill_editor_approval_reactivates_revoked_manager(db) -> None:
    space_id, skill_id, order_id = _claimed_auto_skill_order(db)
    _space_skills(db).add_manager(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        manager_user_id="applicant-1",
        env="dev",
    )
    with db.orm_session() as session:
        grant = (
            session.query(SkillGrant)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == "applicant-1",
            )
            .one()
        )
        grant.status = "REVOKED"

    _skill_editor_requests(db).approve_auto_skill_editor_request(
        work_order_id=order_id, env="dev"
    )

    with db.orm_session() as session:
        grants = (
            session.query(SkillGrant)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == "applicant-1",
            )
            .all()
        )
        assert len(grants) == 1
        assert (grants[0].role, grants[0].status, grants[0].granted_by) == (
            "MANAGER",
            "ACTIVE",
            "SYSTEM",
        )


def test_auto_skill_editor_notification_failure_rolls_back_grant(db) -> None:
    _, skill_id, order_id = _claimed_auto_skill_order(db)

    def fail_notice(_mapper, _connection, _target):
        raise RuntimeError("notice insert failed")

    event.listen(WorkOrderNotificationModel, "before_insert", fail_notice)
    try:
        with pytest.raises(RuntimeError, match="notice insert failed"):
            _skill_editor_requests(db).approve_auto_skill_editor_request(
                work_order_id=order_id, env="dev"
            )
    finally:
        event.remove(WorkOrderNotificationModel, "before_insert", fail_notice)

    with db.orm_session() as session:
        assert session.get(WorkOrderModel, order_id).status == "PROCESSING"
        assert (
            session.query(SkillGrant)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == "applicant-1",
            )
            .count()
            == 0
        )
        assert (
            session.query(WorkOrderNotificationModel)
            .filter(WorkOrderNotificationModel.work_order_id == order_id)
            .count()
            == 0
        )


@pytest.mark.parametrize("unsafe_case", ["pending", "approver", "identity"])
def test_auto_skill_editor_approval_rejects_untrusted_order_shape(
    db, unsafe_case
) -> None:
    _, skill_id, order_id = _claimed_auto_skill_order(db)
    with db.orm_session() as session:
        order = session.get(WorkOrderModel, order_id)
        if unsafe_case == "pending":
            order.status = WorkOrderStatus.PENDING.value
        elif unsafe_case == "approver":
            session.add(
                WorkOrderApproverModel(
                    work_order_id=order_id,
                    approver_user_id="owner-1",
                    status=WorkOrderApproverStatus.PENDING.value,
                    env="dev",
                )
            )
        else:
            order.biz_id = "another-skill"

    with pytest.raises(
        (WorkOrderAlreadyProcessedError, WorkOrderSkillEditorRequestNotAllowedError)
    ):
        _skill_editor_requests(db).approve_auto_skill_editor_request(
            work_order_id=order_id, env="dev"
        )

    with db.orm_session() as session:
        assert (
            session.query(SkillGrant)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == "applicant-1",
            )
            .count()
            == 0
        )
        assert (
            session.query(WorkOrderNotificationModel)
            .filter(WorkOrderNotificationModel.work_order_id == order_id)
            .count()
            == 0
        )
