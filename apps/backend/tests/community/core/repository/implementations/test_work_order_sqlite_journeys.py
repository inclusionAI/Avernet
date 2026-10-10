"""Real joined services and file SQLite; only external boundaries are mocked."""

import asyncio
from types import SimpleNamespace
from unittest.mock import create_autospec

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from agentclaw.community.core.bot_collaborator.services.member_management_capability import (
    MemberManagementCapabilityService,
)
from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.models.space_skill import SkillGrant
from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.core.repository.implementations.bot.collaborator import (
    CollaboratorRepository,
)
from agentclaw.community.core.repository.implementations.spaces.space import (
    SpaceRepository,
)
from agentclaw.community.core.skill_center.services.skill_collaborator_approval_handler import (
    SkillCollaboratorApprovalHandler,
)
from agentclaw.community.core.skill_center.services.space_skill_editor_request_service import (
    SpaceSkillEditorRequestService,
)
from agentclaw.community.core.spaces.services.space_access_service import (
    SpaceAccessService,
)
from agentclaw.community.core.work_orders.callbacks import (
    WorkOrderCallbackCredential,
    WorkOrderDecisionCallbackDispatcher,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAlreadyProcessedError,
    WorkOrderCallbackError,
    WorkOrderLocalFinalizeError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApprovalMode,
    WorkOrderBizType,
    WorkOrderDecision,
    WorkOrderEventType,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderModel,
    WorkOrderApproverModel,
    WorkOrderNotificationModel,
)
from agentclaw.community.core.work_orders.services.work_order_service import (
    WorkOrderService,
    WorkOrderNotificationService,
)
from agentclaw.community.plugin_api.http_client import HttpClient
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugin_api.staff_dept import StaffDeptPlugin, StaffProfileInfo
from agentclaw.community.plugins.local import database
from tests.community.core.repository.implementations.test_skill_auto_approval_prerequisite import (
    _space_skill,
    _skill_editor_requests,
    _work_orders,
)


@pytest.fixture
def world(tmp_path, monkeypatch):
    database.reset_for_tests()
    path = tmp_path / "work-order-journey.sqlite"
    monkeypatch.setattr(database, "_DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("ENV", "dev")
    db = database.SqliteDB()
    asyncio.run(db.bootstrap())
    space_id, skill_id = _space_skill(db)
    with db.orm_session() as session:
        bot = BotModel(
            bot_id="journey-bot",
            bot_name="Journey",
            entity_id="owner-1",
            entity_type="user",
            creator_id="owner-1",
            owner_id="owner-1",
            status="ACTIVE",
            bot_type="service",
            space_id=space_id,
            env="dev",
        )
        session.add(bot)
        session.flush()
        bot_pk = bot.id
    repo = _work_orders(db)
    skills = _skill_editor_requests(db)
    spaces = SpaceRepository(db)
    staff = create_autospec(StaffDeptPlugin, instance=True)
    staff.get_profile_by_work_no.return_value = StaffProfileInfo(
        work_no="applicant-1", nick_name="Applicant"
    )
    http = create_autospec(HttpClient, instance=True)
    http.post.return_value = httpx.Response(
        200, json={"code": 20000}, request=httpx.Request("POST", "https://bcn.invalid/")
    )
    downstream = create_autospec(CollaboratorServiceProtocol, instance=True)
    handler = SkillCollaboratorApprovalHandler(skills, lambda: "dev")
    service = WorkOrderService(
        repo,
        spaces,
        SpaceAccessService(spaces),
        WorkOrderNotificationService(repo),
        BotRepository(db),
        CollaboratorRepository(db),
        downstream,
        create_autospec(MemberManagementCapabilityService, instance=True),
        staff,
        handler,
        WorkOrderDecisionCallbackDispatcher(http, resign_principal=lambda value: value),
    )
    entry = SpaceSkillEditorRequestService(repo, skills, service, staff, lambda: "dev")
    yield SimpleNamespace(
        db=db,
        path=path,
        repo=repo,
        service=service,
        entry=entry,
        space_id=space_id,
        skill_id=skill_id,
        bot_pk=bot_pk,
        http=http,
        downstream=downstream,
        handler=handler,
    )
    database.reset_for_tests()


def snapshot(w, order_id):
    with w.db.orm_session() as session:
        order = session.get(WorkOrderModel, order_id)
        result = {
            "status": order.status,
            "reviewer": order.reviewer_user_id,
            "approvers": session.query(WorkOrderApproverModel)
            .filter_by(work_order_id=order_id)
            .count(),
            "notices": [
                (row.notification_category, row.recipient_user_id)
                for row in session.query(WorkOrderNotificationModel)
                .filter_by(work_order_id=order_id)
                .order_by(WorkOrderNotificationModel.id)
            ],
            "grants": session.query(SkillGrant)
            .filter_by(skill_id=w.skill_id, user_id="applicant-1", status="ACTIVE")
            .count(),
            "collaborators": session.query(BotCollaboratorModel)
            .filter_by(bot_pk=w.bot_pk, user_id="applicant-1")
            .count(),
        }
    print(f"STEP order={order_id}: {result}")
    return result


def review(w, order_id, decision=WorkOrderDecision.APPROVED, actor="owner-1"):
    return w.service.process_approval(
        work_order_id=order_id,
        actor_id=actor,
        decision=decision,
        review_remark="reviewed",
        callback_credential=WorkOrderCallbackCredential(headers={}),
    )


def friend(w, auto):
    return w.service.create_work_order_event(
        event_category=NotificationCategory.APPROVAL,
        approval_mode=WorkOrderApprovalMode.AUTO
        if auto
        else WorkOrderApprovalMode.MANUAL,
        biz_type=WorkOrderBizType.BOT_FRIEND.value,
        biz_id="friend-request",
        event_type=WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED.value,
        applicant_user_id="applicant-1",
        approver_user_ids=["owner-1"],
        recipient_user_ids=[],
        title="Friend request",
        content=None,
        apply_reason="connect",
        biz_data={"request_ids": ["request-local"]},
        actor_id="applicant-1",
    )


@pytest.mark.parametrize("kind", ["skill", "bot", "friend"])
@pytest.mark.parametrize("mode", ["auto", "approve", "reject"])
def test_joined_success_and_manual_rejection(world, kind, mode):
    w = world
    auto = mode == "auto"
    print(f"JOURNEY {kind}/{mode} SQLite={w.path}")
    if kind == "skill":
        w.entry.update_approval_policy(
            space_id=w.space_id,
            skill_id=w.skill_id,
            actor_id="owner-1",
            auto_approve_editor_requests=auto,
        )
        result = w.entry.create_request(
            space_id=w.space_id,
            skill_id=w.skill_id,
            applicant_user_id="applicant-1",
            reason="edit",
        )
        order_id = result.work_order_id
    elif kind == "bot":
        w.service.update_bot_editor_request_policy(
            bot_id="journey-bot",
            owner_id="owner-1",
            actor_id="owner-1",
            auto_approve=auto,
        )
        result = w.service.create_bot_editor_request(
            bot_id="journey-bot",
            owner_id="owner-1",
            applicant_user_id="applicant-1",
            reason="edit",
        )
        order_id = result.id
    else:
        callback_states = []
        response = w.http.post.return_value

        def callback(*args, **kwargs):
            with w.db.orm_session() as session:
                order_id = session.query(WorkOrderModel).one().id
            callback_states.append(snapshot(w, order_id))
            return response

        w.http.post.side_effect = callback
        order_id = friend(w, auto).work_order_id
    created = snapshot(w, order_id)
    assert w.path.is_file()
    if not auto:
        assert created["status"] == "PENDING"
        assert created["grants"] == created["collaborators"] == 0
        assert created["approvers"] == 1
        assert created["notices"] == [("APPROVAL", "owner-1")]
        w.http.post.assert_not_called()
        review(
            w,
            order_id,
            WorkOrderDecision.REJECTED
            if mode == "reject"
            else WorkOrderDecision.APPROVED,
        )
    final = snapshot(w, order_id)
    assert final["status"] == ("REJECTED" if mode == "reject" else "APPROVED")
    assert final["grants"] == int(kind == "skill" and mode != "reject")
    assert final["collaborators"] == int(kind == "bot" and mode != "reject")
    assert final["notices"][-1] == (
        "NOTICE",
        "owner-1" if kind == "friend" and auto else "applicant-1",
    )
    if auto:
        assert final["approvers"] == 0
        assert final["reviewer"] == (None if kind == "bot" else "SYSTEM")
    if kind == "friend":
        assert len(callback_states) == 1
        assert callback_states[0]["status"] == ("PROCESSING" if auto else "PENDING")
        assert not any(
            category == "NOTICE" for category, _ in callback_states[0]["notices"]
        )
        assert w.http.post.call_count == 1
    else:
        w.http.post.assert_not_called()
    assert w.downstream.on_collaboration_changed.call_count == int(
        kind == "bot" and mode != "reject"
    )
    with pytest.raises(WorkOrderAlreadyProcessedError):
        review(
            w, order_id, actor="applicant-1" if auto and kind != "friend" else "owner-1"
        )
    assert snapshot(w, order_id) == final


@pytest.mark.parametrize("auto", [False, True])
def test_friend_callback_failure_keeps_db_consistent(world, auto):
    w = world
    w.http.post.side_effect = TimeoutError("remote outcome unknown")
    if auto:
        result = friend(w, True)
        order_id = result.work_order_id
        assert result.status.value == "FAILED"
    else:
        order_id = friend(w, False).work_order_id
        with pytest.raises(WorkOrderCallbackError):
            review(w, order_id)
    state = snapshot(w, order_id)
    assert state["status"] == ("FAILED" if auto else "PENDING")
    assert sum(category == "NOTICE" for category, _ in state["notices"]) == int(auto)


def reject_result_notice(session, _context, _instances):
    if any(
        isinstance(row, WorkOrderNotificationModel)
        and row.notification_category == "NOTICE"
        for row in session.new
    ):
        raise RuntimeError("injected result notice storage failure")


@pytest.mark.parametrize("auto", [False, True])
def test_friend_remote_success_local_write_failure(world, auto, caplog):
    caplog.set_level("INFO", logger="start")
    w = world
    if not auto:
        order_id = friend(w, False).work_order_id
    event.listen(Session, "before_flush", reject_result_notice)
    try:
        with pytest.raises(WorkOrderLocalFinalizeError) as failure:
            if auto:
                friend(w, True)
            else:
                review(w, order_id)
    finally:
        event.remove(Session, "before_flush", reject_result_notice)
    with w.db.orm_session() as session:
        order_id = session.query(WorkOrderModel).one().id
    assert failure.value.work_order_id == order_id
    assert isinstance(failure.value.__cause__, RuntimeError)
    assert any(getattr(r, "remote_confirmed", False) for r in caplog.records)
    state = snapshot(w, order_id)
    assert state["status"] == ("PROCESSING" if auto else "PENDING")
    assert not any(category == "NOTICE" for category, _ in state["notices"])
    assert w.http.post.call_count == 1
    if not auto:
        review(w, order_id)
        assert w.http.post.call_count == 2
        assert snapshot(w, order_id)["status"] == "APPROVED"


@pytest.mark.parametrize("auto", [False, True])
def test_bot_downstream_failure_after_commit(world, auto, caplog):
    caplog.set_level("INFO", logger="start")
    w = world
    w.service.update_bot_editor_request_policy(
        bot_id="journey-bot", owner_id="owner-1", actor_id="owner-1", auto_approve=auto
    )
    w.downstream.on_collaboration_changed.side_effect = RuntimeError(
        "downstream unavailable"
    )
    if auto:
        result = w.service.create_bot_editor_request(
            bot_id="journey-bot",
            owner_id="owner-1",
            applicant_user_id="applicant-1",
            reason="edit",
        )
        with w.db.orm_session() as session:
            order_id = session.query(WorkOrderModel).one().id
    else:
        result = w.service.create_bot_editor_request(
            bot_id="journey-bot",
            owner_id="owner-1",
            applicant_user_id="applicant-1",
            reason="edit",
        )
        order_id = result.id
        result = review(w, order_id)
    state = snapshot(w, order_id)
    assert state["status"] == "APPROVED"
    assert state["collaborators"] == 1
    assert state["notices"][-1] == ("NOTICE", "applicant-1")

    logs = [
        r
        for r in caplog.records
        if getattr(r, "phase", "") == "bot_post_commit_sync"
        and getattr(r, "outcome", "") == "failed"
    ]
    assert len(logs) == 1
    assert logs[0].work_order_id == order_id
    assert logs[0].local_committed is True
    assert logs[0].exception_type == "RuntimeError"
    assert "downstream unavailable" not in caplog.text


@pytest.mark.parametrize("kind", ["skill", "bot"])
@pytest.mark.parametrize("auto", [False, True])
def test_local_business_and_notice_roll_back_together(world, kind, auto):
    w = world
    if kind == "skill":
        w.entry.update_approval_policy(
            space_id=w.space_id,
            skill_id=w.skill_id,
            actor_id="owner-1",
            auto_approve_editor_requests=auto,
        )

        def create():
            return w.entry.create_request(
                space_id=w.space_id,
                skill_id=w.skill_id,
                applicant_user_id="applicant-1",
                reason="edit",
            ).work_order_id
    else:
        w.service.update_bot_editor_request_policy(
            bot_id="journey-bot",
            owner_id="owner-1",
            actor_id="owner-1",
            auto_approve=auto,
        )

        def create():
            return w.service.create_bot_editor_request(
                bot_id="journey-bot",
                owner_id="owner-1",
                applicant_user_id="applicant-1",
                reason="edit",
            ).id

    if not auto:
        order_id = create()
    event.listen(Session, "before_flush", reject_result_notice)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            if auto:
                create()
            else:
                review(w, order_id)
    finally:
        event.remove(Session, "before_flush", reject_result_notice)
    with w.db.orm_session() as session:
        assert session.query(SkillGrant).filter_by(user_id="applicant-1").count() == 0
        assert (
            session.query(BotCollaboratorModel).filter_by(user_id="applicant-1").count()
            == 0
        )
        orders = session.query(WorkOrderModel).all()
        # Dedicated Bot AUTO is one transaction; Skill AUTO creation precedes completion.
        if auto and kind == "bot":
            assert orders == []
        else:
            assert len(orders) == 1
            state = snapshot(w, orders[0].id)
            assert state["status"] == ("PROCESSING" if auto else "PENDING")
            assert not any(category == "NOTICE" for category, _ in state["notices"])
    w.downstream.on_collaboration_changed.assert_not_called()


def test_generic_bot_auto_uses_same_post_commit_failure_semantics(world, caplog):
    caplog.set_level("INFO", logger="start")
    w = world
    w.service.update_bot_editor_request_policy(
        bot_id="journey-bot", owner_id="owner-1", actor_id="owner-1", auto_approve=True
    )
    w.downstream.on_collaboration_changed.side_effect = RuntimeError(
        "secret downstream payload"
    )
    result = w.service.create_work_order_event(
        event_category=NotificationCategory.APPROVAL,
        approval_mode=WorkOrderApprovalMode.AUTO,
        biz_type=WorkOrderBizType.BOT_COLLABORATOR.value,
        biz_id="journey-bot",
        event_type=WorkOrderEventType.BOT_COLLABORATOR_APPLIED.value,
        applicant_user_id="applicant-1",
        approver_user_ids=["applicant-1"],
        recipient_user_ids=["ignored-user"],
        title="Bot edit",
        content=None,
        apply_reason="edit",
        actor_id="applicant-1",
        biz_data={
            "bot_pk": w.bot_pk,
            "bot_id": "journey-bot",
            "owner_id": "owner-1",
            "space_id": w.space_id,
        },
    )
    state = snapshot(w, result.work_order_id)
    assert result.status.value == state["status"] == "APPROVED"
    assert state["collaborators"] == 1
    assert state["notices"] == [("NOTICE", "applicant-1")]
    assert w.downstream.on_collaboration_changed.call_count == 1
    assert "secret downstream payload" not in caplog.text
    assert any(
        getattr(r, "phase", "") == "bot_post_commit_sync"
        and getattr(r, "outcome", "") == "failed"
        for r in caplog.records
    )


def test_auto_friend_stage_logs_and_failure_notice_do_not_claim_remote_rejection(
    world, caplog
):
    caplog.set_level("INFO", logger="start")
    w = world
    w.http.post.side_effect = TimeoutError("sensitive response body")
    result = friend(w, True)
    with w.db.orm_session() as session:
        order = session.get(WorkOrderModel, result.work_order_id)
        notice = (
            session.query(WorkOrderNotificationModel)
            .filter_by(work_order_id=order.id)
            .one()
        )
        assert "external outcome may require reconciliation" in order.review_remark
        assert "external outcome may require reconciliation" in notice.content
    phases = [
        (getattr(r, "phase", None), getattr(r, "outcome", None)) for r in caplog.records
    ]
    assert ("event_create", "completed") in phases
    assert ("auto_callback", "failed") in phases
    assert ("auto_failure_record", "completed") in phases
    assert "sensitive response body" not in caplog.text


def test_auto_friend_success_stages_are_ordered(world, caplog):
    caplog.set_level("INFO", logger="start")
    result = friend(world, True)
    phases = [
        (getattr(r, "phase", None), getattr(r, "outcome", None)) for r in caplog.records
    ]
    assert phases.index(("event_create", "completed")) < phases.index(
        ("auto_callback", "completed")
    )
    assert phases.index(("auto_callback", "completed")) < phases.index(
        ("auto_finalize", "completed")
    )
    records = [r for r in caplog.records if getattr(r, "phase", "") == "auto_finalize"]
    assert all(r.work_order_id == result.work_order_id for r in records)
    assert all(r.env == "dev" for r in records)
