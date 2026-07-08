"""Tests for aicoding workspace initialization endpoint path validation."""
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.aicoding.router import router as aicoding_router
from agentclaw.community.core.aicoding.services.workspace_service import WorkspaceService
from agentclaw.community.core.auth import AuthenticatedIdentity
from agentclaw.community.adapters.http.auth.dependencies import get_current_user


@pytest.fixture
def mock_user():
    return AuthenticatedIdentity(
        id="bu_001", staffId="user_001", operatorName="test",
        nickName="test", realName="test",
    )


@pytest.fixture
def mock_workspace_service():
    svc = MagicMock(spec=WorkspaceService)
    svc.initialize_workspace = AsyncMock(return_value={
        "workspace_id": "ws-abc",
        "path": "/mock/path",
        "name": "workspace",
        "repos": [],
        "git": None,
        "warnings": [],
        "ready": True,
    })
    return svc


@pytest.fixture
def client(mock_user, mock_workspace_service):
    app = FastAPI()
    app.include_router(aicoding_router)
    app.dependency_overrides[get_current_user] = lambda: mock_user

    from agentclaw.community.api.workspace_service import WorkspaceServiceProtocol

    class _TestModule(Module):
        def configure(self, binder):
            binder.bind(WorkspaceService, to=mock_workspace_service)
            binder.bind(WorkspaceServiceProtocol, to=mock_workspace_service)

    attach_injector(app, Injector([_TestModule()]))
    return TestClient(app, raise_server_exceptions=False)


class TestWorkspacePathValidation:
    """Verify that path prefix validation accepts /workspace and rejects invalid paths."""

    def test_default_path_workspace_accepted(self, client, mock_workspace_service):
        resp = client.post("/api/aicoding/workspace/initialize", json={
            "bot_id": "bot1",
            "path": "/workspace",
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        mock_workspace_service.initialize_workspace.assert_called_once()

    def test_subpath_under_workspace_accepted(self, client, mock_workspace_service):
        resp = client.post("/api/aicoding/workspace/initialize", json={
            "bot_id": "bot1",
            "path": "/workspace/myproject",
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_legacy_path_workspace_aicoding_still_accepted(self, client, mock_workspace_service):
        """Old default /workspace/aicoding must still pass validation."""
        resp = client.post("/api/aicoding/workspace/initialize", json={
            "bot_id": "bot1",
            "path": "/workspace/aicoding",
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_absolute_container_path_rejected(self, client):
        resp = client.post("/api/aicoding/workspace/initialize", json={
            "bot_id": "bot1",
            "path": "/home/admin/.aicoding/workspace",
        })
        assert resp.status_code == 400
        assert "/workspace" in resp.json()["detail"]

    def test_root_path_rejected(self, client):
        resp = client.post("/api/aicoding/workspace/initialize", json={
            "bot_id": "bot1",
            "path": "/",
        })
        assert resp.status_code == 400

    def test_path_traversal_rejected(self, client):
        resp = client.post("/api/aicoding/workspace/initialize", json={
            "bot_id": "bot1",
            "path": "/workspace/../etc/passwd",
        })
        assert resp.status_code == 400


class TestWorkspaceServiceDefaults:
    """Verify WorkspaceService default parameters match /workspace."""

    def test_get_workspace_path_default(self):
        from agentclaw.community.core.aicoding.services.workspace_service import WorkspaceService
        from pathlib import Path
        mock_pf = MagicMock()
        mock_pf.get_bot_engine_dir.return_value = Path("/base/staff_u/bot/aicoding")
        svc = WorkspaceService(
            bot_provider=MagicMock(),
            device_provider=MagicMock(),
            path_factory=mock_pf,
            sandbox_client=MagicMock(),
        )
        result = svc.get_workspace_path("u", "bot")
        assert result == "/base/staff_u/bot/aicoding/workspace"
