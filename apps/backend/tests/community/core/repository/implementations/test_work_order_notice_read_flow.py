"""Real HTTP -> services -> file SQLite notice inbox/read/badge regression."""

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.work_orders.router import router
from agentclaw.community.api.work_order_service import (
    WorkOrderNotificationServiceProtocol,
    WorkOrderServiceProtocol,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderModel,
    WorkOrderNotificationModel,
)
from agentclaw.community.core.work_orders.services.notification_service import (
    WorkOrderNotificationService,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)
from tests.community.core.repository.implementations.test_work_order_sqlite_journeys import (
    friend,
    world as sqlite_world,
)

world = sqlite_world
BASE = "/openapi/v1/bots"


@pytest.fixture
def client(world):
    app = FastAPI()
    injector = Injector()
    injector.binder.bind(WorkOrderServiceProtocol, to=world.service)
    injector.binder.bind(
        WorkOrderNotificationServiceProtocol,
        to=WorkOrderNotificationService(world.repo),
    )
    attach_injector(app, injector)
    app.include_router(router)
    mount_public_error_handlers(app)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "owner-1"}
    with user_scoped_client(app, "owner-1") as http:
        yield http


def data(response):
    assert response.status_code == 200, response.text
    return response.json()["data"]


def inbox(client, query, category="NOTICE", **extra):
    return data(
        client.get(
            f"{BASE}/work-orders",
            params={
                "query_type": query,
                "item_type": category,
                **extra,
            },
        )
    )


def badge(client):
    return data(client.get(f"{BASE}/work-order-notifications/unread-count"))


@pytest.mark.parametrize(
    "status", [None, "PENDING", "PROCESSING", "APPROVED", "REJECTED", "FAILED"]
)
@pytest.mark.parametrize("read_action", ["detail", "read", "read-all"])
def test_notice_classification_depends_only_on_read_state(
    world, client, status, read_action
):
    order_id = friend(world, True).work_order_id
    with world.db.orm_session() as session:
        order = session.get(WorkOrderModel, order_id)
        notice = (
            session.query(WorkOrderNotificationModel)
            .filter_by(work_order_id=order_id)
            .one()
        )
        notice_id = notice.id
        if status is None:
            notice.work_order_id = None
        else:
            order.status = status
    pending = inbox(client, "PENDING_FOR_ME")
    assert pending["total"] == 1
    assert pending["items"][0]["notification_id"] == notice_id
    assert pending["items"][0]["is_read"] is False
    assert inbox(client, "PROCESSED_BY_ME")["total"] == 0
    assert inbox(client, "PENDING_FOR_ME", "ALL")["total"] == 1
    assert badge(client)["badge_count"] == 1
    # Listing and counting must never mark a notification read.
    assert badge(client)["unread_notice_count"] == 1
    path = f"{BASE}/work-order-notifications"
    if read_action == "detail":
        assert data(client.get(f"{path}/{notice_id}"))["is_read"] is True
    elif read_action == "read":
        assert data(client.post(f"{path}/{notice_id}/read"))["is_read"] is True
    else:
        assert data(client.post(f"{path}/read-all"))["updated_count"] == 1
    assert inbox(client, "PENDING_FOR_ME")["total"] == 0
    processed = inbox(client, "PROCESSED_BY_ME")
    assert processed["total"] == 1
    assert processed["items"][0]["is_read"] is True
    assert badge(client)["badge_count"] == 0
    # Repeated reads are harmless; they do not mutate the order decision.
    assert data(client.get(f"{path}/{notice_id}"))["is_read"] is True
    with world.db.orm_session() as session:
        assert session.get(WorkOrderModel, order_id).status == (status or "APPROVED")
    assert world.path.is_file()


def test_read_all_preserves_pending_approval_and_isolates_recipients_and_env(
    world, client
):
    friend(world, False)
    with world.db.orm_session() as session:
        for recipient, env in [
            ("owner-1", "dev"),
            ("other", "dev"),
            ("owner-1", "pre"),
        ]:
            session.add(
                WorkOrderNotificationModel(
                    recipient_user_id=recipient,
                    notification_category="NOTICE",
                    event_type="TASK_DISCOVERED",
                    biz_type="TASK",
                    biz_id="task-1",
                    title="notice",
                    content="{}",
                    env=env,
                )
            )
    assert badge(client)["badge_count"] == 2
    assert inbox(client, "PENDING_FOR_ME", "ALL")["total"] == 2
    assert (
        inbox(client, "PENDING_FOR_ME", "NOTICE", page_no=2, page_size=1)["items"] == []
    )
    assert (
        data(client.post(f"{BASE}/work-order-notifications/read-all"))["updated_count"]
        == 2
    )
    counts = badge(client)
    assert counts["pending_approval_count"] == counts["badge_count"] == 1
    assert counts["unread_notice_count"] == 0
    remaining = inbox(client, "PENDING_FOR_ME", "ALL")
    assert remaining["total"] == 1 and remaining["items"][0]["can_approve"] is True
    with world.db.orm_session() as session:
        outsiders = (
            session.query(WorkOrderNotificationModel)
            .filter(
                (WorkOrderNotificationModel.recipient_user_id == "other")
                | (WorkOrderNotificationModel.env == "pre")
            )
            .all()
        )
        assert len(outsiders) == 2 and all(not n.is_read for n in outsiders)
        outsider_ids = [n.id for n in outsiders]
    for notice_id in outsider_ids:
        assert (
            client.get(f"{BASE}/work-order-notifications/{notice_id}").status_code
            == 404
        )
    assert inbox(client, "PROCESSED_BY_ME")["total"] == 1
