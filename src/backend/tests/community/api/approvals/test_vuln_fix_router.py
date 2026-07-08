"""Tests for approvals router security fix (#99).

Verifies that set_approval_mode requires authentication and
prevents users from modifying other users' approval modes.
"""
from unittest.mock import MagicMock, AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.core.auth.models import AuthenticatedIdentity


# --- Fixtures ---

@pytest.fixture
def user_a():
    return AuthenticatedIdentity(id="1", operatorName="user_a", outUserNo="100011", nickName="UserA")


@pytest.fixture
def mock_connection_manager():
    mgr = MagicMock()
    # Mock get_client to return a client with async send_request
    mock_client = MagicMock()
    mock_client.send_request = AsyncMock(return_value=MagicMock(ok=True, payload={"ok": True}))
    mgr.get_client = AsyncMock(return_value=mock_client)
    return mgr


@pytest.fixture
def client(user_a, mock_connection_manager):
    from agentclaw.community.adapters.http.approvals.router import router
    from agentclaw.community.adapters.http.approvals.dependencies import get_connection_manager

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user_a
    app.dependency_overrides[get_connection_manager] = lambda: mock_connection_manager
    return TestClient(app)


@pytest.fixture
def client_no_auth(mock_connection_manager):
    """Client without auth — simulates unauthenticated request."""
    from agentclaw.community.adapters.http.approvals.router import router
    from agentclaw.community.adapters.http.approvals.dependencies import get_connection_manager

    app = FastAPI()
    app.include_router(router)
    # No get_current_user override, so auth dependency will fail with 401
    app.dependency_overrides[get_connection_manager] = lambda: mock_connection_manager
    return TestClient(app, raise_server_exceptions=False)


# --- Tests for #99: set_approval_mode auth + ownership check ---

class TestSetApprovalModeAuth:
    """POST /api/approvals/mode/set — auth and ownership checks."""

    def test_set_own_approval_mode_succeeds(self, client, mock_connection_manager):
        """User can set their own approval mode."""
        resp = client.post(
            "/api/approvals/mode/set",
            json={
                "user_id": "100011",
                "entity_type": "staff",
                "bot_id": "default",
                "session_key": "test-session",
                "mode": "never",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_set_other_user_approval_mode_forbidden(self, client):
        """User cannot set another user's approval mode (IDOR prevention)."""
        resp = client.post(
            "/api/approvals/mode/set",
            json={
                "user_id": "100012",  # different user
                "entity_type": "staff",
                "bot_id": "default",
                "session_key": "test-session",
                "mode": "never",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "无权" in data.get("error", "")

    def test_unauthenticated_request_rejected(self, client_no_auth):
        """Unauthenticated requests should be rejected with 401."""
        resp = client_no_auth.post(
            "/api/approvals/mode/set",
            json={
                "user_id": "100011",
                "entity_type": "staff",
                "bot_id": "default",
                "session_key": "test-session",
                "mode": "never",
            },
        )
        assert resp.status_code in (401, 403) or resp.status_code >= 400

    def test_invalid_mode_rejected(self, client):
        """Invalid mode values should be rejected."""
        resp = client.post(
            "/api/approvals/mode/set",
            json={
                "user_id": "100011",
                "entity_type": "staff",
                "bot_id": "default",
                "session_key": "test-session",
                "mode": "invalid_mode",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "Invalid mode" in data.get("error", "")


class TestGetApprovalModeAuth:
    """POST /api/approvals/mode/get — now requires authentication."""

    def test_get_approval_mode_authenticated(self, client, mock_connection_manager):
        """Authenticated user can get approval mode."""
        resp = client.post(
            "/api/approvals/mode/get",
            json={
                "user_id": "100011",
                "entity_type": "staff",
                "bot_id": "default",
            },
        )
        assert resp.status_code == 200

    def test_get_approval_mode_unauthenticated(self, client_no_auth):
        """Unauthenticated requests should be rejected."""
        resp = client_no_auth.post(
            "/api/approvals/mode/get",
            json={
                "user_id": "100011",
                "entity_type": "staff",
                "bot_id": "default",
            },
        )
        assert resp.status_code in (401, 403) or resp.status_code >= 400