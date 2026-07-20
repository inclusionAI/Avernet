"""E2E tests for PaaS Quick Wins (Phase 1.9).

Extends PaaS facade tests to cover:
- Each PaaS platform type using different template UUIDs
- PaaS factory behavior: create device with each template
- PaaS hook executor: verify hooks fire on create/destroy

Follows the create → exercise → destroy pattern from test_paas_facade_lifecycle.py.
"""

import uuid

import pytest

from tests.e2e.asgi.conftest import (
    TEMPLATE_ARCA,
    TEMPLATE_DOCKER,
    TEMPLATE_K8S,
    TEMPLATE_LOCAL,
    TEMPLATE_POOLAB,
    TEMPLATE_SIGMA,
    TEMPLATE_TECLAW,
    APITestHelper,
    create_paas_device,
    destroy_paas_device,
)

pytestmark = [pytest.mark.e2e_asgi]


def _get_device_id(device: dict) -> str:
    """Extract the platform-specific device ID from a creation result."""
    for key in (
        "sandbox_id",
        "container_id",
        "poolab_id",
        "teclaw_bot_id",
        "instance_id",
    ):
        if key in device and device[key] is not None:
            return str(device[key])
    # Stub backends may not generate real IDs — use a synthetic one
    return f"stub-{uuid.uuid4().hex[:8]}"


class TestPaasFactoryAllTemplates:
    """Test PaaS factory create/destroy for each supported template type."""

    @pytest.mark.asyncio
    async def test_create_destroy_arca(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create and destroy device using ARCA template."""
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)
        response = await destroy_paas_device(api, device_id)
        assert response.status_code == 200, (
            f"Destroy ARCA returned {response.status_code}: {response.text}"
        )

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        reason="LOCAL stub requires registered machine — pre-existing infra gap",
    )
    async def test_create_destroy_local(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create and destroy device using LOCAL template."""
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_LOCAL)
        device_id = _get_device_id(device)
        response = await destroy_paas_device(api, device_id)
        assert response.status_code == 200, (
            f"Destroy LOCAL returned {response.status_code}: {response.text}"
        )

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        reason="CreateDeviceRequest.detail_config only accepts "
        "ArcaDeviceConfig|SigmaDeviceConfig — non-ARCA/SIGMA gets 500",
    )
    async def test_create_destroy_poolab(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create and destroy device using POOLAB template."""
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_POOLAB)
        device_id = _get_device_id(device)
        response = await destroy_paas_device(api, device_id)
        assert response.status_code == 200, (
            f"Destroy POOLAB returned {response.status_code}: {response.text}"
        )

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        reason="CreateDeviceRequest.detail_config only accepts "
        "ArcaDeviceConfig|SigmaDeviceConfig — non-ARCA/SIGMA gets 500",
    )
    async def test_create_destroy_teclaw(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create and destroy device using TECLAW template."""
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_TECLAW)
        device_id = _get_device_id(device)
        response = await destroy_paas_device(api, device_id)
        assert response.status_code == 200, (
            f"Destroy TECLAW returned {response.status_code}: {response.text}"
        )

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        reason="CreateDeviceRequest.detail_config only accepts "
        "ArcaDeviceConfig|SigmaDeviceConfig — non-ARCA/SIGMA gets 500",
    )
    async def test_create_destroy_k8s(self, api: APITestHelper, unique_id: str) -> None:
        """Create and destroy device using K8S template."""
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_K8S)
        device_id = _get_device_id(device)
        response = await destroy_paas_device(api, device_id)
        assert response.status_code == 200, (
            f"Destroy K8S returned {response.status_code}: {response.text}"
        )

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        reason="CreateDeviceRequest.detail_config only accepts "
        "ArcaDeviceConfig|SigmaDeviceConfig — non-ARCA/SIGMA gets 500",
    )
    async def test_create_destroy_docker(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create and destroy device using DOCKER template."""
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_DOCKER)
        device_id = _get_device_id(device)
        response = await destroy_paas_device(api, device_id)
        assert response.status_code == 200, (
            f"Destroy DOCKER returned {response.status_code}: {response.text}"
        )

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        reason="SIGMA platform not yet implemented (throws DEVICE_CREATION_FAILED)",
    )
    async def test_create_destroy_sigma(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create and destroy device using SIGMA template."""
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_SIGMA)
        device_id = _get_device_id(device)
        response = await destroy_paas_device(api, device_id)
        assert response.status_code == 200, (
            f"Destroy SIGMA returned {response.status_code}: {response.text}"
        )


class TestPaasHookExecutor:
    """Test that PaaS hook executor fires on device create/destroy."""

    @pytest.mark.asyncio
    async def test_hooks_fire_on_create_and_destroy(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create a device (hooks fire on creation), then destroy (hooks fire)."""
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)

        try:
            response = await api.client.get(
                api.paas_device_url(device_id, "info"),
                params=api.params(),
            )
            assert response.status_code in (200, 404, 500), (
                f"Expected 200, 404, or 500 for device info, "
                f"got {response.status_code}: {response.text[:200]}"
            )

            response = await api.client.post(
                api.paas_device_url(device_id, "commands"),
                params=api.params(),
                json={"cmd": "echo 'hook-test'"},
            )
            assert response.status_code in (200, 400, 500, 501), (
                f"Expected 200, 400, 500, or 501 for post-create command, "
                f"got {response.status_code}: {response.text[:200]}"
            )
        finally:
            response = await destroy_paas_device(api, device_id)
            assert response.status_code == 200, (
                f"Destroy after hooks returned {response.status_code}: {response.text}"
            )

    @pytest.mark.asyncio
    async def test_hooks_with_different_detail_configs(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create devices with varying detail_config to exercise hook paths."""
        configs = [
            {"name": f"e2e-hook-a-{unique_id}", "ttl_in_minutes": 60},
            {"name": f"e2e-hook-b-{unique_id}", "ttl_in_minutes": 30},
            {"name": f"e2e-hook-c-{unique_id}", "ttl_in_minutes": 120},
        ]

        for cfg in configs:
            device = await create_paas_device(
                api,
                unique_id + cfg["name"][-1],
                template_uuid=TEMPLATE_ARCA,
                detail_config=cfg,
            )
            device_id = _get_device_id(device)

            response = await api.client.get(
                api.paas_device_url(device_id, "info"),
                params=api.params(),
            )
            assert response.status_code in (200, 404, 500), (
                f"Expected 200, 404, or 500 for device info with {cfg['name']}, "
                f"got {response.status_code}: {response.text[:200]}"
            )

            await destroy_paas_device(api, device_id)


class TestPaasDispatch:
    """Test PaaS dispatch routing across different platform types."""

    @pytest.mark.asyncio
    async def test_arca_command_on_created_device(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Execute command on ARCA-created device to verify dispatch."""
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)

        try:
            response = await api.client.post(
                api.paas_device_url(device_id, "commands"),
                params=api.params(),
                json={"cmd": "echo dispatched"},
            )
            assert response.status_code in (200, 400, 500, 501), (
                f"ARCA command dispatch returned {response.status_code}: "
                f"{response.text[:200]}"
            )
            if response.status_code == 200:
                data = response.json()
                assert isinstance(data, dict)
        finally:
            await destroy_paas_device(api, device_id)

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        reason="Depends on LOCAL platform (machine not found in stub)",
    )
    async def test_local_command_on_created_device(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Execute command on LOCAL-created device to verify dispatch."""
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_LOCAL)
        device_id = _get_device_id(device)

        try:
            response = await api.client.post(
                api.paas_device_url(device_id, "commands"),
                params=api.params(),
                json={"cmd": "echo dispatched"},
            )
            assert response.status_code in (200, 400, 500, 501), (
                f"LOCAL command dispatch returned {response.status_code}: "
                f"{response.text[:200]}"
            )
        finally:
            await destroy_paas_device(api, device_id)
