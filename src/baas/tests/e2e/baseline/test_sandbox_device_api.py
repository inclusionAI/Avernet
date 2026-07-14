"""E2E tests for sandbox device management API.

Endpoints:
- GET /api/v1/sandbox-device/active-sandboxes
- POST /api/v1/sandbox-device/probe-and-warn
- POST /api/v1/sandbox-device/renew-ttl
"""

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestSandboxDeviceApi:
    """Test suite for sandbox device management endpoints."""

    @pytest.mark.asyncio
    async def test_active_sandboxes_without_api_key_403(self, api):
        """Test that listing active sandboxes without API key returns 401/403."""
        resp = await api.client.get(
            "/api/v1/sandbox-device/active-sandboxes",
            params={"table_type": "baas"},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_active_sandboxes_missing_table_type_403(self, api):
        """Test that missing table_type returns 401, 403, or 422."""
        resp = await api.client.get("/api/v1/sandbox-device/active-sandboxes")
        assert resp.status_code in (401, 403, 422)

    @pytest.mark.asyncio
    async def test_probe_and_warn_without_api_key_403(self, api):
        """Test that probe-and-warn without API key returns 401/403."""
        resp = await api.client.post(
            "/api/v1/sandbox-device/probe-and-warn",
            json={"table_id": 1, "table_type": "baas"},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_probe_and_warn_missing_fields_403(self, api):
        """Test that probe-and-warn with missing body fields returns 401, 403, or 422."""
        resp = await api.client.post(
            "/api/v1/sandbox-device/probe-and-warn",
            json={},
        )
        assert resp.status_code in (401, 403, 422)

    @pytest.mark.asyncio
    async def test_renew_ttl_without_api_key_403(self, api):
        """Test that renew-ttl without API key returns 401/403."""
        resp = await api.client.post(
            "/api/v1/sandbox-device/renew-ttl",
            json={"table_id": 1, "table_type": "baas"},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_renew_ttl_missing_fields_403(self, api):
        """Test that renew-ttl with missing body fields returns 401, 403, or 422."""
        resp = await api.client.post(
            "/api/v1/sandbox-device/renew-ttl",
            json={},
        )
        assert resp.status_code in (401, 403, 422)

    @pytest.mark.asyncio
    async def test_active_sandboxes_invalid_table_type_403(self, api):
        """Test that invalid table_type still requires API key first (401/403)."""
        resp = await api.client.get(
            "/api/v1/sandbox-device/active-sandboxes",
            params={"table_type": "invalid_type"},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_active_sandboxes_page_size_too_large_403(self, api):
        """Test that page_size > 100 still requires API key first (401/403)."""
        resp = await api.client.get(
            "/api/v1/sandbox-device/active-sandboxes",
            params={"table_type": "baas", "page_size": 200},
        )
        assert resp.status_code in (401, 403)
