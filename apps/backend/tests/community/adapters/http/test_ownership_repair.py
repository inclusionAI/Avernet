"""Ops route uses authenticated actor and refuses caller-provided identity scope."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.bot_management.ownership_repair import router
from agentclaw.community.core.bot_management.ownership_sync.operations import (
    OwnershipRepairError,
    OwnershipSyncOperations,
)


@pytest.fixture
def repair_client():
    service = Mock(spec=OwnershipSyncOperations)
    injector = Injector()
    injector.binder.bind(OwnershipSyncOperations, to=service)
    app = FastAPI()
    app.include_router(router, prefix="/api/bots")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        staffId="trusted-ops"
    )
    attach_injector(app, injector)
    return TestClient(app), service


def test_repair_actor_comes_from_login(repair_client):
    client, service = repair_client
    service.request_repair.return_value = {
        "submission": "queued",
        "delivery_confirmed": False,
    }
    response = client.post("/api/bots/owner-handoffs/bcn-repair", json={"bot_pk": 7})
    assert response.status_code == 202
    assert response.json()["data"]["delivery_confirmed"] is False
    service.request_repair.assert_called_once_with(bot_pk=7, actor_id="trusted-ops")


@pytest.mark.parametrize("field", ["actor_id", "owner_id", "env", "avernet_tenant"])
def test_caller_cannot_override_actor_or_scope(repair_client, field):
    client, service = repair_client
    assert (
        client.post(
            "/api/bots/owner-handoffs/bcn-repair", json={"bot_pk": 7, field: "spoof"}
        ).status_code
        == 422
    )
    service.request_repair.assert_not_called()


@pytest.mark.parametrize(
    "code,status",
    [
        ("ADMIN_REQUIRED", 403),
        ("BOT_NOT_FOUND", 404),
        ("OWNERSHIP_SYNC_UNAVAILABLE", 503),
    ],
)
def test_repair_rejection_is_not_accepted(repair_client, code, status):
    client, service = repair_client
    service.request_repair.side_effect = OwnershipRepairError(code)
    assert (
        client.post(
            "/api/bots/owner-handoffs/bcn-repair", json={"bot_pk": 7}
        ).status_code
        == status
    )
