"""Smoke for the bcs publish-to-users endpoint (collaboration_bots).

Bare-FastAPI mount + fastapi-injector binding the BotPublicService to a fake so
the success path (envelope(data)) is exercised without the real di graph / local
approval-noop (those surface as the high-value behaviour tests elsewhere). Verifies
the router is mounted at ``/openapi/v1/collaboration/bots/{bot_uuid}/public`` and
the ``Envelope[BcsPublishResult]`` response wiring is sound.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.collaboration_bots import (
    public_router as collaboration_public_router,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.core.gateway_principal import (
    GatewayUser,
    UserPrincipal,
    VerifiedCaller,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)

USER = "u-smoke"


def _caller_with_user() -> VerifiedCaller:
    return VerifiedCaller(
        principals=(UserPrincipal(subject=GatewayUser(id=USER, username=USER)),)
    )


class _FakeBcsService:
    """Bound BotPublicServiceProtocol — echoes a canned publish result."""

    def __init__(self, public_result: dict) -> None:
        self._result = public_result
        self.captured: dict = {}

    def public_bcs_bot(self, *args, **kwargs) -> dict:
        self.captured = {"args": args, "kwargs": kwargs}
        return self._result


@pytest.fixture
def make_client():
    def _build(public_result: dict):
        svc = _FakeBcsService(public_result)

        class _M(Module):
            def configure(self, binder):
                binder.bind(BotPublicServiceProtocol, to=svc)

        app = FastAPI()
        app.include_router(collaboration_public_router)
        app.dependency_overrides[require_principal] = _caller_with_user
        attach_injector(app, Injector([_M()]))
        mount_public_error_handlers(app)
        return user_scoped_client(app, USER), svc

    return _build


def test_publish_endpoint_mounted_and_returns_envelope(make_client):
    result = {
        "success": True, "puid": "p1", "approval_url": "u1",
        "state": "PROCESSING", "last_operate": None, "error_msg": None,
    }
    client, svc = make_client(result)

    response = client.post(
        "/openapi/v1/collaboration/bots/b1:entity1/public",
        json={
            "public_scope": "user",
            "visibility": "public",
            "view_depts": [{"deptNo": "D1", "deptName": "Tech"}],
        },
    )

    # mounted + served (not 404) + success Envelope
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["success"] is True, body
    assert body["data"]["puid"] == "p1", body
    # the {bot_uuid} wire param reaches the service as bot_uid (BCS identity, not split)
    assert svc.captured["kwargs"]["bot_uid"] == "b1:entity1"
    assert svc.captured["kwargs"]["public_scope"] == "user"
    assert svc.captured["kwargs"]["visibility"] == "public"


def test_publish_endpoint_rejects_invalid_public_scope(make_client):
    client, _svc = make_client({"success": True, "puid": "p1", "state": "PROCESSING"})

    response = client.post(
        "/openapi/v1/collaboration/bots/b1/public",
        json={"public_scope": "team"},  # not in Literal["user","agent"]
    )

    assert response.status_code == 422, response.text  # pydantic validation Envelope


def test_publish_response_locks_to_declared_fields_drops_extras(make_client):
    """The public BcsPublishResult is locked to its declared fields.

    Across public_bcs_bot's three return paths the raw service dict carries
    keys the response must NOT surface: the prod approval reply uses camelCase
    ``lastOperate``, the no-workflow synthesis uses snake ``last_operate``, and
    the private direct path adds ``visibility`` / ``visibility_field``. None of
    these are declared on BcsPublishResult — and ``last_operate`` is removed from
    the contract altogether — so the response must drop every casing/extra and
    keep only {success, puid, approval_url, state, error_msg}.
    """
    result = {
        "success": True,
        "puid": "p1",
        "approval_url": "u1",
        "state": "COMPLETED",
        "error_msg": None,
        "last_operate": "agree",          # snake (no-workflow synthesis path)
        "lastOperate": "AGREE",            # camel (prod approval reply contract)
        "visibility": "private",          # private direct path
        "visibility_field": "user_visibility",
    }
    client, _svc = make_client(result)

    response = client.post(
        "/openapi/v1/collaboration/bots/b1:entity1/public",
        json={"public_scope": "user", "visibility": "public"},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    # declared fields survive
    assert data["success"] is True, data
    assert data["puid"] == "p1", data
    assert data["state"] == "COMPLETED", data
    # last_operate (removed) and lastOperate (camel leak) are both absent
    assert "last_operate" not in data, data
    assert "lastOperate" not in data, data
    # no extras surface at all — the response is locked to the declared fields
    assert set(data) <= {"success", "puid", "approval_url", "state", "error_msg"}, data
