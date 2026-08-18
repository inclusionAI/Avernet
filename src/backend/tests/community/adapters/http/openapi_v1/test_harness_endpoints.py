"""Smoke tests for the public ``/openapi/v1/harness`` router.

A minimal FastAPI app hosts the harness router with the caller principal
overridden and the harness services bound to mocks via the injector — the same
harness the bots endpoint tests use. The real authenticator stays a stub;
``require_principal`` is overridden per test to supply (or withhold) a caller.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.harness.router import router
from agentclaw.community.api.content_scanner_service import ContentScannerProtocol
from agentclaw.community.api.patch_engine_service import PatchEngineProtocol
from agentclaw.community.api.patch_library_service import PatchLibraryProtocol
from agentclaw.community.api.patch_planner_service import PatchPlannerProtocol
from agentclaw.community.api.collaborator_service import CollaboratorServiceProtocol
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.harness import (
    HarnessPatchRecordRepository,
    HarnessPatchRepository,
    HarnessScanRecordRepository,
)

BOT = {"id": 77, "bot_id": "b1", "owner_id": "u1"}


@pytest.fixture
def bot_repo():
    m = MagicMock()
    m.get_by_id.return_value = BOT
    return m


@pytest.fixture
def collaborator():
    m = MagicMock()
    m.check_collaborator_permission.return_value = {"has_permission": False}
    return m


@pytest.fixture
def scan_repo():
    m = MagicMock()
    m.get_latest_dim_records.return_value = []
    m.list_dim_history.return_value = ([], 0)
    return m


@pytest.fixture
def client(bot_repo, collaborator, scan_repo):
    class _M(Module):
        def configure(self, binder):
            binder.bind(BotRepository, to=bot_repo)
            binder.bind(CollaboratorServiceProtocol, to=collaborator)
            binder.bind(HarnessScanRecordRepository, to=scan_repo)
            binder.bind(HarnessPatchRepository, to=MagicMock())
            binder.bind(HarnessPatchRecordRepository, to=MagicMock())
            binder.bind(ContentScannerProtocol, to=MagicMock())
            binder.bind(PatchPlannerProtocol, to=MagicMock())
            binder.bind(PatchEngineProtocol, to=MagicMock())
            binder.bind(PatchLibraryProtocol, to=MagicMock())

    app = FastAPI()
    app.include_router(router)
    mount_public_error_handlers(app)
    attach_injector(app, Injector([_M()]))
    app.dependency_overrides[require_principal] = lambda: {"user_id": "u1"}
    return user_scoped_client(app, "u1")


def test_missing_principal_is_401(client):
    client.app.dependency_overrides[require_principal] = lambda: None
    resp = client.get("/openapi/v1/harness/bots/b1/dim-report?entity_id=u1")
    assert resp.status_code == 401
    assert resp.json()["code"] == 401000


def test_dim_report_happy_path_for_owner(client, scan_repo):
    scan_repo.get_latest_dim_records.return_value = [
        {"scan_dim": "skill", "health_score": 90, "grade": "A", "status": "completed"}
    ]
    resp = client.get("/openapi/v1/harness/bots/b1/dim-report?entity_id=u1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200000
    assert body["data"]["bot_id"] == "b1"
    assert body["data"]["items"][0]["scan_dim"] == "skill"


def test_dim_history_happy_path_for_owner(client, scan_repo):
    resp = client.get("/openapi/v1/harness/bots/b1/dim-history?entity_id=u1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200000
    assert body["data"]["total"] == 0


def test_non_owner_without_grant_is_404(client, collaborator):
    client.app.dependency_overrides[require_principal] = lambda: {"user_id": "u2"}
    resp = client.get("/openapi/v1/harness/bots/b1/dim-report?entity_id=u2", params={"user_id": "u2"})
    assert resp.status_code == 404


def test_unknown_bot_is_404(client, bot_repo):
    bot_repo.get_by_id.return_value = None
    resp = client.get("/openapi/v1/harness/bots/nope/dim-report?entity_id=u1")
    assert resp.status_code == 404


def test_collaborator_with_grant_passes(client, collaborator):
    collaborator.check_collaborator_permission.return_value = {"has_permission": True}
    client.app.dependency_overrides[require_principal] = lambda: {"user_id": "u2"}
    resp = client.get("/openapi/v1/harness/bots/b1/dim-report?entity_id=u2", params={"user_id": "u2"})
    assert resp.status_code == 200
