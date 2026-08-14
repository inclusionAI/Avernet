"""E2E tests for Sigma PaaS stub — all operations return errors or NotImplemented.

Sigma integration is not yet implemented. All operations are expected to return
PaasError with specific error codes. These tests verify the error paths are
exercised and return proper HTTP responses.

IMPORTANT: Sigma has template_id=7 in the seed data. All device IDs must use
the suffix @7 to route through the SigmaPaasService factory, not @0 which
falls back to MockPaasService (ARCA).
"""

import pytest

from tests.e2e.asgi.conftest import TEMPLATE_SIGMA, APITestHelper

pytestmark = [pytest.mark.e2e_asgi]

# Sigma template has id=7 in the seed data — required to route through SigmaPaasService
_SIGMA_DEVICE_ID = "sigma-test-@7"
_SIGMA_DEVICE_ID_NS = "sigma-nonexistent@7"


class TestSigmaCreateDestroy:
    """Sigma create returns DEVICE_CREATION_FAILED, destroy returns DEVICE_DESTROY_FAILED."""

    @pytest.mark.asyncio
    async def test_create_sigma_returns_error(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        resp = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "tenant_name": api.tenant,
                "device_template_uuid": TEMPLATE_SIGMA,
                "detail_config": {
                    "name": f"sigma-test-{unique_id}",
                    "ttl_in_minutes": 60,
                },
            },
        )
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_destroy_sigma_returns_error(self, api: APITestHelper) -> None:
        resp = await api.client.delete(
            api.paas_device_url(_SIGMA_DEVICE_ID_NS),
            params=api.params(),
        )
        assert resp.status_code in (404, 500)


class TestSigmaWsInfo:
    @pytest.mark.asyncio
    async def test_ws_info_not_implemented(self, api: APITestHelper) -> None:
        resp = await api.client.get(
            api.paas_device_url(_SIGMA_DEVICE_ID, "ws-info"),
            params=api.params(port=9222, path="/devtools"),
        )
        assert resp.status_code in (404, 500, 501)


class TestSigmaCommands:
    @pytest.mark.asyncio
    async def test_execute_command_returns_error(self, api: APITestHelper) -> None:
        resp = await api.client.post(
            api.paas_device_url(_SIGMA_DEVICE_ID, "commands"),
            params=api.params(),
            json={"cmd": "echo hello"},
        )
        assert resp.status_code in (404, 500)


class TestSigmaDeviceInfo:
    @pytest.mark.asyncio
    async def test_device_info_returns_error(self, api: APITestHelper) -> None:
        resp = await api.client.get(
            api.paas_device_url(_SIGMA_DEVICE_ID, "info"),
            params=api.params(),
        )
        assert resp.status_code in (404, 500)


class TestSigmaOutboundRule:
    @pytest.mark.asyncio
    async def test_outbound_rule_returns_error(self, api: APITestHelper) -> None:
        resp = await api.client.put(
            api.paas_device_url(_SIGMA_DEVICE_ID, "outbound-rule"),
            params=api.params(),
            json={
                "header_operation_rules": [
                    {
                        "domains": ["*"],
                        "action": "ALLOW",
                        "header_name": "x-test",
                        "value": "1",
                    }
                ]
            },
        )
        assert resp.status_code in (404, 500, 501, 503)


class TestSigmaInvokeHttp:
    @pytest.mark.asyncio
    async def test_invoke_http_not_implemented(self, api: APITestHelper) -> None:
        resp = await api.client.get(
            api.paas_device_url(_SIGMA_DEVICE_ID) + "/invoke-http/8080/health",
            params=api.params(),
        )
        assert resp.status_code in (404, 500, 501)


class TestSigmaTtlAndOpenFolder:
    @pytest.mark.asyncio
    async def test_ttl_not_implemented(self, api: APITestHelper) -> None:
        resp = await api.client.put(
            api.paas_device_url(_SIGMA_DEVICE_ID, "ttl"),
            params=api.params(),
        )
        assert resp.status_code in (404, 500, 501)

    @pytest.mark.asyncio
    async def test_open_folder_not_implemented(self, api: APITestHelper) -> None:
        resp = await api.client.post(
            api.paas_device_url(_SIGMA_DEVICE_ID, "open-folder"),
            params=api.params(),
            json={"folder_path": "/tmp"},
        )
        assert resp.status_code in (404, 500, 501)
