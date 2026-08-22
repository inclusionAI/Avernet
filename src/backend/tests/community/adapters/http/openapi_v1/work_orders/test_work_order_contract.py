"""Contract tests for work-order and notification OpenAPI handlers."""

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
from agentclaw.community.core.work_orders.errors import WorkOrderNotFoundError
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderBizType,
    WorkOrderDetail,
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
        title="reviewed",
        content="approved",
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


def test_create_join_request_uses_principal_and_returns_created(
    client, work_order_service
):
    work_order_service.create_space_join_request.return_value = _work_order()

    response = client.post(
        "/openapi/v1/bots/spaces/7/join-requests", json={"reason": "join"}
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


def _space_join_display_data() -> str:
    return json.dumps(
        {
            "display_title": {
                "PENDING": "空间加入申请待审批",
                "APPROVED": "空间加入申请已通过",
                "REJECTED": "空间加入申请未通过",
            },
            "display_content": {
                "PENDING": "空间加入申请正在等待审批。",
                "APPROVED": "空间加入申请已通过。",
                "REJECTED": "空间加入申请未通过。",
            },
        }
    )


def test_list_work_orders_maps_plain_and_notification_items(client, work_order_service):
    work_order_service.list_items.return_value = (
        2,
        [
            WorkOrderListItem(
                work_order=_work_order(biz_data=_space_join_display_data()),
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
    assert items[0]["content"] == "空间加入申请正在等待审批。"
    assert items[0]["gmt_modified"] == "2026-08-18T02:03:04Z"
    assert items[1]["item_id"] == "NOTIFICATION_21"
    assert items[1]["item_type"] == "NOTICE"
    assert items[1]["notification_id"] == 21
    assert items[1]["gmt_modified"] == "2026-08-18T03:04:05Z"
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
    ("status", "title", "content"),
    [
        (
            WorkOrderStatus.PENDING,
            "空间加入申请待审批",
            "空间加入申请正在等待审批。",
        ),
        (
            WorkOrderStatus.APPROVED,
            "空间加入申请已通过",
            "空间加入申请已通过。",
        ),
        (
            WorkOrderStatus.REJECTED,
            "空间加入申请未通过",
            "空间加入申请未通过。",
        ),
    ],
)
def test_list_work_orders_provides_copy_for_approval_without_notification(
    client, work_order_service, status, title, content
):
    work_order_service.list_items.return_value = (
        1,
        [
            WorkOrderListItem(
                work_order=_work_order(status, biz_data=_space_join_display_data()),
                notification=None,
                can_approve=False,
            )
        ],
    )

    response = client.get("/openapi/v1/bots/work-orders")

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["item_type"] == "APPROVAL"
    assert item["notification_id"] is None
    assert item["notification_category"] is None
    assert item["title"] == title
    assert item["content"] == content


def test_list_work_orders_uses_business_copy_without_shared_biz_type_enum(
    client, work_order_service
):
    order = _work_order(
        biz_data=json.dumps(
            {
                "display_title": {"PENDING": "机器人协作申请"},
                "display_content": {"PENDING": "请处理机器人协作申请。"},
            }
        )
    )
    order.biz_type = "BOT_COLLABORATOR"
    work_order_service.list_items.return_value = (
        1,
        [WorkOrderListItem(work_order=order, notification=None, can_approve=False)],
    )

    response = client.get("/openapi/v1/bots/work-orders")

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["biz_type"] == "BOT_COLLABORATOR"
    assert item["title"] == "机器人协作申请"
    assert item["content"] == "请处理机器人协作申请。"


def test_get_work_order_maps_nested_content(client, work_order_service):
    work_order_service.get_detail.return_value = WorkOrderDetail(
        work_order=_work_order(),
        event_type=WorkOrderEventType.SPACE_JOIN_APPLIED,
        title="pending",
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
    assert data["content"] == {
        "space_id": 7,
        "space_name": "Team",
        "applicant_user_id": "applicant-1",
        "applicant_name": "Applicant",
        "reason": "join",
    }
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
        space_id=0,
        space_name="",
        applicant_name="applicant-1",
        can_approve=True,
    )

    response = client.get("/openapi/v1/bots/work-orders/11")

    assert response.status_code == 200
    assert response.json()["data"]["content"] == {
        "bot_id": "bot-7",
        "bot_name": "Data Bot",
        "owner_id": "owner-1",
        "space_id": 7,
        "applicant_user_id": "applicant-1",
        "applicant_name": "Applicant",
        "requested_role": "member",
        "reason": "join",
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
        "reviewed_at": "2026-08-18T02:03:04Z",
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
        "title": "reviewed",
        "content": "approved",
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
        "read_at": "2026-08-18T02:03:04Z",
    }
    notification_service.get_detail.assert_called_once_with(
        notification_id=21, actor_id="owner-1"
    )
    notification_service.mark_read.assert_called_once_with(
        notification_id=21, actor_id="owner-1"
    )


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
        ("/openapi/v1/bots/spaces/7/join-requests", "post", {"reason": ""}),
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
