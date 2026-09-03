"""Contract tests for work-order and notification OpenAPI handlers."""

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.work_orders.converter import (
    display_title,
    json_object,
)
from agentclaw.community.adapters.http.openapi_v1.work_orders.router import router
from agentclaw.community.api.work_order_service import (
    WorkOrderNotificationServiceProtocol,
    WorkOrderServiceProtocol,
)
from agentclaw.community.core.work_orders.callbacks import WorkOrderCallbackCredential
from agentclaw.community.core.work_orders.errors import (
    WorkOrderCallbackError,
    WorkOrderNotFoundError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderBizType,
    WorkOrderDecision,
    WorkOrderDetail,
    WorkOrderEventCreatedResult,
    WorkOrderEventStatus,
    WorkOrderEventType,
    WorkOrderListItem,
    WorkOrderNotificationDetail,
    WorkOrderNotificationBadgeSummary,
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
        "content": {"message": "apply", "items": [1, True]},
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
        content={"message": "apply", "items": [1, True]},
        apply_reason=None,
        biz_data={"space_id": 7, "meta": {"source": "web"}},
        actor_id="owner-1",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("content", [1, 2, 3]),
        ("content", "text"),
        ("content", 123),
        ("content", True),
        ("biz_data", [1, 2, 3]),
        ("biz_data", "text"),
        ("biz_data", 123),
        ("biz_data", True),
    ],
)
def test_create_work_order_event_rejects_non_object_json(
    client, work_order_service, field, value
):
    payload = {
        "event_category": "APPROVAL",
        "biz_type": "SPACE_JOIN",
        "biz_id": "7",
        "event_type": "SPACE_JOIN_APPLIED",
        "applicant_user_id": "owner-1",
        "approver_user_ids": ["approver-1"],
        "recipient_user_ids": [],
        "title": "title",
        field: value,
    }

    response = client.post("/openapi/v1/bots/work-orders/events", json=payload)

    assert response.status_code == 422
    work_order_service.create_work_order_event.assert_not_called()


def test_list_work_orders_allows_notice_without_work_order(client, work_order_service):
    work_order_service.list_items.return_value = (
        1,
        [
            WorkOrderListItem(
                work_order=None,
                notification=_notification().model_copy(update={"work_order_id": None}),
                can_approve=False,
            )
        ],
    )

    response = client.get("/openapi/v1/bots/work-orders")

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["item_type"] == "NOTICE"
    assert item["work_order_id"] is None
    assert item["work_order_no"] is None
    assert item["notification_id"] == 21


def test_list_work_orders_maps_plain_and_notification_items(client, work_order_service):
    work_order_service.list_items.return_value = (
        2,
        [
            WorkOrderListItem(
                work_order=_work_order(),
                notification=None,
                can_approve=True,
            ),
            WorkOrderListItem(
                work_order=_work_order(WorkOrderStatus.APPROVED),
                notification=_notification(),
                can_approve=False,
            ),
        ],
    )

    response = client.get(
        "/openapi/v1/bots/work-orders",
        params={
            "query_type": "INITIATED_BY_ME",
            "item_type": "ALL",
            "page_no": 2,
            "page_size": 5,
        },
    )

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert items[0]["item_id"] == "WORK_ORDER_11"
    assert items[0]["item_type"] == "APPROVAL"
    assert items[0]["notification_id"] is None
    assert items[0]["title"] == "空间加入申请待审批"
    assert items[0]["summary"] == "你有一条新的通知，请查看详情。"
    assert items[0]["content"] is None
    assert items[0]["gmt_created"] == "2026-08-18T01:02:03"
    assert items[0]["gmt_modified"] == "2026-08-18T02:03:04"
    assert items[1]["item_id"] == "NOTIFICATION_21"
    assert items[1]["item_type"] == "NOTICE"
    assert items[1]["notification_id"] == 21
    assert items[1]["title"] == "空间加入申请已处理"
    assert items[1]["summary"] == "空间加入申请已有处理结果，请查看详情。"
    assert items[1]["content"] == {"message": "approved"}
    assert items[1]["gmt_created"] == "2026-08-18T01:02:03"
    assert items[1]["gmt_modified"] == "2026-08-18T03:04:05"
    work_order_service.list_items.assert_called_once()
    assert work_order_service.list_items.call_args.kwargs == {
        "actor_id": "owner-1",
        "query_type": "INITIATED_BY_ME",
        "item_type": "ALL",
        "biz_type": None,
        "biz_id": None,
        "page_no": 2,
        "page_size": 5,
    }


@pytest.mark.parametrize(
    ("status", "title"),
    [
        (WorkOrderStatus.PENDING, "空间加入申请待审批"),
        (WorkOrderStatus.APPROVED, "空间加入申请已通过"),
        (WorkOrderStatus.REJECTED, "空间加入申请未通过"),
    ],
)
def test_list_work_orders_derives_space_title_without_notification(
    client, work_order_service, status, title
):
    work_order_service.list_items.return_value = (
        1,
        [
            WorkOrderListItem(
                work_order=_work_order(status),
                notification=None,
                can_approve=False,
            )
        ],
    )

    response = client.get("/openapi/v1/bots/work-orders")

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["title"] == title
    assert item["content"] is None


@pytest.mark.parametrize(
    ("event_type", "stored_title", "expected"),
    [
        (WorkOrderEventType.SPACE_JOIN_REVIEWED, None, "空间加入申请已处理"),
        (WorkOrderEventType.SKILL_COLLABORATOR_APPLIED, None, "Skill 共同编辑申请待审批"),
        (WorkOrderEventType.BOT2BOT_FRIEND_REVIEWED, "BOT_FRIEND APPROVED", "Bot 好友申请已处理"),
        ("EXTERNAL_EVENT", "custom title", "custom title"),
    ],
)
def test_list_work_orders_uses_event_type_display_contract(
    client, work_order_service, event_type, stored_title, expected
):
    notification = _notification().model_copy(
        update={"event_type": event_type, "title": stored_title}
    )
    work_order_service.list_items.return_value = (
        1,
        [
            WorkOrderListItem(
                work_order=_work_order(WorkOrderStatus.APPROVED),
                notification=notification,
                can_approve=False,
            )
        ],
    )

    response = client.get("/openapi/v1/bots/work-orders")

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["title"] == expected
    assert item["summary"]


def test_get_work_order_maps_nested_content(client, work_order_service):
    work_order_service.get_detail.return_value = WorkOrderDetail(
        work_order=_work_order(biz_data=json.dumps({"space_id": 7, "tags": ["a"]})),
        event_type=WorkOrderEventType.SPACE_JOIN_APPLIED,
        title="SPACE_JOIN_PENDING",
        content=json.dumps({"message": "pending", "meta": {"priority": 1}}),
        space_id=7,
        space_name="Team",
        applicant_name="Applicant",
        can_approve=True,
    )

    response = client.get("/openapi/v1/bots/work-orders/11")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["work_order_id"] == 11
    assert data["biz_id"] == 7
    assert data["title"] == "空间加入申请待审批"
    assert data["content"] == {"message": "pending", "meta": {"priority": 1}}
    assert data["biz_data"] == {"space_id": 7, "tags": ["a"]}
    assert data["can_approve"] is True
    work_order_service.get_detail.assert_called_once_with(
        work_order_id=11, actor_id="owner-1"
    )


def test_get_bot_editor_work_order_maps_business_content(client, work_order_service):
    record = _work_order(
        biz_data=json.dumps(
            {
                "bot_id": "bot-7",
                "bot_name": "Data Bot",
                "owner_id": "owner-1",
                "space_id": 7,
                "applicant_name": "Applicant",
                "requested_role": "member",
            }
        )
    )
    record.biz_type = WorkOrderBizType.BOT_COLLABORATOR
    record.biz_id = "bot-7"
    work_order_service.get_detail.return_value = WorkOrderDetail(
        work_order=record,
        event_type=WorkOrderEventType.BOT_COLLABORATOR_APPLIED,
        title="pending",
        content=json.dumps({"message": "raw bot content"}),
        space_id=0,
        space_name="",
        applicant_name="applicant-1",
        can_approve=True,
    )

    response = client.get("/openapi/v1/bots/work-orders/11")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["content"] == {"message": "raw bot content"}
    assert data["biz_data"] == {
        "bot_id": "bot-7",
        "bot_name": "Data Bot",
        "owner_id": "owner-1",
        "space_id": 7,
        "applicant_name": "Applicant",
        "requested_role": "member",
    }


@pytest.mark.parametrize(
    ("operation", "status"),
    [("approve", WorkOrderStatus.APPROVED), ("reject", WorkOrderStatus.REJECTED)],
)
def test_review_endpoints_return_explicit_result(
    client, work_order_service, operation, status
):
    getattr(work_order_service, operation).return_value = WorkOrderReviewResult(
        work_order_id=11,
        status=status,
        reviewer_user_id="owner-1",
        review_remark="done",
        reviewed_at=MODIFIED,
    )

    response = client.post(
        f"/openapi/v1/bots/work-orders/11/{operation}",
        json={"review_remark": "done"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "work_order_id": 11,
        "status": status.value,
        "reviewer_user_id": "owner-1",
        "review_remark": "done",
        "reviewed_at": "2026-08-18T02:03:04",
    }
    getattr(work_order_service, operation).assert_called_once_with(
        work_order_id=11, actor_id="owner-1", review_remark="done"
    )


@pytest.mark.parametrize(
    ("payload", "remark"), [({}, None), ({"review_remark": ""}, "")]
)
def test_approve_accepts_optional_remark(client, work_order_service, payload, remark):
    work_order_service.approve.return_value = WorkOrderReviewResult(
        work_order_id=11,
        status=WorkOrderStatus.APPROVED,
        reviewer_user_id="owner-1",
        review_remark=None,
        reviewed_at=MODIFIED,
    )

    response = client.post(
        "/openapi/v1/bots/work-orders/11/approve",
        json=payload,
    )

    assert response.status_code == 200
    assert response.json()["data"]["review_remark"] is None
    work_order_service.approve.assert_called_once_with(
        work_order_id=11, actor_id="owner-1", review_remark=remark
    )


def test_notification_count_and_read_all(client, notification_service):
    notification_service.badge_summary.return_value = WorkOrderNotificationBadgeSummary(
        unread_count=4,
        pending_approval_count=2,
        unread_notice_count=3,
        badge_count=5,
    )
    notification_service.mark_all_read.return_value = 3

    unread = client.get("/openapi/v1/bots/work-order-notifications/unread-count")
    read_all = client.post("/openapi/v1/bots/work-order-notifications/read-all")

    assert unread.status_code == 200
    assert unread.json()["data"] == {
        "unread_count": 4,
        "pending_approval_count": 2,
        "unread_notice_count": 3,
        "badge_count": 5,
    }
    assert read_all.status_code == 200
    assert read_all.json()["data"] == {"updated_count": 3}
    notification_service.badge_summary.assert_called_once_with(actor_id="owner-1")
    notification_service.mark_all_read.assert_called_once_with(actor_id="owner-1")


def test_notification_detail_and_mark_read(client, notification_service):
    notification_service.get_detail.return_value = WorkOrderNotificationDetail(
        notification=_notification(NotificationCategory.APPROVAL),
        work_order_status=WorkOrderStatus.PENDING,
        can_approve=True,
    )
    notification_service.mark_read.return_value = _notification().model_copy(
        update={"is_read": True, "read_at": MODIFIED}
    )

    detail = client.get("/openapi/v1/bots/work-order-notifications/21")
    marked = client.post("/openapi/v1/bots/work-order-notifications/21/read")

    assert detail.status_code == 200
    assert detail.json()["data"] == {
        "notification_id": 21,
        "work_order_id": 11,
        "notification_category": "APPROVAL",
        "event_type": "SPACE_JOIN_REVIEWED",
        "title": "空间加入申请已处理",
        "summary": "空间加入申请已有处理结果，请查看详情。",
        "content": {"message": "approved"},
        "is_read": False,
        "work_order_status": "PENDING",
        "can_approve": True,
        "biz_type": "SPACE_JOIN",
        "biz_id": "7",
    }
    assert marked.status_code == 200
    assert marked.json()["data"] == {
        "notification_id": 21,
        "is_read": True,
        "read_at": "2026-08-18T02:03:04",
    }
    notification_service.get_detail.assert_called_once_with(
        notification_id=21, actor_id="owner-1"
    )
    notification_service.mark_read.assert_called_once_with(
        notification_id=21, actor_id="owner-1"
    )


def test_notification_detail_wraps_historical_plain_text(client, notification_service):
    notification_service.get_detail.return_value = WorkOrderNotificationDetail(
        notification=_notification(NotificationCategory.NOTICE).model_copy(
            update={"title": "SPACE_JOIN APPROVED", "content": "approved"}
        ),
        work_order_status=WorkOrderStatus.APPROVED,
        can_approve=False,
    )

    response = client.get("/openapi/v1/bots/work-order-notifications/21")

    assert response.status_code == 200
    assert response.json()["data"]["title"] == "空间加入申请已处理"
    assert response.json()["data"]["summary"] == "空间加入申请已有处理结果，请查看详情。"
    assert response.json()["data"]["content"] == {"legacy_value": "approved"}


def test_domain_error_is_mapped_to_public_work_order_contract(
    client, work_order_service
):
    work_order_service.get_detail.side_effect = WorkOrderNotFoundError("private id")

    response = client.get("/openapi/v1/bots/work-orders/999")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == 404201
    assert body["message"] == "Not found"
    assert body["data"] is None
    assert "private id" not in body["message"]


@pytest.mark.parametrize(
    ("path", "method", "payload"),
    [
        ("/openapi/v1/bots/spaces/0/join-requests", "post", {"reason": "join"}),
        ("/openapi/v1/bots/spaces/7/join-requests", "post", {"reason": "x" * 513}),
        ("/openapi/v1/bots/work-orders?page_no=0", "get", None),
        ("/openapi/v1/bots/work-orders/0", "get", None),
    ],
)
def test_request_validation_rejects_invalid_contract(client, path, method, payload):
    response = (
        getattr(client, method)(path, json=payload)
        if payload is not None
        else getattr(client, method)(path)
    )
    assert response.status_code == 422


def test_presentation_helpers_preserve_empty_values() -> None:
    assert display_title(None, biz_type="CUSTOM", status=None) is None
    assert json_object(None) is None
