from __future__ import annotations

import pytest

from secbaas.community.api.device_manage import ErrorCode, PaasError
from secbaas.community.bootstrap import get_container
from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.mock_paas_create_failure]


def _set_mock_failure(monkeypatch: pytest.MonkeyPatch, env_var: str) -> None:
    monkeypatch.setenv("PAAS_MOCK_MODE", "true")
    monkeypatch.setenv(env_var, "true")


class TestPaasFacadeCreateFailure:
    """Test PaaS facade create_device failure via in-process direct call.

    Migrated from HTTP POST /api/v1/paas/devices to direct facade invocation.
    ``PAAS_MOCK_CREATE_FAILURE=true`` causes ``create_device()`` to raise
    ``PaasError(DEVICE_CREATION_FAILED)``.
    """

    @pytest.mark.asyncio
    async def test_create_device_raises_with_mock_create_failure(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        """Facade.create_device raises PaasError(DEVICE_CREATION_FAILED) with mock."""
        _set_mock_failure(monkeypatch, "PAAS_MOCK_CREATE_FAILURE")
        facade = get_container().services.paas_facade()

        with pytest.raises(PaasError) as exc_info:
            await facade.create_device(
                tenant_name=api.tenant,
                device_template_uuid="TEMPLATE-4d0e2849d7004111836333de782b95d8",
                detail_config={
                    "name": f"fail-device-{unique_id}",
                    "ttl_in_minutes": 60,
                },
            )
        assert exc_info.value.code == ErrorCode.DEVICE_CREATION_FAILED

    @pytest.mark.asyncio
    async def test_create_device_without_template_uuid_raises(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        """Facade.create_device without template UUID also raises with mock failure."""
        _set_mock_failure(monkeypatch, "PAAS_MOCK_CREATE_FAILURE")
        facade = get_container().services.paas_facade()

        with pytest.raises(PaasError) as exc_info:
            await facade.create_device(
                tenant_name=api.tenant,
                detail_config={
                    "name": f"fail-device-{unique_id}",
                    "ttl_in_minutes": 60,
                },
            )
        assert exc_info.value.code == ErrorCode.DEVICE_CREATION_FAILED
