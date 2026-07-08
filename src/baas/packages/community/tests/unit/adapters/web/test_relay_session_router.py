"""Unit tests for relay_session_router.

Tests the HTTP endpoints for relay session lifecycle management:
GET /api/v1/paas/relay-sessions/{session_id} and
PUT /api/v1/paas/relay-sessions/{session_id}.
"""

from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock

import pytest
from dependency_injector.wiring import Provide
from fastapi.testclient import TestClient

from secbaas.adapters.web.app import app
from secbaas.adapters.web.routers.relay_session_router import (
    router as _router,  # noqa: F401
)
from secbaas.core.repository.ws_relay_session import (
    WsRelaySessionRecord,
    WsRelaySessionRepository,
)
from tests.unit.adapters.web.conftest import iter_api_routes


def _patch_repo(mock_repo):
    """Override DI-injected Provide[...] dependency for ws_relay_session_repository."""
    old_overrides = dict(app.dependency_overrides)
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if isinstance(dep.call, Provide):
                app.dependency_overrides[dep.call] = lambda: mock_repo
    return old_overrides


def _restore_overrides(old_overrides):
    app.dependency_overrides = old_overrides


@pytest.fixture
def client():
    """Create a FastAPI test client."""
    return TestClient(app)


def _make_record(session_id="s1", status="init", **overrides):
    """Create a WsRelaySessionRecord with default values."""
    defaults = dict(
        id=1,
        gmt_create=datetime(2026, 1, 1, tzinfo=UTC),
        gmt_modified=datetime(2026, 1, 1, tzinfo=UTC),
        session_id=session_id,
        machine_id="m1",
        connected_server_instance="",
        status=status,
        env="dev",
        gmt_close=None,
        connected_route_info=None,
        operator="user-1",
    )
    defaults.update(overrides)
    return WsRelaySessionRecord(**defaults)


class TestGetRelaySession:
    """Test GET /api/v1/paas/relay-sessions/{session_id}."""

    def test_get_existing_session_returns_200(self, client):
        """Test 1: GET existing session returns 200 with record body."""
        mock_repo = MagicMock(spec=WsRelaySessionRepository)
        record = _make_record(session_id="test-session-1", status="active")
        mock_repo.get_by_session_id.return_value = record

        old = _patch_repo(mock_repo)
        try:
            response = client.get("/api/v1/paas/relay-sessions/test-session-1")
            assert response.status_code == 200
            body = response.json()
            assert body["session_id"] == "test-session-1"
            assert body["status"] == "active"
        finally:
            _restore_overrides(old)

    def test_get_nonexistent_session_returns_404(self, client):
        """Test 2: GET nonexistent session returns 404."""
        mock_repo = MagicMock(spec=WsRelaySessionRepository)
        mock_repo.get_by_session_id.return_value = None

        old = _patch_repo(mock_repo)
        try:
            response = client.get("/api/v1/paas/relay-sessions/nonexistent-id")
            assert response.status_code == 404
            body = response.json()
            assert "detail" in body
            assert body["detail"]["error_code"] == "RELAY_SESSION_NOT_FOUND"
        finally:
            _restore_overrides(old)


class TestPutRelaySession:
    """Test PUT /api/v1/paas/relay-sessions/{session_id}."""

    def test_put_active_returns_200(self, client):
        """Test 3: PUT active on init session returns 200."""
        mock_repo = MagicMock(spec=WsRelaySessionRepository)
        record = _make_record(session_id="init-session", status="init")
        mock_repo.get_by_session_id.return_value = record

        old = _patch_repo(mock_repo)
        try:
            response = client.put(
                "/api/v1/paas/relay-sessions/init-session",
                json={
                    "status": "active",
                    "connected_server_instance": "i-001",
                    "connected_route_info": {"worker": "w1"},
                },
            )
            assert response.status_code == 200, (
                f"Got {response.status_code}: {response.json()}"
            )
            body = response.json()
            assert body["session_id"] == "init-session"
            assert body["status"] == "active"
        finally:
            _restore_overrides(old)

    def test_put_reverse_transition_returns_409(self, client):
        """Test 4: PUT active on closed session returns 409 (reverse transition)."""
        from secbaas.api.device_manage._errors import DeviceCreationError

        mock_repo = MagicMock(spec=WsRelaySessionRepository)
        record = _make_record(session_id="closed-session", status="closed")
        mock_repo.get_by_session_id.return_value = record

        # _validate_transition raises DeviceCreationError for invalid transition
        def raise_conflict(current, target):
            raise DeviceCreationError(
                error_code="RELAY_STATE_CONFLICT",
                message=f"Invalid state transition: {current} -> {target}",
            )

        mock_repo._validate_transition.side_effect = raise_conflict

        old = _patch_repo(mock_repo)
        try:
            response = client.put(
                "/api/v1/paas/relay-sessions/closed-session",
                json={"status": "active"},
            )
            assert response.status_code == 409, (
                f"Got {response.status_code}: {response.json()}"
            )
            body = response.json()
            assert "detail" in body
            assert body["detail"]["error_code"] == "RELAY_STATE_CONFLICT"
        finally:
            _restore_overrides(old)

    def test_put_same_status_idempotent_returns_200(self, client):
        """Test 5: PUT same status (active -> active) returns 200 no-op."""
        mock_repo = MagicMock(spec=WsRelaySessionRepository)
        record = _make_record(session_id="active-session", status="active")
        mock_repo.get_by_session_id.return_value = record

        old = _patch_repo(mock_repo)
        try:
            response = client.put(
                "/api/v1/paas/relay-sessions/active-session",
                json={"status": "active"},
            )
            assert response.status_code == 200, (
                f"Got {response.status_code}: {response.json()}"
            )
            body = response.json()
            assert body["session_id"] == "active-session"
            assert body["status"] == "active"
            # Verify no state change was attempted
            mock_repo.update_active.assert_not_called()
        finally:
            _restore_overrides(old)

    def test_put_invalid_status_returns_422(self, client):
        """Test 6: PUT with invalid status returns 422 (Pydantic Literal validation)."""
        mock_repo = MagicMock(spec=WsRelaySessionRepository)
        record = _make_record(session_id="s1", status="init")
        mock_repo.get_by_session_id.return_value = record

        old = _patch_repo(mock_repo)
        try:
            response = client.put(
                "/api/v1/paas/relay-sessions/s1",
                json={"status": "running"},
            )
            assert response.status_code == 422, (
                f"Got {response.status_code}: {response.json()}"
            )
        finally:
            _restore_overrides(old)

    def test_put_nonexistent_session_returns_404(self, client):
        """Test 7: PUT to nonexistent session returns 404."""
        mock_repo = MagicMock(spec=WsRelaySessionRepository)
        mock_repo.get_by_session_id.return_value = None

        old = _patch_repo(mock_repo)
        try:
            response = client.put(
                "/api/v1/paas/relay-sessions/nonexistent-id",
                json={"status": "active"},
            )
            assert response.status_code == 404, (
                f"Got {response.status_code}: {response.json()}"
            )
            body = response.json()
            assert "detail" in body
            assert body["detail"]["error_code"] == "RELAY_SESSION_NOT_FOUND"
        finally:
            _restore_overrides(old)
