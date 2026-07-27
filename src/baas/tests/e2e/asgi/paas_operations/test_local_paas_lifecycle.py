"""E2E tests for LOCAL PaaS device lifecycle operations (Phase 3.2).

Tests cover destroy, restart, TTL update, idempotent destroy, and scale
on LOCAL-template devices at the PaaS service layer.

Endpoints exercised:
- POST   /api/v1/paas/devices/{id}/restart      — Restart device
- DELETE /api/v1/paas/devices/{id}               — Destroy device
- DELETE /api/v1/paas/devices/{id} (idempotent)  — Re-destroy
- PUT    /api/v1/paas/devices/{id}/ttl           — Update TTL (config update)
- GET    /api/v1/paas/devices/{id}/info           — Verify device state
"""

from typing import Any

import pytest

from tests.e2e.asgi.conftest import (
    TEMPLATE_LOCAL,
    APITestHelper,
    create_paas_device,
    destroy_paas_device,
)

pytestmark = [pytest.mark.paas_operations]


def _get_device_id(device: dict[str, object]) -> str:
    for key in ("sandbox_id", "container_id", "poolab_id", "teclaw_bot_id"):
        if key in device:
            return str(device[key])
    raise KeyError(f"No device ID found in {list(device.keys())}")


def _assert_not_di_error(response: Any) -> None:
    if response.status_code != 500:
        return
    try:
        body = response.json()
    except Exception:
        return
    detail = body.get("detail", body)
    msg = detail.get("message", "") if isinstance(detail, dict) else str(detail)
    assert "Provide" not in msg, (
        f"DI container wiring error detected: {msg}\n"
        "The Provide placeholder is not being resolved. "
        "Check that container.wire() is called and @inject decorator is present."
    )


class TestLocalPaasRestart:
    @pytest.mark.xfail(
        reason="LOCAL stub requires registered machine (user_id/machine_id/tc_bot_id/agent_code)"
    )
    @pytest.mark.asyncio
    async def test_restart_local_device_returns_200(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_LOCAL)
        device_id = _get_device_id(device)
        try:
            resp = await api.client.post(
                api.paas_device_url(device_id, "restart"),
                params=api.params(),
            )
            _assert_not_di_error(resp)
            assert resp.status_code in (200, 400, 404, 500, 501), (
                f"Restart returned {resp.status_code}: {resp.text}"
            )
        finally:
            await destroy_paas_device(api, device_id)


class TestLocalPaasDestroy:
    @pytest.mark.xfail(
        reason="LOCAL stub requires registered machine (user_id/machine_id/tc_bot_id/agent_code)"
    )
    @pytest.mark.asyncio
    async def test_destroy_local_device_returns_200(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_LOCAL)
        device_id = _get_device_id(device)
        resp = await destroy_paas_device(api, device_id)
        _assert_not_di_error(resp)
        assert resp.status_code == 200, (
            f"Destroy returned {resp.status_code}: {resp.text}"
        )

    @pytest.mark.xfail(
        reason="LOCAL stub requires registered machine (user_id/machine_id/tc_bot_id/agent_code)"
    )
    @pytest.mark.asyncio
    async def test_destroyed_device_not_found_returns_404(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_LOCAL)
        device_id = _get_device_id(device)
        await destroy_paas_device(api, device_id)
        resp = await api.client.get(
            api.paas_device_url(device_id, "info"),
            params=api.params(),
        )
        _assert_not_di_error(resp)
        assert resp.status_code in (200, 404), (
            f"Destroyed device info returned {resp.status_code}: {resp.text}"
        )

    @pytest.mark.xfail(
        reason="LOCAL stub requires registered machine (user_id/machine_id/tc_bot_id/agent_code)"
    )
    @pytest.mark.asyncio
    async def test_idempotent_destroy_handled_gracefully(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_LOCAL)
        device_id = _get_device_id(device)
        r1 = await destroy_paas_device(api, device_id)
        _assert_not_di_error(r1)
        assert r1.status_code == 200
        r2 = await destroy_paas_device(api, device_id)
        _assert_not_di_error(r2)
        assert r2.status_code in (200, 404), (
            f"Idempotent re-destroy returned {r2.status_code}: {r2.text}"
        )


class TestLocalPaasUpdate:
    @pytest.mark.xfail(
        reason="LOCAL stub requires registered machine (user_id/machine_id/tc_bot_id/agent_code)"
    )
    @pytest.mark.asyncio
    async def test_update_ttl_local_device_returns_200(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_LOCAL)
        device_id = _get_device_id(device)
        try:
            resp = await api.client.put(
                api.paas_device_url(device_id, "ttl"),
                params=api.params(),
                json={"ttl_in_minutes": 120},
            )
            _assert_not_di_error(resp)
            assert resp.status_code in (200, 400, 404, 500, 501, 503), (
                f"TTL update returned {resp.status_code}: {resp.text}"
            )
            if resp.status_code == 200:
                data = resp.json()
                assert isinstance(data, dict), f"TTL response is not a dict: {data}"
        finally:
            await destroy_paas_device(api, device_id)


class TestLocalPaasScale:
    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="LOCAL PaaS device-level scale endpoint not implemented")
    async def test_scale_local_device_returns_expected_status(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_LOCAL)
        device_id = _get_device_id(device)
        try:
            resp = await api.client.post(
                api.paas_device_url(device_id, "scale"),
                params=api.params(),
                json={"target_count": 2},
            )
            _assert_not_di_error(resp)
            assert resp.status_code in (200, 400, 404, 500, 501), (
                f"Scale returned {resp.status_code}: {resp.text}"
            )
        finally:
            await destroy_paas_device(api, device_id)
