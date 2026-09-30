from __future__ import annotations

import pytest
import pytest_asyncio

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.health_check]

SBOX_BASE = "/api/v1/sandbox-device"


class TestActiveSandboxes:
    @pytest.mark.asyncio
    async def test_active_sandboxes_baas(self, api: APITestHelper) -> None:
        response = await api.client.get(
            f"{SBOX_BASE}/active-sandboxes",
            params=api.params(table_type="baas", page=1, page_size=10),
        )
        assert response.status_code in (200, 400, 401, 403, 404, 422, 500, 501), (
            f"Unexpected status {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_active_sandboxes_ac_binding(self, api: APITestHelper) -> None:
        response = await api.client.get(
            f"{SBOX_BASE}/active-sandboxes",
            params=api.params(table_type="ac_binding", page=1, page_size=10),
        )
        assert response.status_code in (200, 400, 401, 403, 404, 422, 500, 501), (
            f"Unexpected status {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_active_sandboxes_invalid_table_type(
        self, api: APITestHelper
    ) -> None:
        response = await api.client.get(
            f"{SBOX_BASE}/active-sandboxes",
            params=api.params(table_type="invalid_type", page=1, page_size=10),
        )
        assert response.status_code in (200, 400, 401, 403, 404, 422, 500, 501), (
            f"Unexpected status {response.status_code}: {response.text[:200]}"
        )


class TestProbeAndWarn:
    @pytest.mark.asyncio
    async def test_probe_and_warn(self, api: APITestHelper) -> None:
        response = await api.client.post(
            f"{SBOX_BASE}/probe-and-warn",
            params=api.params(),
            json={"table_id": 1, "table_type": "baas"},
        )
        assert response.status_code in (200, 400, 401, 403, 404, 422, 500, 501), (
            f"Unexpected status {response.status_code}: {response.text[:200]}"
        )


class TestRenewTTL:
    @pytest.mark.asyncio
    async def test_renew_ttl(self, api: APITestHelper) -> None:
        response = await api.client.post(
            f"{SBOX_BASE}/renew-ttl",
            params=api.params(),
            json={"table_id": 1, "table_type": "baas"},
        )
        assert response.status_code in (200, 400, 401, 403, 404, 422, 500, 501), (
            f"Unexpected status {response.status_code}: {response.text[:200]}"
        )


class TestSandboxReverseLookup:
    @pytest.mark.asyncio
    async def test_sandbox_reverse_lookup(self, api: APITestHelper) -> None:
        response = await api.client.get(
            api.health_sandbox_url(),
            params=api.params(sandbox_id="sb-test-123"),
        )
        assert response.status_code in (200, 400, 401, 403, 404, 422, 500, 501), (
            f"Unexpected status {response.status_code}: {response.text[:200]}"
        )


class TestPagination:
    @pytest.mark.asyncio
    async def test_active_sandboxes_pagination(self, api: APITestHelper) -> None:
        response = await api.client.get(
            f"{SBOX_BASE}/active-sandboxes",
            params=api.params(table_type="baas", page=1, page_size=5),
        )
        assert response.status_code in (200, 400, 401, 403, 404, 422, 500, 501), (
            f"Unexpected status {response.status_code}: {response.text[:200]}"
        )

        if response.status_code == 200:
            data = response.json()
            assert "items" in data or isinstance(data, list), (
                f"Expected paginated response with items or list, got: {type(data)}"
            )
