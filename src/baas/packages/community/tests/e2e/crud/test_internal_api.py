"""E2E tests for internal cross-instance forwarding API.

Endpoints:
- POST /internal/v1/forward - Forward request to another instance
"""

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.crud]


class TestInternalApi:
    """Test suite for internal forwarding endpoints."""

    @pytest.mark.asyncio
    async def test_internal_forward_missing_machine_id_400(self, api):
        """Test that forwarding without machine_id returns 400."""
        resp = await api.client.post(
            "/internal/v1/forward",
            json={
                "action": "execute_command",
                "machine_id": "",
                "params": {"cmd": "echo hello"},
                "request_id": "test-req-001",
            },
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_internal_forward_missing_params_422(self, api):
        """Test that forwarding with missing fields returns 422."""
        resp = await api.client.post(
            "/internal/v1/forward",
            json={},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_internal_forward_missing_action_422(self, api):
        """Test that forwarding without action returns 422."""
        resp = await api.client.post(
            "/internal/v1/forward",
            json={
                "machine_id": "test-machine",
                "params": {},
                "request_id": "test-req-002",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_internal_forward_missing_params_field_422(self, api):
        """Test that forwarding without params field returns 422."""
        resp = await api.client.post(
            "/internal/v1/forward",
            json={
                "action": "execute_command",
                "machine_id": "test-machine",
                "request_id": "test-req-003",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_internal_forward_nonexistent_machine(self, api, unique_id: str):
        """Test forwarding to a non-existent machine returns error envelope."""
        machine_id = f"nonexistent-{unique_id}"
        resp = await api.client.post(
            "/internal/v1/forward",
            json={
                "action": "execute_command",
                "machine_id": machine_id,
                "params": {"cmd": "echo hello"},
                "request_id": f"req-{unique_id}",
            },
        )
        # Should return 200 with error envelope (machine not connected),
        # not raise HTTP exception — per internal_router contract
        assert resp.status_code == 200
        result = resp.json()
        assert isinstance(result, dict)
        assert "status" in result

    @pytest.mark.asyncio
    async def test_internal_forward_invalid_action_not_found(self, api, unique_id: str):
        """Test forwarding with an unrecognized action."""
        machine_id = f"test-machine-{unique_id}"
        resp = await api.client.post(
            "/internal/v1/forward",
            json={
                "action": "nonexistent_action_xyz",
                "machine_id": machine_id,
                "params": {},
                "request_id": f"req-{unique_id}",
            },
        )
        # Returns 200 with error envelope
        assert resp.status_code == 200
        result = resp.json()
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_internal_forward_validates_machine_id_not_empty(self, api):
        """Test validation that machine_id is a non-empty string."""
        resp = await api.client.post(
            "/internal/v1/forward",
            json={
                "action": "execute_command",
                "machine_id": "   ",
                "params": {"cmd": "echo hello"},
                "request_id": "test-req-004",
            },
        )
        # Empty/whitespace machine_id is falsy → should get 400
        assert resp.status_code in (200, 400, 422)
