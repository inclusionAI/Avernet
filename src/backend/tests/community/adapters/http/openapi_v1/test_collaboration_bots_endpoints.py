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
    bind_edit_lock_seam,
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
                bind_edit_lock_seam(binder)

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
