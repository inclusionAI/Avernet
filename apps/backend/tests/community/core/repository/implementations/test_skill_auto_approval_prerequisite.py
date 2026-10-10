"""Skill-only AUTO Grant step against real SQLite persistence."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Barrier
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import SkillGrant, SkillSpaceBinding
from agentclaw.community.core.skill_center.errors import SpaceSkillGrantForbiddenError
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
from agentclaw.community.core.skill_center.services.space_skill_editor_request_service import (
    SpaceSkillEditorRequestService,
)
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderAlreadyPendingError,
    WorkOrderAlreadyProcessedError,
    WorkOrderNotFoundError,
    WorkOrderSkillApplicantAlreadyEditorError,
    WorkOrderSkillEditorRequestNotAllowedError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApprovalMode,
    WorkOrderApproverStatus,
    WorkOrderBizType,
    WorkOrderEventType,
    WorkOrderStatus,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderApproverModel,
    WorkOrderModel,
    WorkOrderNotificationModel,
)
from agentclaw.community.core.work_orders.services.work_order_service import (
    WorkOrderService,
)
from agentclaw.community.plugins.local.database import SqliteDB, reset_for_tests
from agentclaw.community.plugins.local.staff_dept import LocalStaffDeptService


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
            approval_mode="AUTO",
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


def test_editor_approval_policy_is_owner_only_and_defaults_off(db) -> None:
    space_id, skill_id = _space_skill(db)
    repository = _skill_editor_requests(db)

    assert (
        repository.get_editor_approval_policy(
            space_id=space_id, skill_id=skill_id, actor_id="owner-1", env="dev"
        )
        is False
    )
    assert (
        repository.update_editor_approval_policy(
            space_id=space_id,
            skill_id=skill_id,
            actor_id="owner-1",
            auto_approve_editor_requests=True,
            env="dev",
        )
        is True
    )
    assert (
        repository.get_editor_approval_policy(
            space_id=space_id, skill_id=skill_id, actor_id="owner-1", env="dev"
        )
        is True
    )
    with pytest.raises(SpaceSkillGrantForbiddenError):
        repository.get_editor_approval_policy(
            space_id=space_id, skill_id=skill_id, actor_id="applicant-1", env="dev"
        )
    with pytest.raises(SpaceSkillGrantForbiddenError):
        repository.update_editor_approval_policy(
            space_id=space_id,
            skill_id=skill_id,
            actor_id="applicant-1",
            auto_approve_editor_requests=False,
            env="dev",
        )
    with db.orm_session() as session:
        member = (
            session.query(SpaceMemberModel)
            .filter_by(space_id=space_id, user_id="applicant-1", env="dev")
            .one()
        )
        member.role = SpaceRole.ADMIN.value
    with pytest.raises(SpaceSkillGrantForbiddenError):
        repository.get_editor_approval_policy(
            space_id=space_id, skill_id=skill_id, actor_id="applicant-1", env="dev"
        )


def test_editor_approval_policy_is_independent_per_skill_binding(db) -> None:
    space_id, skill_id = _space_skill(db)
    with db.orm_session() as session:
        other = Skill(
            name="other-skill",
            git_path="center://other-skill",
            skill_uuid="33333333-3333-4333-8333-333333333333",
            status="PUBLISHED",
            env="dev",
        )
        session.add(other)
        session.flush()
        other_id = other.id
        session.add(
            SkillSpaceBinding(
                skill_id=other_id, space_id=space_id, created_by="owner-1", env="dev"
            )
        )
        session.add(
            SkillGrant(
                skill_id=other_id,
                user_id="owner-1",
                role="OWNER",
                status="ACTIVE",
                owner_slot=1,
                granted_by="owner-1",
                env="dev",
            )
        )
    repository = _skill_editor_requests(db)
    repository.update_editor_approval_policy(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        auto_approve_editor_requests=True,
        env="dev",
    )

    assert (
        repository.get_editor_approval_policy(
            space_id=space_id, skill_id=skill_id, actor_id="owner-1", env="dev"
        )
        is True
    )
    assert (
        repository.get_editor_approval_policy(
            space_id=space_id, skill_id=other_id, actor_id="owner-1", env="dev"
        )
        is False
    )


def test_enabling_auto_does_not_rewrite_existing_pending_manual_order(db) -> None:
    space_id, skill_id = _space_skill(db)
    order = _work_orders(db).create_skill_editor_request(
        space_id=space_id,
        skill_id=skill_id,
        applicant_user_id="applicant-1",
        applicant_name="Applicant",
        apply_reason="共同维护",
        env="dev",
    )
    _skill_editor_requests(db).update_editor_approval_policy(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        auto_approve_editor_requests=True,
        env="dev",
    )

    with db.orm_session() as session:
        assert session.get(WorkOrderModel, order.id).status == "PENDING"
        approver = (
            session.query(WorkOrderApproverModel)
            .filter_by(work_order_id=order.id, env="dev")
            .one()
        )
        assert (approver.approver_user_id, approver.status) == ("owner-1", "PENDING")
        notice = (
            session.query(WorkOrderNotificationModel)
            .filter_by(work_order_id=order.id, env="dev")
            .one()
        )
        assert (notice.recipient_user_id, notice.notification_category) == (
            "owner-1",
            "APPROVAL",
        )


def test_editor_approval_policy_rejects_personal_or_offline_skill(db) -> None:
    space_id, skill_id = _space_skill(db)
    repository = _skill_editor_requests(db)
    with db.orm_session() as session:
        session.get(SpaceModel, space_id).space_type = "PERSONAL"
    with pytest.raises(SpaceSkillGrantForbiddenError, match="Team Space"):
        repository.get_editor_approval_policy(
            space_id=space_id, skill_id=skill_id, actor_id="owner-1", env="dev"
        )
    with db.orm_session() as session:
        session.get(SpaceModel, space_id).space_type = "TEAM"
        session.get(Skill, skill_id).offline_at = datetime(2026, 10, 9)
    with pytest.raises(SpaceSkillGrantForbiddenError, match="live"):
        repository.update_editor_approval_policy(
            space_id=space_id,
            skill_id=skill_id,
            actor_id="owner-1",
            auto_approve_editor_requests=True,
            env="dev",
        )


def test_manual_skill_editor_request_rejects_when_auto_policy_enabled(db) -> None:
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
        match="must use WorkOrder AUTO",
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


def test_skill_application_auto_path_grants_and_notifies_without_owner_todo(db) -> None:
    space_id, skill_id = _space_skill(db)
    skill_repository = _skill_editor_requests(db)
    skill_repository.update_editor_approval_policy(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        auto_approve_editor_requests=True,
        env="dev",
    )
    work_orders = _work_orders(db)
    staff = LocalStaffDeptService()
    callbacks = MagicMock()
    callbacks.requires_callback.return_value = False
    work_order_service = WorkOrderService(
        work_orders,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        staff,
        MagicMock(),
        callbacks,
    )
    service = SpaceSkillEditorRequestService(
        work_orders, skill_repository, work_order_service, staff, lambda: "dev"
    )

    result = service.create_request(
        space_id=space_id,
        skill_id=skill_id,
        applicant_user_id="applicant-1",
        reason="共同维护",
    )

    assert result.status is WorkOrderStatus.APPROVED
    with db.orm_session() as session:
        order = session.get(WorkOrderModel, result.work_order_id)
        assert (order.status, order.approval_mode, order.reviewer_user_id) == (
            "APPROVED",
            "AUTO",
            "SYSTEM",
        )
        grant = (
            session.query(SkillGrant)
            .filter_by(skill_id=skill_id, user_id="applicant-1", env="dev")
            .one()
        )
        assert (grant.role, grant.status, grant.granted_by) == (
            "MANAGER",
            "ACTIVE",
            "SYSTEM",
        )
        assert session.query(WorkOrderApproverModel).count() == 0
        notices = (
            session.query(WorkOrderNotificationModel)
            .filter_by(work_order_id=result.work_order_id)
            .all()
        )
        assert len(notices) == 1
        assert notices[0].recipient_user_id == "applicant-1"
        assert notices[0].notification_category == "NOTICE"


def test_concurrent_auto_applications_create_only_one_approved_order(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    space_id, skill_id = _space_skill(db)
    skill_repository = _skill_editor_requests(db)
    skill_repository.update_editor_approval_policy(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        auto_approve_editor_requests=True,
        env="dev",
    )
    work_orders = _work_orders(db)
    callbacks = MagicMock()
    callbacks.requires_callback.return_value = False
    staff = LocalStaffDeptService()
    work_order_service = WorkOrderService(
        work_orders,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        staff,
        MagicMock(),
        callbacks,
    )
    service = SpaceSkillEditorRequestService(
        work_orders, skill_repository, work_order_service, staff, lambda: "dev"
    )
    inspected = skill_repository.inspect_editor_request
    both_inspected = Barrier(2)

    def inspect_then_wait(**kwargs):
        admission = inspected(**kwargs)
        both_inspected.wait(timeout=5)
        return admission

    monkeypatch.setattr(skill_repository, "inspect_editor_request", inspect_then_wait)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                service.create_request,
                space_id=space_id,
                skill_id=skill_id,
                applicant_user_id="applicant-1",
                reason="共同维护",
            )
            for _ in range(2)
        ]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=10))
            except Exception as exc:
                outcomes.append(exc)

    approved = [
        outcome
        for outcome in outcomes
        if not isinstance(outcome, Exception)
        and outcome.status is WorkOrderStatus.APPROVED
    ]
    with db.orm_session() as session:
        orders = session.query(WorkOrderModel).all()
        notices = session.query(WorkOrderNotificationModel).all()
        grants = (
            session.query(SkillGrant)
            .filter_by(skill_id=skill_id, user_id="applicant-1", env="dev")
            .all()
        )
        assert (
            len(approved),
            len(orders),
            len(notices),
            len(grants),
        ) == (1, 1, 1, 1)
        assert (
            sum(
                isinstance(
                    outcome,
                    (
                        WorkOrderAlreadyPendingError,
                        WorkOrderSkillApplicantAlreadyEditorError,
                    ),
                )
                for outcome in outcomes
            )
            == 1
        )
        assert orders[0].status == WorkOrderStatus.APPROVED.value


def test_auto_order_creation_rechecks_skill_policy_before_inserting(db) -> None:
    space_id, skill_id = _space_skill(db)

    with pytest.raises(WorkOrderSkillEditorRequestNotAllowedError, match="disabled"):
        _work_orders(db).create_work_order_event(
            event_category=NotificationCategory.APPROVAL,
            approval_mode=WorkOrderApprovalMode.AUTO,
            biz_type=WorkOrderBizType.SKILL_COLLABORATOR.value,
            biz_id=str(skill_id),
            event_type=WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value,
            applicant_user_id="applicant-1",
            approver_user_ids=['applicant-1'],
            recipient_user_ids=[],
            title="Skill editor request",
            content=None,
            apply_reason="共同维护",
            biz_data=json.dumps({"space_id": space_id, "skill_id": skill_id}),
            env="dev",
        )

    with db.orm_session() as session:
        assert session.query(WorkOrderModel).count() == 0
        assert session.query(WorkOrderNotificationModel).count() == 0


def _apply_auto(db, order_id: int) -> None:
    with db.transactional_orm_session() as session:
        _skill_editor_requests(db).apply_auto_skill_editor_request(
            session=session, work_order_id=order_id, env="dev"
        )


def test_auto_skill_step_only_grants_manager_in_caller_transaction(db) -> None:
    _, skill_id, order_id = _claimed_auto_skill_order(db)
    repository = _skill_editor_requests(db)
    with db.transactional_orm_session() as session:
        repository.apply_auto_skill_editor_request(
            session=session, work_order_id=order_id, env="dev"
        )
        repository.apply_auto_skill_editor_request(
            session=session, work_order_id=order_id, env="dev"
        )
        order = session.get(WorkOrderModel, order_id)
        grants = (
            session.query(SkillGrant)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == "applicant-1",
                SkillGrant.env == "dev",
            )
            .all()
        )
        assert order.status == "PROCESSING"
        assert order.reviewer_user_id is None
        assert len(grants) == 1
        assert (grants[0].role, grants[0].status, grants[0].granted_by) == (
            "MANAGER",
            "ACTIVE",
            "SYSTEM",
        )
        assert (
            session.query(WorkOrderNotificationModel)
            .filter(WorkOrderNotificationModel.work_order_id == order_id)
            .count()
            == 0
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
            == 1
        )
        assert (
            session.query(WorkOrderNotificationModel)
            .filter(WorkOrderNotificationModel.work_order_id == order_id)
            .count()
            == 0
        )


def test_auto_skill_step_rechecks_toggle_and_rolls_back(db) -> None:
    _, skill_id, order_id = _claimed_auto_skill_order(db, enabled=False)
    with pytest.raises(WorkOrderSkillEditorRequestNotAllowedError, match="disabled"):
        _apply_auto(db, order_id)
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


def test_auto_skill_step_rechecks_active_member(db) -> None:
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
        _apply_auto(db, order_id)
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


def test_auto_skill_step_rejects_offline_skill(db) -> None:
    _, skill_id, order_id = _claimed_auto_skill_order(db)
    with db.orm_session() as session:
        session.get(Skill, skill_id).offline_at = datetime(2026, 10, 8)
    with pytest.raises(WorkOrderSkillEditorRequestNotAllowedError, match="offline"):
        _apply_auto(db, order_id)


def test_auto_skill_step_preserves_existing_manager_audit(db) -> None:
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
    _apply_auto(db, order_id)
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


def test_auto_skill_step_never_downgrades_owner(db) -> None:
    _, _, order_id = _claimed_auto_skill_order(db)
    with db.orm_session() as session:
        session.get(WorkOrderModel, order_id).applicant_user_id = "owner-1"
    with pytest.raises(WorkOrderSkillApplicantAlreadyEditorError):
        _apply_auto(db, order_id)
    with db.orm_session() as session:
        assert session.get(WorkOrderModel, order_id).status == "PROCESSING"
        owner = (
            session.query(SkillGrant)
            .filter(SkillGrant.user_id == "owner-1", SkillGrant.status == "ACTIVE")
            .one()
        )
        assert owner.role == "OWNER"


def test_auto_skill_step_reactivates_revoked_manager(db) -> None:
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
    _apply_auto(db, order_id)
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


def test_result_notice_persistence_failure_rolls_back_shared_transaction(db) -> None:
    _, skill_id, order_id = _claimed_auto_skill_order(db)
    with pytest.raises(IntegrityError):
        with db.transactional_orm_session() as session:
            _skill_editor_requests(db).apply_auto_skill_editor_request(
                session=session, work_order_id=order_id, env="dev"
            )
            order = session.get(WorkOrderModel, order_id)
            order.status = WorkOrderStatus.APPROVED.value
            order.reviewer_user_id = "SYSTEM"
            session.add(
                WorkOrderNotificationModel(
                    work_order_id=order_id,
                    recipient_user_id=None,  # Force the NOT NULL persistence gate.
                    notification_category=NotificationCategory.NOTICE.value,
                    event_type=WorkOrderEventType.SKILL_COLLABORATOR_REVIEWED.value,
                    biz_type=WorkOrderBizType.SKILL_COLLABORATOR.value,
                    biz_id=str(skill_id),
                    title="Skill 申请已通过",
                    content=json.dumps({"text": "approved"}),
                    env="dev",
                )
            )
            session.flush()
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
def test_auto_skill_step_rejects_untrusted_order_shape(db, unsafe_case) -> None:
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
        _apply_auto(db, order_id)
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


def test_work_order_auto_skill_completion_is_atomic(db) -> None:
    _, skill_id, order_id = _claimed_auto_skill_order(db)
    repository = _work_orders(db)

    repository.apply_auto_skill_editor_request(
        work_order_id=order_id,
        recipient_user_ids=["applicant-1"],
        source_event_type=WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value,
        env="dev",
    )

    with db.orm_session() as session:
        order = session.get(WorkOrderModel, order_id)
        grant = (
            session.query(SkillGrant)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == "applicant-1",
                SkillGrant.env == "dev",
            )
            .one()
        )
        notice = (
            session.query(WorkOrderNotificationModel)
            .filter(WorkOrderNotificationModel.work_order_id == order_id)
            .one()
        )
        assert (order.status, order.reviewer_user_id, order.review_remark) == (
            WorkOrderStatus.APPROVED.value,
            "SYSTEM",
            None,
        )
        assert (grant.role, grant.status, grant.granted_by) == (
            "MANAGER",
            "ACTIVE",
            "SYSTEM",
        )
        assert notice.recipient_user_id == "applicant-1"
        assert notice.notification_category == NotificationCategory.NOTICE.value
        assert notice.event_type == WorkOrderEventType.SKILL_COLLABORATOR_REVIEWED.value
        assert json.loads(notice.content) == {
            "text": "自动审批已通过。",
            "status": WorkOrderStatus.APPROVED.value,
        }


def test_work_order_auto_skill_completion_rejects_non_auto_order(db) -> None:
    _, _, order_id = _claimed_auto_skill_order(db)
    with db.orm_session() as session:
        order = session.get(WorkOrderModel, order_id)
        order.approval_mode = "MANUAL"

    with pytest.raises(WorkOrderAccessDeniedError, match="not an AUTO Skill"):
        _work_orders(db).apply_auto_skill_editor_request(
            work_order_id=order_id,
            recipient_user_ids=["applicant-1"],
            source_event_type=WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value,
            env="dev",
        )

    with db.orm_session() as session:
        order = session.get(WorkOrderModel, order_id)
        assert order.status == WorkOrderStatus.PROCESSING.value
        assert session.query(WorkOrderNotificationModel).count() == 0


def test_work_order_auto_skill_completion_rolls_back_grant_and_status_if_notice_fails(
    db, monkeypatch
) -> None:
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    _, skill_id, order_id = _claimed_auto_skill_order(db)

    def fail_notice_flush(session, _flush_context, instances):
        if any(isinstance(item, WorkOrderNotificationModel) for item in session.new):
            raise RuntimeError("notice insert failed")

    event.listen(Session, "before_flush", fail_notice_flush)
    try:
        with pytest.raises(RuntimeError, match="notice insert failed"):
            _work_orders(db).apply_auto_skill_editor_request(
                work_order_id=order_id,
                recipient_user_ids=["applicant-1"],
                source_event_type=WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value,
                env="dev",
            )
    finally:
        event.remove(Session, "before_flush", fail_notice_flush)

    with db.orm_session() as session:
        order = session.get(WorkOrderModel, order_id)
        assert order.status == WorkOrderStatus.PROCESSING.value
        assert order.reviewer_user_id is None
        assert (
            session.query(SkillGrant)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == "applicant-1",
            )
            .count()
            == 0
        )
        assert session.query(WorkOrderNotificationModel).count() == 0


def test_work_order_auto_skill_completion_rejects_missing_order(db) -> None:
    with pytest.raises(WorkOrderNotFoundError, match="not found"):
        _work_orders(db).apply_auto_skill_editor_request(
            work_order_id=9999,
            recipient_user_ids=["applicant-1"],
            source_event_type=WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value,
            env="dev",
        )


def test_work_order_auto_skill_completion_rejects_non_processing_order(db) -> None:
    _, _, order_id = _claimed_auto_skill_order(db)
    with db.orm_session() as session:
        session.get(WorkOrderModel, order_id).status = WorkOrderStatus.APPROVED.value

    with pytest.raises(WorkOrderAlreadyProcessedError, match="not processing"):
        _work_orders(db).apply_auto_skill_editor_request(
            work_order_id=order_id,
            recipient_user_ids=["applicant-1"],
            source_event_type=WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value,
            env="dev",
        )
