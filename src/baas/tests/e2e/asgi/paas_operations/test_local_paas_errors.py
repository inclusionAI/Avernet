"""E2E tests for PaaS service layer error paths (Phase 3.3)."""

import uuid

import pytest

from tests.e2e.asgi.conftest import (
    TEMPLATE_LOCAL,
    APITestHelper,
    create_paas_device,
    destroy_paas_device,
)

pytestmark = [pytest.mark.paas_operations]


class TestLocalPaasErrors:
    @pytest.mark.asyncio
    async def test_create_with_invalid_detail_config(self, api: APITestHelper) -> None:
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "tenant_name": api.tenant,
                "device_template_uuid": TEMPLATE_LOCAL,
                "detail_config": "not-a-dict",
            },
        )
        assert response.status_code >= 400
        body = response.json()
        assert "error" in body or "detail" in body or "message" in body

    @pytest.mark.asyncio
    async def test_create_when_stub_reports_failure(self, api: APITestHelper) -> None:
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "tenant_name": api.tenant,
                "device_template_uuid": "TEMPLATE-ffffffffffffffffffffffffffffffff",
                "detail_config": {},
            },
        )
        assert response.status_code >= 400
        body = response.json()
        assert "error" in body or "detail" in body or "message" in body

    @pytest.mark.asyncio
    async def test_destroy_nonexistent_device(self, api: APITestHelper) -> None:
        fake_id = f"nonexistent-{uuid.uuid4().hex[:8]}"
        response = await api.client.delete(
            api.paas_device_url(fake_id),
            params=api.params(),
        )
        assert response.status_code in (404, 422, 200)

    @pytest.mark.asyncio
    async def test_scale_beyond_limits(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id)
        try:
            response = await api.client.post(
                api.paas_device_url(device.get("device_id"), "scale"),
                params=api.params(),
                json={"replicas": 999999},
            )
            assert response.status_code >= 400
            body = response.json()
            assert "error" in body or "detail" in body or "message" in body
        finally:
            await destroy_paas_device(api, device.get("device_id"))

    @pytest.mark.asyncio
    async def test_command_on_nonexistent_device(self, api: APITestHelper) -> None:
        fake_id = f"nonexistent-{uuid.uuid4().hex[:8]}"
        response = await api.client.post(
            api.paas_device_url(fake_id, "command"),
            params=api.params(),
            json={"command": "echo hello"},
        )
        assert response.status_code >= 400

    @pytest.mark.asyncio
    async def test_create_with_empty_tenant_name(self, api: APITestHelper) -> None:
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "tenant_name": "",
                "device_template_uuid": TEMPLATE_LOCAL,
                "detail_config": {},
            },
        )
        assert response.status_code >= 400

    @pytest.mark.asyncio
    async def test_create_with_missing_tenant_name(self, api: APITestHelper) -> None:
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "device_template_uuid": TEMPLATE_LOCAL,
                "detail_config": {},
            },
        )
        assert response.status_code >= 400

    @pytest.mark.asyncio
    async def test_get_info_nonexistent_device(self, api: APITestHelper) -> None:
        fake_id = f"nonexistent-{uuid.uuid4().hex[:8]}"
        response = await api.client.get(
            api.paas_device_url(fake_id, "info"),
            params=api.params(),
        )
        assert response.status_code in (200, 404, 500)
