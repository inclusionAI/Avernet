"""Contract tests for work-order creation and policy OpenAPI handlers."""

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.work_orders.router import router
from agentclaw.community.api.work_order_service import (
    WorkOrderNotificationServiceProtocol,
    WorkOrderServiceProtocol,
)
from agentclaw.community.core.work_orders.callbacks import WorkOrderCallbackCredential
from agentclaw.community.core.work_orders.errors import (
    WorkOrderCallbackError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApprovalMode,
    WorkOrderBizType,
    WorkOrderDecision,
    WorkOrderEventCreatedResult,
    WorkOrderEventStatus,
    WorkOrderEventType,
    WorkOrderNotificationRecord,
    WorkOrderRecord,
    WorkOrderReviewResult,
    WorkOrderStatus,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)

CREATED = datetime(2026, 8, 18, 1, 2, 3)
MODIFIED = datetime(2026, 8, 18, 2, 3, 4)
NOTIFICATION_MODIFIED = datetime(2026, 8, 18, 3, 4, 5)


def _work_order(
    status: WorkOrderStatus = WorkOrderStatus.PENDING,
    biz_data: str | None = None,
) -> WorkOrderRecord:
    return WorkOrderRecord(
        id=11,
        work_order_no="WO-11",
        biz_type=WorkOrderBizType.SPACE_JOIN,
        biz_id="7",
        biz_data=biz_data,
        applicant_user_id="applicant-1",
        apply_reason="join",
        status=status,
        reviewer_user_id=None,
        review_remark=None,
        reviewed_at=None,
        env="dev",
        gmt_created=CREATED,
        gmt_modified=MODIFIED,
    )


def _notification(
    category: NotificationCategory = NotificationCategory.NOTICE,
) -> WorkOrderNotificationRecord:
    return WorkOrderNotificationRecord(
        id=21,
        work_order_id=11,
        recipient_user_id="owner-1",
        notification_category=category,
        event_type=WorkOrderEventType.SPACE_JOIN_REVIEWED,
        biz_type=WorkOrderBizType.SPACE_JOIN,
        biz_id="7",
        title="SPACE_JOIN_APPROVED",
        content=json.dumps({"message": "approved"}),
        is_read=False,
        read_at=None,
        env="dev",
        gmt_created=CREATED,
        gmt_modified=NOTIFICATION_MODIFIED,
    )


@pytest.fixture
def work_order_service():
    return MagicMock()


@pytest.fixture
def notification_service():
    return MagicMock()


@pytest.fixture
def client(work_order_service, notification_service):
    class _Bindings(Module):
        def configure(self, binder):
            binder.bind(WorkOrderServiceProtocol, to=work_order_service)
            binder.bind(WorkOrderNotificationServiceProtocol, to=notification_service)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "owner-1"}
    attach_injector(app, Injector([_Bindings()]))
    mount_public_error_handlers(app)
    return user_scoped_client(app, "owner-1")


@pytest.mark.parametrize("enabled", [False, True])
def test_editor_policy_get_and_patch(client, work_order_service, enabled):
    work_order_service.get_bot_editor_request_policy.return_value = enabled
    work_order_service.update_bot_editor_request_policy.return_value = enabled
    path = "/openapi/v1/bots/bot-1/editor-request-policy?entity_id=target-owner"
    assert client.get(path).json()["data"] == {"auto_approve": enabled}
    work_order_service.get_bot_editor_request_policy.assert_called_once_with(
        bot_id="bot-1",
        owner_id="target-owner",
        actor_id="owner-1",
    )
    response = client.patch(path, json={"auto_approve": enabled})
    assert response.status_code == 200
    assert response.json()["data"] == {"auto_approve": enabled}
    work_order_service.update_bot_editor_request_policy.assert_called_once_with(
        bot_id="bot-1",
        owner_id="target-owner",
        actor_id="owner-1",
        auto_approve=enabled,
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"auto_approve": "true"},
        {"auto_approve": 1},
        {"auto_approve": None},
        {"auto_approve": True, "role": "admin"},
    ],
)
def test_editor_policy_strict_input(client, work_order_service, body):
    response = client.patch("/openapi/v1/bots/bot-1/editor-request-policy", json=body)
    assert response.status_code == 422
    work_order_service.update_bot_editor_request_policy.assert_not_called()


@pytest.mark.parametrize("method", ["get", "patch"])
def test_editor_policy_refuses_app_without_delegation(
    client, work_order_service, method
):
    from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller
    from agentclaw.community.adapters.http.openapi_v1.principal import (
        require_acting_caller,
    )

    client.app.dependency_overrides[require_acting_caller] = lambda: ActingCaller(
        user_id="owner-1", app_id=123
    )
    kwargs = {"json": {"auto_approve": True}} if method == "patch" else {}
    response = getattr(client, method)(
        "/openapi/v1/bots/bot-1/editor-request-policy", **kwargs
    )
    assert response.status_code == 404
    work_order_service.get_bot_editor_request_policy.assert_not_called()
    work_order_service.update_bot_editor_request_policy.assert_not_called()


def test_editor_policy_rejects_forged_actor_query(client, work_order_service):
    work_order_service.update_bot_editor_request_policy.return_value = True
    response = client.patch(
        "/openapi/v1/bots/bot-1/editor-request-policy?user_id=forged&entity_id=target",
        json={"auto_approve": True},
    )
    assert response.status_code == 403
    work_order_service.update_bot_editor_request_policy.assert_not_called()


def test_editor_policy_owner_denial_is_mapped(client, work_order_service):
    from agentclaw.community.core.work_orders.errors import WorkOrderAccessDeniedError

    work_order_service.update_bot_editor_request_policy.side_effect = (
        WorkOrderAccessDeniedError("private")
    )
    response = client.patch(
        "/openapi/v1/bots/bot-1/editor-request-policy", json={"auto_approve": True}
    )
    assert response.status_code == 403
    assert "private" not in response.text


def test_editor_policy_rejects_conflicting_owner_aliases(client, work_order_service):
    response = client.patch(
        "/openapi/v1/bots/bot-1/editor-request-policy?entity_id=a&owner_id=b",
        json={"auto_approve": True},
    )
    assert response.status_code == 422
    work_order_service.update_bot_editor_request_policy.assert_not_called()


def test_editor_application_returns_auto_approved_status(client, work_order_service):
    work_order_service.create_bot_editor_request.return_value = _work_order(
        status=WorkOrderStatus.APPROVED
    )
    response = client.post(
        "/openapi/v1/bots/bot-1/editor-requests?entity_id=other-owner",
        json={"reason": "edit"},
    )
    assert response.status_code == 201
    assert response.json()["data"]["status"] == "APPROVED"


def test_process_approval_forwards_only_callback_identity_headers(
    client, work_order_service
):
    work_order_service.process_approval.return_value = WorkOrderReviewResult(
        work_order_id=11,
        status=WorkOrderStatus.APPROVED,
        decision=WorkOrderDecision.APPROVED,
        reviewer_user_id="owner-1",
        review_remark=None,
        reviewed_at=MODIFIED,
    )

    response = client.post(
        "/openapi/v1/bots/work-orders/11/approval",
        json={"decision": "APPROVED"},
        headers={
            "Authorization": "Bearer token",
            "Cookie": "backend_session=must-not-forward",
            "X-Request-Id": "request-1",
            "X-Trace-Id": "trace-1",
            "X-Avernet-Principal": "principal-token",
        },
    )

    assert response.status_code == 200
    kwargs = work_order_service.process_approval.call_args.kwargs
    assert kwargs["work_order_id"] == 11
    assert kwargs["actor_id"] == "owner-1"
    assert kwargs["decision"] is WorkOrderDecision.APPROVED
    assert kwargs["review_remark"] is None
    credential = kwargs["callback_credential"]
    assert isinstance(credential, WorkOrderCallbackCredential)
    lowered = {key.lower(): value for key, value in credential.headers.items()}
    assert lowered == {
        "authorization": "Bearer token",
        "x-avernet-principal": "principal-token",
        "x-request-id": "request-1",
        "x-trace-id": "trace-1",
    }


def test_process_approval_exposes_callback_failure_without_local_success(
    client, work_order_service
):
    work_order_service.process_approval.side_effect = WorkOrderCallbackError(
        "private upstream details"
    )

    response = client.post(
        "/openapi/v1/bots/work-orders/11/approval",
        json={"decision": "APPROVED"},
    )

    assert response.status_code == 502
    assert response.json()["code"] == 502201
    assert response.json()["message"] == "Upstream work-order callback failed"


def test_create_join_request_uses_principal_and_returns_created(
    client, work_order_service
):
    work_order_service.create_space_join_request.return_value = _work_order()

    response = client.post(
        "/openapi/v1/bots/spaces/7/join-requests",
        params={"user_id": None},
        json={"reason": "join"},
    )

    assert response.status_code == 201
    assert response.json()["data"] == {
        "work_order_id": 11,
        "work_order_no": "WO-11",
        "status": "PENDING",
    }
    work_order_service.create_space_join_request.assert_called_once_with(
        space_id=7, applicant_user_id="owner-1", reason="join"
    )


def test_create_join_request_ignores_forged_legacy_identity_query(
    client, work_order_service
):
    work_order_service.create_space_join_request.return_value = _work_order()

    response = client.post(
        "/openapi/v1/bots/spaces/7/join-requests",
        params={"user_id": "someone-else", "user_name": "forged"},
        json={"reason": "join"},
    )

    assert response.status_code == 201
    work_order_service.create_space_join_request.assert_called_once_with(
        space_id=7,
        applicant_user_id="owner-1",
        reason="join",
    )


@pytest.mark.parametrize(
    "payload", [{}, {"reason": None}, {"reason": ""}, {"reason": "   "}]
)
def test_create_join_request_accepts_optional_reason(
    client, work_order_service, payload
):
    work_order_service.create_space_join_request.return_value = _work_order()

    response = client.post("/openapi/v1/bots/spaces/7/join-requests", json=payload)

    assert response.status_code == 201
    assert response.json()["data"]["status"] == "PENDING"
    work_order_service.create_space_join_request.assert_called_once_with(
        space_id=7, applicant_user_id="owner-1", reason=payload.get("reason")
    )


def test_create_join_request_rejects_reason_over_512_characters(client):
    response = client.post(
        "/openapi/v1/bots/spaces/7/join-requests",
        json={"reason": "x" * 513},
    )

    assert response.status_code == 422


def test_create_bot_editor_request_uses_principal_and_named_owner(
    client, work_order_service
):
    record = _work_order()
    record.biz_type = WorkOrderBizType.BOT_COLLABORATOR
    record.biz_id = "bot-7"
    work_order_service.create_bot_editor_request.return_value = record

    response = client.post(
        "/openapi/v1/bots/bot-7/editor-requests",
        params={"owner_id": "bot-owner"},
        json={"reason": "joint editing"},
    )

    assert response.status_code == 201
    assert response.json()["data"] == {
        "work_order_id": 11,
        "work_order_no": "WO-11",
        "status": "PENDING",
    }
    work_order_service.create_bot_editor_request.assert_called_once_with(
        bot_id="bot-7",
        owner_id="bot-owner",
        applicant_user_id="owner-1",
        reason="joint editing",
    )


def test_create_bot_editor_request_takes_the_owner_as_entity_id(
    client, work_order_service
):
    record = _work_order()
    record.biz_type = WorkOrderBizType.BOT_COLLABORATOR
    record.biz_id = "bot-7"
    work_order_service.create_bot_editor_request.return_value = record

    response = client.post(
        "/openapi/v1/bots/bot-7/editor-requests",
        params={"entity_id": "bot-owner"},
        json={"reason": "joint editing"},
    )

    assert response.status_code == 201
    work_order_service.create_bot_editor_request.assert_called_once_with(
        bot_id="bot-7",
        owner_id="bot-owner",
        applicant_user_id="owner-1",
        reason="joint editing",
    )


def test_create_bot_editor_request_treats_an_empty_entity_id_as_absent(
    client, work_order_service
):
    """``?entity_id=&owner_id=x`` names one owner, not two disagreeing ones."""
    record = _work_order()
    record.biz_type = WorkOrderBizType.BOT_COLLABORATOR
    record.biz_id = "bot-7"
    work_order_service.create_bot_editor_request.return_value = record

    response = client.post(
        "/openapi/v1/bots/bot-7/editor-requests",
        params={"entity_id": "", "owner_id": "bot-owner"},
        json={"reason": "joint editing"},
    )

    assert response.status_code == 201
    work_order_service.create_bot_editor_request.assert_called_once_with(
        bot_id="bot-7",
        owner_id="bot-owner",
        applicant_user_id="owner-1",
        reason="joint editing",
    )


def test_create_bot_editor_request_refuses_disagreeing_owner_spellings(
    client, work_order_service
):
    response = client.post(
        "/openapi/v1/bots/bot-7/editor-requests",
        params={"entity_id": "bot-owner", "owner_id": "someone-else"},
        json={"reason": "joint editing"},
    )

    assert response.status_code == 422
    work_order_service.create_bot_editor_request.assert_not_called()


def test_create_work_order_event_accepts_json_objects(
    client, work_order_service, caplog
):
    caplog.set_level("INFO", logger="start")
    work_order_service.create_work_order_event.return_value = (
        WorkOrderEventCreatedResult(
            event_category=NotificationCategory.APPROVAL,
            work_order_id=11,
            work_order_no="WO-11",
            notification_ids=[21],
            status=WorkOrderEventStatus.PENDING,
        )
    )
    payload = {
        "event_category": "APPROVAL",
        "biz_type": "SPACE_JOIN",
        "biz_id": "7",
        "event_type": "SPACE_JOIN_APPLIED",
        "applicant_user_id": "owner-1",
        "approver_user_ids": ["approver-1"],
        "recipient_user_ids": [],
        "title": "ignored display title",
        "content": {"text": "apply", "items": [1, True]},
        "biz_data": {"space_id": 7, "meta": {"source": "web"}},
    }

    response = client.post("/openapi/v1/bots/work-orders/events", json=payload)

    assert response.status_code == 201
    event_log = next(
        record
        for record in caplog.records
        if record.message == "work-order event received"
    )
    assert event_log.work_order_event == {**payload, "apply_reason": None}
    work_order_service.create_work_order_event.assert_called_once_with(
        event_category=NotificationCategory.APPROVAL,
        biz_type="SPACE_JOIN",
        biz_id="7",
        event_type="SPACE_JOIN_APPLIED",
        applicant_user_id="owner-1",
        approver_user_ids=["approver-1"],
        recipient_user_ids=[],
        title="ignored display title",
        content={"text": "apply", "items": [1, True]},
        apply_reason=None,
        biz_data={"space_id": 7, "meta": {"source": "web"}},
        actor_id="owner-1",
    )


def test_public_work_order_event_accepts_auto(client, work_order_service):
    payload = {
        "event_category": "APPROVAL",
        "approval_mode": "AUTO",
        "biz_type": "BOT_FRIEND",
        "biz_id": "friend-1",
        "event_type": "HUMAN2BOT_FRIEND_APPLIED",
        "applicant_user_id": "owner-1",
        "approver_user_ids": [],
        "recipient_user_ids": ["owner-1"],
        "title": "Friend request",
        "biz_data": {"request_ids": ["request-1"]},
    }

    work_order_service.create_work_order_event.return_value = (
        WorkOrderEventCreatedResult(
            event_category=NotificationCategory.APPROVAL,
            work_order_id=11,
            work_order_no="WO-11",
            notification_ids=[21],
            status=WorkOrderEventStatus.APPROVED,
        )
    )
    response = client.post(
        "/openapi/v1/bots/work-orders/events",
        json=payload,
        headers={"X-Request-Id": "request-1"},
    )

    assert response.status_code == 201
    assert response.json()["data"]["status"] == "APPROVED"
    kwargs = work_order_service.create_work_order_event.call_args.kwargs
    assert kwargs["approval_mode"] is WorkOrderApprovalMode.AUTO
    assert kwargs["recipient_user_ids"] == ["owner-1"]
    assert kwargs["actor_id"] == "owner-1"


def test_public_auto_space_join_delegates_business_eligibility(
    client, work_order_service
):
    work_order_service.create_work_order_event.return_value = (
        WorkOrderEventCreatedResult(
            event_category=NotificationCategory.APPROVAL,
            work_order_id=11,
            work_order_no="WO-11",
            notification_ids=[21],
            status=WorkOrderEventStatus.APPROVED,
        )
    )
    response = client.post(
        "/openapi/v1/bots/work-orders/events",
        json={
            "event_category": "APPROVAL",
            "approval_mode": "AUTO",
            "biz_type": "SPACE_JOIN",
            "biz_id": "7",
            "event_type": "SPACE_JOIN_APPLIED",
            "applicant_user_id": "owner-1",
            "approver_user_ids": [],
            "recipient_user_ids": ["owner-1"],
            "title": "join",
        },
    )

    assert response.status_code == 201
    assert response.json()["data"]["status"] == "APPROVED"
    kwargs = work_order_service.create_work_order_event.call_args.kwargs
    assert kwargs["approval_mode"] is WorkOrderApprovalMode.AUTO
    assert kwargs["recipient_user_ids"] == ["owner-1"]
    assert kwargs["actor_id"] == "owner-1"


@pytest.mark.parametrize(
    ("biz_type", "event_type"),
    [
        ("SKILL_COLLABORATOR", "SKILL_COLLABORATOR_APPLIED"),
        ("SKILL_COLLABORATOR", "SPACE_JOIN_APPLIED"),
        ("SPACE_JOIN", "SKILL_COLLABORATOR_APPLIED"),
        (" SKILL_COLLABORATOR ", "SPACE_JOIN_APPLIED"),
        ("SPACE_JOIN", "\tSKILL_COLLABORATOR_APPLIED\n"),
    ],
)
@pytest.mark.parametrize("approval_mode", ["MANUAL", "AUTO"])
def test_public_generic_events_reject_skill_approval_before_service_call(
    client, work_order_service, biz_type, event_type, approval_mode
):
    payload = {
        "event_category": "APPROVAL",
        "approval_mode": approval_mode,
        "biz_type": biz_type,
        "biz_id": "91",
        "event_type": event_type,
        "applicant_user_id": "owner-1",
        "approver_user_ids": ["real-skill-owner"],
        "recipient_user_ids": [],
        "title": "Skill editor request",
        "biz_data": {"space_id": 7, "skill_id": 91},
    }

    response = client.post("/openapi/v1/bots/work-orders/events", json=payload)

    assert response.status_code == 400
    assert response.json()["code"] == 400201
    work_order_service.create_work_order_event.assert_not_called()


@pytest.mark.parametrize(
    "content",
    [
        None,
        {},
        {"text": "display"},
        {"text": "display", "legacy_value": "legacy"},
        {"message": "成员已加入空间", "space_id": 7},
        {"display_content": "...", "metadata": {"source": "bcs"}},
        {"review_remark": "审批备注"},
        {"workitem_name": "历史任务"},
    ],
)
def test_create_work_order_event_accepts_supported_content(
    content, client, work_order_service
):
    work_order_service.create_work_order_event.return_value = (
        WorkOrderEventCreatedResult(
            event_category=NotificationCategory.NOTICE,
            work_order_id=None,
            work_order_no=None,
            notification_ids=[21],
            status=WorkOrderEventStatus.CREATED,
        )
    )
    payload = {
        "event_category": "NOTICE",
        "biz_type": "SPACE",
        "biz_id": "space-1",
        "event_type": "SPACE_MEMBER_ADDED",
        "recipient_user_ids": ["owner-1"],
        "title": "Member added",
        "content": content,
    }

    response = client.post("/openapi/v1/bots/work-orders/events", json=payload)

    assert response.status_code == 201
    assert (
        work_order_service.create_work_order_event.call_args.kwargs["content"]
        == content
    )


@pytest.mark.parametrize(
    "content",
    [
        "普通字符串",
        [],
        123,
        True,
    ],
)
def test_create_work_order_event_rejects_invalid_content(
    content, client, work_order_service
):
    payload = {
        "event_category": "NOTICE",
        "biz_type": "SPACE",
        "biz_id": "space-1",
        "event_type": "SPACE_MEMBER_ADDED",
        "recipient_user_ids": ["owner-1"],
        "title": "Member added",
        "content": content,
    }

    response = client.post("/openapi/v1/bots/work-orders/events", json=payload)

    assert response.status_code == 422
    assert "Invalid request" in response.text
    work_order_service.create_work_order_event.assert_not_called()


def test_auto_event_requires_user_delegation(client, work_order_service):
    from agentclaw.community.adapters.http.openapi_v1.principal import (
        require_acting_caller,
    )
    from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller

    client.app.dependency_overrides[require_acting_caller] = lambda: ActingCaller(
        user_id="owner-1", app_id=123
    )
    response = client.post(
        "/openapi/v1/bots/work-orders/events",
        json={
            "event_category": "APPROVAL",
            "approval_mode": "AUTO",
            "biz_type": "SPACE_JOIN",
            "biz_id": "7",
            "event_type": "SPACE_JOIN_APPLIED",
            "approver_user_ids": [],
            "recipient_user_ids": ["owner-1"],
            "title": "join",
        },
    )
    assert response.status_code == 404
    work_order_service.create_work_order_event.assert_not_called()
