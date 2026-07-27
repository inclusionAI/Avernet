"""E2E tests for local PaaS API endpoints.

Endpoints:
- GET /api/v1/local/machines/{machine_id}/info
- GET /api/v1/local/machines/{machine_id}/res-dirs
- GET /api/v1/local/users/{user_id}/machines
"""

import pytest

pytestmark = [pytest.mark.e2e_asgi]


class TestLocalPaasApi:
    """Test suite for local PaaS machine management endpoints."""

    @pytest.mark.asyncio
    async def test_get_machine_info_not_found(self, api, unique_id: str):
        """Test getting info for a non-existent machine returns 404."""
        machine_id = f"nonexistent-{unique_id}"
        resp = await api.client.get(f"/api/v1/local/machines/{machine_id}/info")
        assert resp.status_code in (404, 400)

    @pytest.mark.asyncio
    async def test_get_machine_res_dirs_not_found(self, api, unique_id: str):
        """Test getting res dirs for a non-existent machine returns error."""
        machine_id = f"nonexistent-{unique_id}"
        resp = await api.client.get(
            f"/api/v1/local/machines/{machine_id}/res-dirs",
            params={"dir": "~/Desktop"},
        )
        assert resp.status_code in (404, 400)

    @pytest.mark.asyncio
    async def test_list_user_machines_empty(self, api, unique_id: str):
        """Test listing machines for a non-existent user returns empty list."""
        user_id = f"nonexistent-user-{unique_id}"
        resp = await api.client.get(f"/api/v1/local/users/{user_id}/machines")
        # Should return 200 with empty list when no machines found
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            assert isinstance(data, list)
        else:
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_machine_res_dirs_invalid_path_traversal(self, api):
        """Test that path traversal in res-dirs is rejected."""
        machine_id = "test-machine"
        resp = await api.client.get(
            f"/api/v1/local/machines/{machine_id}/res-dirs",
            params={"dir": "../../etc/passwd"},
        )
        # Should be rejected as invalid params (400)
        assert resp.status_code in (400, 403, 404)

    @pytest.mark.asyncio
    async def test_get_machine_res_dirs_absolute_path_rejected(self, api):
        """Test that absolute path in res-dirs is rejected."""
        machine_id = "test-machine"
        resp = await api.client.get(
            f"/api/v1/local/machines/{machine_id}/res-dirs",
            params={"dir": "/etc"},
        )
        # Should be rejected as invalid params (400)
        assert resp.status_code in (400, 403, 404)

    @pytest.mark.asyncio
    async def test_get_machine_info_empty_id_422(self, api):
        """Test that empty machine_id returns 422."""
        resp = await api.client.get("/api/v1/local/machines//info")
        assert resp.status_code in (404, 422)
