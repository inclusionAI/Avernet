"""Unit tests for Sandbox Device Router.

Tests the router layer with mocked handler and API key validation.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from secbaas.adapters.web.routers.health_checker.sandbox_device_router import (
    router,
    validate_sandbox_device_api_key,
)
from secbaas.api.api_gateway import APIKeyRecord
from secbaas.core.service.health_check.sandbox import (
    AcBindingSandboxHandler,
    PaginatedResult,
    RenewTtlResult,
    SandboxDeviceInfo,
    SandboxDeviceRouter,
    WarnResult,
)
from tests.unit.adapters.web.conftest import iter_api_routes


@pytest.fixture(autouse=True)
def mock_db_manager():
    """Mock db_manager to prevent database initialization errors."""
    from secbaas.core.database import db_manager

    original = db_manager._connection_factory
    db_manager._connection_factory = lambda ds: MagicMock()
    yield
    db_manager._connection_factory = original


@pytest.fixture
def mock_api_key():
    """Create a mock API key record with health-checker app_type."""
    return APIKeyRecord(
        id=1,
        gmt_create=datetime(2026, 1, 1),
        gmt_modified=datetime(2026, 1, 1),
        api_key_hash="test_hash",
        api_key_prefix="sk_test_",
        key_name="Test Key",
        app_id="test_app",
        app_type="health-checker",
        description="Test API Key",
        rate_limit_rpm=None,
        rate_limit_rpd=None,
        status="ACTIVE",
        owner="test_user",
        tenant="default",
        env="prod",
        creator="test_user",
        modifier=None,
        policy=None,
    )


@pytest.fixture
def mock_handler():
    """Create a mock handler with async methods."""
    mock = MagicMock(spec=AcBindingSandboxHandler)
    mock.query_active_sandboxes = MagicMock()
    mock.warn_device = AsyncMock()
    mock.renew_ttl = AsyncMock()
    return mock


@pytest.fixture
def mock_router(mock_handler):
    """Create a mock SandboxDeviceRouter."""
    router = MagicMock(spec=SandboxDeviceRouter)
    router.query_active_sandboxes = mock_handler.query_active_sandboxes
    router.warn_device = AsyncMock()
    router.renew_ttl = AsyncMock()
    return router


@pytest.fixture
def client(mock_router, mock_api_key):
    """Create a test client with dependency override."""
    app = FastAPI()
    app.include_router(router)

    # Override the DI-injected sandbox_device_router dependency
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if dep.name == "device_router":
                app.dependency_overrides[dep.call] = lambda: mock_router

    async def override_validate():
        return mock_api_key

    app.dependency_overrides[validate_sandbox_device_api_key] = override_validate

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


class TestListActiveSandboxes:
    """GET /api/v1/sandbox-device/active-sandboxes"""

    def test_success_ac_binding(self, client, mock_router):
        """Test listing active sandboxes for ac_binding table."""
        mock_router.query_active_sandboxes = MagicMock(
            return_value=PaginatedResult(
                total=2,
                page=1,
                page_size=10,
                items=[
                    SandboxDeviceInfo(
                        table_id=1,
                        table_type="ac_binding",
                        sandbox_id="ARCA-SANDBOX-001@0",
                        ttl_expiration_time="2026-06-01 12:00:00",
                        ttl_expiration_timestamp=1717233600000,
                        refresh_fail_count=0,
                        status="ACTIVE",
                    ),
                    SandboxDeviceInfo(
                        table_id=2,
                        table_type="ac_binding",
                        sandbox_id="ARCA-SANDBOX-002@0",
                        ttl_expiration_time="2026-06-01 14:00:00",
                        ttl_expiration_timestamp=1717240800000,
                        refresh_fail_count=1,
                        status="ACTIVE",
                    ),
                ],
            )
        )

        resp = client.get(
            "/api/v1/sandbox-device/active-sandboxes",
            params={"table_type": "ac_binding", "page": 1, "page_size": 10},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["total"] == 2
        assert len(data["data"]["items"]) == 2
        assert data["data"]["items"][0]["sandbox_id"] == "ARCA-SANDBOX-001@0"

    def test_success_baas(self, client, mock_router):
        """Test listing active sandboxes for baas table."""
        mock_router.query_active_sandboxes = MagicMock(
            return_value=PaginatedResult(
                total=1,
                page=1,
                page_size=10,
                items=[
                    SandboxDeviceInfo(
                        table_id=10,
                        table_type="baas",
                        sandbox_id="ARCA-SANDBOX-010@0",
                        ttl_expiration_time="2026-06-02 12:00:00",
                        ttl_expiration_timestamp=1717320000000,
                        refresh_fail_count=0,
                        status="ACTIVE",
                    ),
                ],
            )
        )

        resp = client.get(
            "/api/v1/sandbox-device/active-sandboxes",
            params={"table_type": "baas", "env": "dev", "page": 1, "page_size": 10},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["total"] == 1
        assert data["data"]["items"][0]["table_type"] == "baas"

    def test_empty_result(self, client, mock_router):
        """Test listing with no active sandboxes."""
        mock_router.query_active_sandboxes = MagicMock(
            return_value=PaginatedResult(
                total=0,
                page=1,
                page_size=10,
                items=[],
            )
        )

        resp = client.get(
            "/api/v1/sandbox-device/active-sandboxes",
            params={"table_type": "ac_binding", "page": 1, "page_size": 10},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["total"] == 0
        assert data["data"]["items"] == []


class TestProbeAndWarn:
    """POST /api/v1/sandbox-device/probe-and-warn"""

    def test_probe_success_reset(self, client, mock_router):
        """Test probe success with reset action."""
        mock_router.warn_device = AsyncMock(
            return_value=WarnResult(
                table_id=1,
                table_type="ac_binding",
                action="RESET",
                refresh_fail_count=0,
            )
        )

        resp = client.post(
            "/api/v1/sandbox-device/probe-and-warn",
            json={"table_id": 1, "table_type": "ac_binding"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["action"] == "RESET"
        assert data["data"]["refresh_fail_count"] == 0

    def test_probe_increment(self, client, mock_router):
        """Test probe failure with increment action."""
        mock_router.warn_device = AsyncMock(
            return_value=WarnResult(
                table_id=1,
                table_type="ac_binding",
                action="INCREMENT",
                refresh_fail_count=3,
            )
        )

        resp = client.post(
            "/api/v1/sandbox-device/probe-and-warn",
            json={"table_id": 1, "table_type": "ac_binding"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["action"] == "INCREMENT"
        assert data["data"]["refresh_fail_count"] == 3

    def test_probe_warning(self, client, mock_router):
        """Test probe failure reaching warning threshold."""
        mock_router.warn_device = AsyncMock(
            return_value=WarnResult(
                table_id=1,
                table_type="baas",
                action="STOPPED",
                refresh_fail_count=10,
            )
        )

        resp = client.post(
            "/api/v1/sandbox-device/probe-and-warn",
            json={"table_id": 5, "table_type": "baas"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["action"] == "STOPPED"
        assert data["data"]["refresh_fail_count"] == 10

    def test_probe_skip_non_active(self, client, mock_router):
        """Test probe skips non-ACTIVE status records."""
        mock_router.warn_device = AsyncMock(
            return_value=WarnResult(
                table_id=1,
                table_type="ac_binding",
                action="SKIP",
                refresh_fail_count=0,
            )
        )

        resp = client.post(
            "/api/v1/sandbox-device/probe-and-warn",
            json={"table_id": 1, "table_type": "ac_binding"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["action"] == "SKIP"

    def test_record_not_found(self, client, mock_router):
        """Test probe with non-existent record."""
        mock_router.warn_device = AsyncMock(
            side_effect=ValueError("Binding record not found: id=999")
        )

        resp = client.post(
            "/api/v1/sandbox-device/probe-and-warn",
            json={"table_id": 999, "table_type": "ac_binding"},
        )

        assert resp.status_code == 404


class TestRenewTtl:
    """POST /api/v1/sandbox-device/renew-ttl"""

    def test_renew_success(self, client, mock_router):
        """Test TTL renewal success."""
        mock_router.renew_ttl = AsyncMock(
            return_value=RenewTtlResult(
                table_id=1,
                table_type="ac_binding",
                device_id="ARCA-SANDBOX-001@0",
                success=True,
                old_expiration_time="2026-06-01 12:00:00",
                new_expiration_time="2026-06-02 12:00:00",
                refresh_fail_count=0,
                error=None,
            )
        )

        resp = client.post(
            "/api/v1/sandbox-device/renew-ttl",
            json={"table_id": 1, "table_type": "ac_binding"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["success"] is True
        assert data["data"]["new_expiration_time"] == "2026-06-02 12:00:00"
        assert data["data"]["refresh_fail_count"] == 0

    def test_renew_failure(self, client, mock_router):
        """Test TTL renewal failure."""
        mock_router.renew_ttl = AsyncMock(
            return_value=RenewTtlResult(
                table_id=1,
                table_type="ac_binding",
                device_id="ARCA-SANDBOX-001@0",
                success=False,
                old_expiration_time="2026-06-01 12:00:00",
                new_expiration_time=None,
                refresh_fail_count=2,
                error="Arca API error",
            )
        )

        resp = client.post(
            "/api/v1/sandbox-device/renew-ttl",
            json={"table_id": 1, "table_type": "ac_binding"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["success"] is False
        assert data["data"]["error"] == "Arca API error"
        assert data["data"]["refresh_fail_count"] == 2

    def test_renew_baas_success(self, client, mock_router):
        """Test TTL renewal success for baas table."""
        mock_router.renew_ttl = AsyncMock(
            return_value=RenewTtlResult(
                table_id=10,
                table_type="baas",
                device_id="ARCA-SANDBOX-010@0",
                success=True,
                old_expiration_time="2026-06-01 12:00:00",
                new_expiration_time="2026-06-03 12:00:00",
                refresh_fail_count=0,
                error=None,
            )
        )

        resp = client.post(
            "/api/v1/sandbox-device/renew-ttl",
            json={"table_id": 10, "table_type": "baas"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["success"] is True
        assert data["data"]["table_type"] == "baas"

    def test_record_not_found(self, client, mock_router):
        """Test renew with non-existent record."""
        mock_router.renew_ttl = AsyncMock(
            side_effect=ValueError("Binding record not found: id=999")
        )

        resp = client.post(
            "/api/v1/sandbox-device/renew-ttl",
            json={"table_id": 999, "table_type": "ac_binding"},
        )

        assert resp.status_code == 404

    def test_no_sandbox_id(self, client, mock_router):
        """Test renew when no sandbox_id in record."""
        mock_router.renew_ttl = AsyncMock(
            side_effect=ValueError("No sandbox_id in device_props for binding id=1")
        )

        resp = client.post(
            "/api/v1/sandbox-device/renew-ttl",
            json={"table_id": 1, "table_type": "ac_binding"},
        )

        assert resp.status_code == 404


class TestValidateApiKey:
    """Test API key validation."""

    def test_allowed_app_type(self, mock_api_key):
        """Test that health-checker app_type is allowed."""
        from secbaas.adapters.web.routers.health_checker.sandbox_device_router import (
            SANDBOX_DEVICE_ALLOWED_APP_TYPES,
        )

        assert mock_api_key.app_type in SANDBOX_DEVICE_ALLOWED_APP_TYPES

    def test_forbidden_app_type(self):
        """Test that non-health-checker app_type is rejected."""
        forbidden_key = APIKeyRecord(
            id=2,
            gmt_create=datetime(2026, 1, 1),
            gmt_modified=datetime(2026, 1, 1),
            api_key_hash="test_hash_2",
            api_key_prefix="sk_other_",
            key_name="Other Key",
            app_id="other_app",
            app_type="web",
            description="Other API Key",
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="ACTIVE",
            owner="test_user",
            tenant="default",
            env="prod",
            creator="test_user",
            modifier=None,
            policy=None,
        )

        assert forbidden_key.app_type not in {"health-checker"}
