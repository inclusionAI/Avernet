"""End-to-end coverage for the admin device-repair endpoint.

Repairs a LIVE device record through
``POST /api/v1/admin/devices/{device_uuid}/repair`` against the real DI
container and its in-memory SQLite database:

- status and provider binding written together, one call
- provider binding only, status untouched
- status only, provider binding preserved
- unknown / lowercase status rejected (422) with the record untouched
- over-long provider binding rejected (422)
- the operator identity is stamped as the row's ``modifier``
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


class TestAdminDeviceRepair:
    """Repair of a live device record: status, provider binding, operator stamp."""

    def _device_repo(self):
        from secbaas.community.bootstrap import get_container

        return get_container().repository.device_repository()

    def _insert_device(
        self,
        *,
        device_uuid: str,
        tenant: str,
        status: str = "PENDING",
        provider_device_id: str | None = "sandbox-old",
        modifier: str = "seed-user",
    ) -> None:
        self._device_repo().insert_device(
            device_uuid=device_uuid,
            tenant=tenant,
            env="dev",
            domain="e2e_test_domain",
            creator="e2e-test",
            modifier=modifier,
            status=status,
            provider_type="arca",
            provider_device_id=provider_device_id,
        )

    def _read(self, device_uuid: str):
        record = self._device_repo().get_by_device_uuid_only(device_uuid)
        assert record is not None
        return record

    @pytest.mark.asyncio
    async def test_status_and_provider_device_id_in_one_call(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """200: status and provider binding both written, response reports both."""
        device_uuid = f"dev-e2e-repair-{unique_id}"
        self._insert_device(device_uuid=device_uuid, tenant=api.tenant)

        response = await api.client.post(
            f"/api/v1/admin/devices/{device_uuid}/repair",
            params=api.params(),
            json={
                "status": "ACTIVE",
                "provider_device_id": "sandbox-new",
                "operator": "ops-alice",
            },
        )

        assert response.status_code == 200, response.text[:300]
        data = response.json()["data"]
        assert data["device_uuid"] == device_uuid
        assert data["previous_status"] == "PENDING"
        assert data["new_status"] == "ACTIVE"
        assert data["previous_provider_device_id"] == "sandbox-old"
        assert data["new_provider_device_id"] == "sandbox-new"

        record = self._read(device_uuid)
        assert record.status == "ACTIVE"
        assert record.provider_device_id == "sandbox-new"
        assert record.provider_type == "arca"

    @pytest.mark.asyncio
    async def test_provider_device_id_only_leaves_status_unchanged(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """200: provider binding only, status untouched."""
        device_uuid = f"dev-e2e-repair-{unique_id}"
        self._insert_device(device_uuid=device_uuid, status="ACTIVE", tenant=api.tenant)

        response = await api.client.post(
            f"/api/v1/admin/devices/{device_uuid}/repair",
            params=api.params(),
            json={"provider_device_id": "sandbox-rebound", "operator": "ops-alice"},
        )

        assert response.status_code == 200, response.text[:300]
        data = response.json()["data"]
        assert data["previous_status"] == "ACTIVE"
        assert data["new_status"] == "ACTIVE"
        assert data["new_provider_device_id"] == "sandbox-rebound"

        record = self._read(device_uuid)
        assert record.status == "ACTIVE"
        assert record.provider_device_id == "sandbox-rebound"

    @pytest.mark.asyncio
    async def test_status_only_preserves_provider_device_id(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """200: status only, stored provider binding preserved."""
        device_uuid = f"dev-e2e-repair-{unique_id}"
        self._insert_device(
            device_uuid=device_uuid,
            provider_device_id="sandbox-keep",
            tenant=api.tenant,
        )

        response = await api.client.post(
            f"/api/v1/admin/devices/{device_uuid}/repair",
            params=api.params(),
            json={"status": "FAILED", "operator": "ops-alice"},
        )

        assert response.status_code == 200, response.text[:300]
        data = response.json()["data"]
        assert data["previous_provider_device_id"] == "sandbox-keep"
        assert data["new_provider_device_id"] == "sandbox-keep"

        record = self._read(device_uuid)
        assert record.status == "FAILED"
        assert record.provider_device_id == "sandbox-keep"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_status", ["active", "BOGUS", "UP"])
    async def test_unknown_status_rejected_and_device_unchanged(
        self, api: APITestHelper, unique_id: str, bad_status: str
    ) -> None:
        """422: status outside DeviceStatus leaves the record untouched."""
        device_uuid = f"dev-e2e-repair-{unique_id}"
        self._insert_device(device_uuid=device_uuid, tenant=api.tenant)

        response = await api.client.post(
            f"/api/v1/admin/devices/{device_uuid}/repair",
            params=api.params(),
            json={
                "status": bad_status,
                "provider_device_id": "sandbox-ignored",
                "operator": "ops-bob",
            },
        )

        assert response.status_code == 422, response.text[:300]

        record = self._read(device_uuid)
        assert record.status == "PENDING"
        assert record.provider_device_id == "sandbox-old"
        assert record.modifier == "seed-user"

    @pytest.mark.asyncio
    async def test_overlong_provider_device_id_rejected(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """422: provider_device_id beyond the column width is rejected."""
        device_uuid = f"dev-e2e-repair-{unique_id}"
        self._insert_device(device_uuid=device_uuid, tenant=api.tenant)

        response = await api.client.post(
            f"/api/v1/admin/devices/{device_uuid}/repair",
            params=api.params(),
            json={"provider_device_id": "x" * 129, "operator": "ops-bob"},
        )

        assert response.status_code == 422, response.text[:300]

        record = self._read(device_uuid)
        assert record.provider_device_id == "sandbox-old"
        assert record.modifier == "seed-user"

    @pytest.mark.asyncio
    async def test_modifier_stamped_with_operator(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """200: the operator identity becomes the row's modifier."""
        device_uuid = f"dev-e2e-repair-{unique_id}"
        self._insert_device(
            device_uuid=device_uuid, modifier="seed-user", tenant=api.tenant
        )

        response = await api.client.post(
            f"/api/v1/admin/devices/{device_uuid}/repair",
            params=api.params(),
            json={"status": "UPDATING", "operator": "ops-carol"},
        )

        assert response.status_code == 200, response.text[:300]

        record = self._read(device_uuid)
        assert record.modifier == "ops-carol"
        assert record.status == "UPDATING"
