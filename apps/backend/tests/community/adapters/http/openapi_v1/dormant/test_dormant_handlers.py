"""Endpoint tests for public personal Bot dormant lifecycle routes."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from tests.community.adapters.http.openapi_v1.conftest import (
    SeamBots,
    SeamCollaborators,
    mount_public_error_handlers,
    user_scoped_client,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.bots.router import (
    router as bots_router,
)
from agentclaw.community.adapters.http.openapi_v1.dormant.router import router
from agentclaw.community.api.bot_dormant_service import (
    BotDormantActivateServiceProtocol,
    BotDormantAuditServiceProtocol,
    BotDormantRecycleServiceProtocol,
)
from agentclaw.community.api.bot_app_grant_service import BotAppGrantServiceProtocol
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_app_grant.models import BotAppGrantRecord
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_dormant.audit_service import DormantAuditService
from agentclaw.community.core.bot_dormant.recycle_service import RecycleReleaseFailed
from agentclaw.community.core.bot_dormant.types import BotLifecycleResult
from agentclaw.community.core.bot_management.services.bot_service import (
    BotInvalidLifecycleStateError,
    BotOperationNotAllowedError,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.gateway_principal import (
    AppPrincipal,
    GatewayApp,
    VerifiedCaller,
)


@pytest.fixture
def activate_service():
    service = MagicMock()
    service.activate.return_value = BotLifecycleResult(
        bot_id="b1", owner_id="u1", status="REACTIVATING", changed=True
    )
    return service


@pytest.fixture
def recycle_service():
    service = MagicMock()
    service.recycle.return_value = BotLifecycleResult(
        bot_id="b1", owner_id="u1", status="RECYCLED", changed=True
    )
    return service


@pytest.fixture
def audit_service():
    return MagicMock(spec=DormantAuditService)


@pytest.fixture
def client(activate_service, recycle_service, audit_service):
    class _M(Module):
        def configure(self, binder):
            binder.bind(BotDormantActivateServiceProtocol, to=activate_service)
            binder.bind(BotDormantRecycleServiceProtocol, to=recycle_service)
            binder.bind(BotDormantAuditServiceProtocol, to=audit_service)
            binder.bind(BotRepository, to=SeamBots())
            binder.bind(CollaboratorServiceProtocol, to=SeamCollaborators())

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "u1"}
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    return user_scoped_client(app, "u1")


def test_activate_starts_asynchronously(client, activate_service):
    response = client.post("/openapi/v1/bots/b1/activate")

    assert response.status_code == 202
    assert response.json()["code"] == 202000
    assert response.json()["data"] == {
        "bot_id": "b1",
        "owner_id": "u1",
        "status": "REACTIVATING",
        "changed": True,
        "message": None,
    }
    activate_service.activate.assert_called_once_with(
        bot_id="b1", owner_id="u1", owner_name="u1"
    )


def test_activate_active_bot_is_synchronous_idempotent(client, activate_service):
    activate_service.activate.return_value = BotLifecycleResult(
        bot_id="b1", owner_id="u1", status="ACTIVE", changed=False
    )

    response = client.post("/openapi/v1/bots/b1/activate")

    assert response.status_code == 200
    assert response.json()["code"] == 200000
    assert response.json()["data"]["changed"] is False


def test_recycle_completes_synchronously_and_writes_audit(
    client, recycle_service, audit_service
):
    response = client.post("/openapi/v1/bots/b1/recycle")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "bot_id": "b1",
        "owner_id": "u1",
        "status": "RECYCLED",
        "changed": True,
    }
    recycle_service.recycle.assert_called_once_with(
        bot_id="b1", owner_id="u1", owner_name="u1"
    )
    audit_service.record_openapi_recycle.assert_called_once_with(
        request_id="", bot_id="b1", owner_id="u1"
    )


def test_recycle_idempotent_result_does_not_duplicate_audit(
    client, recycle_service, audit_service
):
    recycle_service.recycle.return_value = BotLifecycleResult(
        bot_id="b1", owner_id="u1", status="RECYCLED", changed=False
    )

    response = client.post("/openapi/v1/bots/b1/recycle")

    assert response.status_code == 200
    assert response.json()["data"]["changed"] is False
    audit_service.record_openapi_recycle.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        BotOperationNotAllowedError("teclaw bots use their engine-owned lifecycle"),
        BotInvalidLifecycleStateError(bot_id="b1", current_status="FAILED"),
    ],
)
def test_lifecycle_rejections_use_public_conflict_contract(
    client, recycle_service, error
):
    recycle_service.recycle.side_effect = error

    response = client.post("/openapi/v1/bots/b1/recycle")

    assert response.status_code == 409
    assert response.json()["data"] is None


def test_recycle_release_failure_uses_bad_gateway_contract(client, recycle_service):
    recycle_service.recycle.side_effect = RecycleReleaseFailed("release failed")

    response = client.post("/openapi/v1/bots/b1/recycle")

    assert response.status_code == 502
    assert response.json()["message"] == "Bot resource release failed"


@pytest.mark.parametrize("path", ["activate", "recycle"])
def test_admin_collaborator_cannot_change_dormant_lifecycle(path: str):
    activate_service = MagicMock()
    recycle_service = MagicMock()

    class _M(Module):
        def configure(self, binder):
            binder.bind(BotDormantActivateServiceProtocol, to=activate_service)
            binder.bind(BotDormantRecycleServiceProtocol, to=recycle_service)
            binder.bind(
                BotDormantAuditServiceProtocol,
                to=MagicMock(spec=DormantAuditService),
            )
            binder.bind(BotRepository, to=SeamBots())
            binder.bind(
                CollaboratorServiceProtocol,
                to=SeamCollaborators(PermissionLevel.ADMIN),
            )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "u2"}
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    client = user_scoped_client(app, "u2")

    response = client.post(
        f"/openapi/v1/bots/b1/{path}",
        params={"owner_id": "u1"},
    )

    assert response.status_code == 404
    activate_service.activate.assert_not_called()
    recycle_service.recycle.assert_not_called()


def test_admin_collaborator_cannot_poll_dormant_status():
    bot_service = MagicMock()

    class _M(Module):
        def configure(self, binder):
            binder.bind(BotServiceProtocol, to=bot_service)
            binder.bind(BotRepository, to=SeamBots())
            binder.bind(
                CollaboratorServiceProtocol,
                to=SeamCollaborators(PermissionLevel.ADMIN),
            )

    app = FastAPI()
    app.include_router(bots_router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "u2"}
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    client = user_scoped_client(app, "u2")

    response = client.get(
        "/openapi/v1/bots/b1/status",
        params={"owner_id": "u1"},
    )

    assert response.status_code == 404
    bot_service.get_bot.assert_not_called()


@pytest.mark.parametrize(
    ("method", "path", "expected_status"),
    [
        ("POST", "activate", 202),
        ("POST", "recycle", 200),
        ("GET", "status", 200),
    ],
)
def test_application_grant_is_checked_against_exact_owner(
    method: str, path: str, expected_status: int
):
    activate_service = MagicMock()
    activate_service.activate.return_value = BotLifecycleResult(
        bot_id="b1", owner_id="u1", status="REACTIVATING", changed=True
    )
    recycle_service = MagicMock()
    recycle_service.recycle.return_value = BotLifecycleResult(
        bot_id="b1", owner_id="u1", status="RECYCLED", changed=True
    )
    bot_service = MagicMock()
    bot_service.get_bot.return_value = {
        "bot_id": "b1",
        "owner_id": "u1",
        "status": "ACTIVE",
        "device_binding": {},
    }

    class _Grants:
        def find(self, *, bot_id, owner_id, user_id, app_id):
            if (bot_id, owner_id, user_id, app_id) != ("b1", "u1", "u1", 42):
                return None
            return BotAppGrantRecord(
                id=1,
                app_id=42,
                app_name="partner",
                bot_id="b1",
                user_id="u1",
                owner_id="u1",
                avernet_tenant="teamclaw",
                env="test",
                gmt_create=datetime(2026, 9, 11),
            )

        def list_for_app(self, *, app_id, user_id):
            return []

    class _M(Module):
        def configure(self, binder):
            binder.bind(BotDormantActivateServiceProtocol, to=activate_service)
            binder.bind(BotDormantRecycleServiceProtocol, to=recycle_service)
            binder.bind(BotServiceProtocol, to=bot_service)
            binder.bind(
                BotDormantAuditServiceProtocol,
                to=MagicMock(spec=DormantAuditService),
            )
            binder.bind(BotRepository, to=SeamBots())
            binder.bind(CollaboratorServiceProtocol, to=SeamCollaborators())
            binder.bind(BotAppGrantServiceProtocol, to=_Grants())

    principal = VerifiedCaller(
        principals=(
            AppPrincipal(
                tenant="teamclaw",
                app=GatewayApp(
                    app_id=42,
                    app_name="partner",
                    owners="platform",
                    tenant="teamclaw",
                ),
            ),
        )
    )
    app = FastAPI()
    app.include_router(router)
    app.include_router(bots_router)
    app.dependency_overrides[require_principal] = lambda: principal
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    client = user_scoped_client(app, "u1")

    refused = client.request(
        method,
        f"/openapi/v1/bots/b1/{path}", params={"owner_id": "u2"}
    )
    succeeded = client.request(
        method,
        f"/openapi/v1/bots/b1/{path}", params={"owner_id": "u1"}
    )

    assert refused.status_code == 404
    assert succeeded.status_code == expected_status
    if path == "status":
        bot_service.get_bot.assert_called_once_with("b1", "u1")
    else:
        selected = (
            activate_service.activate
            if path == "activate"
            else recycle_service.recycle
        )
        selected.assert_called_once_with(
            bot_id="b1", owner_id="u1", owner_name="u1"
        )
