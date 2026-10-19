"""HTTP AUTO ingress through real services and file-backed SQLite persistence."""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.work_orders.router import (
    router as public_router,
)
from agentclaw.community.adapters.http.org.dependencies import require_user_caller
from agentclaw.community.adapters.http.work_orders.router import (
    router as internal_router,
)
from agentclaw.community.api.work_order_service import WorkOrderServiceProtocol
from agentclaw.community.core.gateway_principal import (
    GatewayUser,
    UserPrincipal,
    VerifiedCaller,
)
from agentclaw.community.core.work_orders.repository.models import WorkOrderModel
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)
from tests.community.core.repository.implementations.test_work_order_sqlite_journeys import (
    snapshot,
    world as sqlite_world,
)


world = sqlite_world


@pytest.fixture(params=["public", "internal"])
def http_entry(world, request):
    class Bindings(Module):
        def configure(self, binder):
            binder.bind(WorkOrderServiceProtocol, to=world.service)

    app = FastAPI()
    attach_injector(app, Injector([Bindings()]))
    mount_public_error_handlers(app)
    if request.param == "public":
        app.include_router(public_router)
        app.dependency_overrides[require_principal] = lambda: {"user_id": "applicant-1"}
        client = user_scoped_client(app, "applicant-1", raise_server_exceptions=False)
        path = "/openapi/v1/bots/work-orders/events"
    else:
        app.include_router(internal_router)
        caller = VerifiedCaller(
            principals=(
                UserPrincipal(
                    subject=GatewayUser(
                        id="applicant-1", username="applicant@example.test"
                    ),
                ),
            )
        )
        app.dependency_overrides[require_user_caller] = lambda: caller
        client = TestClient(app, raise_server_exceptions=False)
        path = "/api/v1/work-orders/events"
    with client:
        yield client, path


def payload():
    return {
        "event_category": "APPROVAL",
        "approval_mode": "AUTO",
        "biz_type": "BOT_FRIEND",
        "biz_id": "friend-request",
        "event_type": "HUMAN2BOT_FRIEND_APPLIED",
        "applicant_user_id": "applicant-1",
        "approver_user_ids": ["unrelated-approver"],
        "recipient_user_ids": [" notify-1 ", "", "notify-2", "notify-1", "\t"],
        "title": "Friend request",
        "biz_data": {"request_ids": ["request-local"]},
    }


@pytest.mark.parametrize("failure", [False, True])
def test_auto_callback_precedes_terminal_notices_to_only_recipients(
    world,
    http_entry,
    failure,
):
    def callback(*args, **kwargs):
        with world.db.orm_session() as session:
            order = session.query(WorkOrderModel).one()
            state = snapshot(world, order.id)
            assert state["status"] == "PROCESSING"
            assert state["approvers"] == 0
            assert state["notices"] == []
        if failure:
            raise TimeoutError("callback response lost")
        return httpx.Response(
            200,
            json={"code": 20000},
            request=httpx.Request("POST", "https://bcn.invalid/"),
        )

    world.http.post.side_effect = callback
    client, path = http_entry
    response = client.post(
        path, json=payload(), headers={"X-Request-Id": "auto-sqlite"}
    )
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["status"] == ("FAILED" if failure else "APPROVED")
    assert len(data["notification_ids"]) == 2
    state = snapshot(world, data["work_order_id"])
    assert state["status"] == data["status"]
    assert state["approvers"] == 0
    assert state["notices"] == [("NOTICE", "notify-1"), ("NOTICE", "notify-2")]
    world.http.post.assert_called_once()
    assert world.path.is_file()


@pytest.mark.parametrize("recipients", [[], ["", " ", "\t"]])
def test_auto_does_not_fall_back_to_approvers_or_write_invalid_event(
    world,
    http_entry,
    recipients,
):
    client, path = http_entry
    response = client.post(path, json=payload() | {"recipient_user_ids": recipients})
    assert response.status_code == 400
    with world.db.orm_session() as session:
        assert session.query(WorkOrderModel).count() == 0
    world.http.post.assert_not_called()


def test_auto_cannot_forge_applicant(world, http_entry):
    client, path = http_entry
    response = client.post(path, json=payload() | {"applicant_user_id": "someone-else"})
    assert response.status_code == 403
    with world.db.orm_session() as session:
        assert session.query(WorkOrderModel).count() == 0
    world.http.post.assert_not_called()
