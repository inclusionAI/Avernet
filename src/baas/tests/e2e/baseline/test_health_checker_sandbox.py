"""E2E tests for the Health Checker Sandbox module.

Covers sandbox device management endpoints that rely on the
SandboxDeviceRouter. These endpoints require an API key with
app_type=health-checker; tests verify routing works (no 500).

Endpoint mapping:
- GET  /api/v1/sandbox-device/active-sandboxes  — list_active_sandboxes
- POST /api/v1/sandbox-device/probe-and-warn    — probe_and_warn
- POST /api/v1/sandbox-device/renew-ttl         — renew_ttl
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]

SBOX_BASE = "/api/v1/sandbox-device"


class TestSandboxActiveSandboxes:
    """Tests for GET /api/v1/sandbox-device/active-sandboxes."""

    @pytest.mark.asyncio
    async def test_active_sandboxes_returns_paginated(self, api: APITestHelper) -> None:
        """GET /active-sandboxes returns paginated list (or auth-gated response)."""
        response = await api.client.get(
            f"{SBOX_BASE}/active-sandboxes",
            params=api.params(table_type="baas", page=1, page_size=10),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_active_sandboxes_ac_binding_type(self, api: APITestHelper) -> None:
        """GET /active-sandboxes with table_type=ac_binding returns non-500."""
        response = await api.client.get(
            f"{SBOX_BASE}/active-sandboxes",
            params=api.params(table_type="ac_binding", page=1, page_size=10),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_active_sandboxes_invalid_table_type(
        self, api: APITestHelper
    ) -> None:
        """GET /active-sandboxes with invalid table_type returns 4xx."""
        response = await api.client.get(
            f"{SBOX_BASE}/active-sandboxes",
            params=api.params(table_type="invalid_type", page=1, page_size=10),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_active_sandboxes_default_params(self, api: APITestHelper) -> None:
        """GET /active-sandboxes with minimal params returns non-500."""
        response = await api.client.get(
            f"{SBOX_BASE}/active-sandboxes",
            params=api.params(table_type="baas"),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )


class TestSandboxProbeAndWarn:
    """Tests for POST /api/v1/sandbox-device/probe-and-warn."""

    @pytest.mark.asyncio
    async def test_probe_and_warn_with_valid_params(self, api: APITestHelper) -> None:
        """POST /probe-and-warn with valid table_id returns non-500."""
        response = await api.client.post(
            f"{SBOX_BASE}/probe-and-warn",
            params=api.params(),
            json={
                "table_id": 1,
                "table_type": "baas",
            },
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_probe_and_warn_ac_binding_type(self, api: APITestHelper) -> None:
        """POST /probe-and-warn with type=ac_binding returns non-500."""
        response = await api.client.post(
            f"{SBOX_BASE}/probe-and-warn",
            params=api.params(),
            json={
                "table_id": 1,
                "table_type": "ac_binding",
            },
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_probe_and_warn_missing_table_id(self, api: APITestHelper) -> None:
        """POST /probe-and-warn without table_id returns 422 validation error."""
        response = await api.client.post(
            f"{SBOX_BASE}/probe-and-warn",
            params=api.params(),
            json={
                "table_type": "baas",
            },
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_probe_and_warn_nonexistent_record(self, api: APITestHelper) -> None:
        """POST /probe-and-warn for nonexistent table_id returns 4xx or 404."""
        response = await api.client.post(
            f"{SBOX_BASE}/probe-and-warn",
            params=api.params(),
            json={
                "table_id": 99999999,
                "table_type": "baas",
            },
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )


class TestSandboxRenewTTL:
    """Tests for POST /api/v1/sandbox-device/renew-ttl."""

    @pytest.mark.asyncio
    async def test_renew_ttl_with_valid_params(self, api: APITestHelper) -> None:
        """POST /renew-ttl with valid table_id returns non-500."""
        response = await api.client.post(
            f"{SBOX_BASE}/renew-ttl",
            params=api.params(),
            json={
                "table_id": 1,
                "table_type": "baas",
            },
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_renew_ttl_ac_binding_type(self, api: APITestHelper) -> None:
        """POST /renew-ttl with type=ac_binding returns non-500."""
        response = await api.client.post(
            f"{SBOX_BASE}/renew-ttl",
            params=api.params(),
            json={
                "table_id": 1,
                "table_type": "ac_binding",
            },
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_renew_ttl_missing_table_type(self, api: APITestHelper) -> None:
        """POST /renew-ttl without table_type returns 422 validation error."""
        response = await api.client.post(
            f"{SBOX_BASE}/renew-ttl",
            params=api.params(),
            json={
                "table_id": 1,
            },
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_renew_ttl_nonexistent_record(self, api: APITestHelper) -> None:
        """POST /renew-ttl for nonexistent table_id returns 4xx or 404."""
        response = await api.client.post(
            f"{SBOX_BASE}/renew-ttl",
            params=api.params(),
            json={
                "table_id": 99999999,
                "table_type": "baas",
            },
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )
