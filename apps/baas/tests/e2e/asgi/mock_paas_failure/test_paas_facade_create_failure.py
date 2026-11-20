from __future__ import annotations

import pytest

from secbaas.community.api.device_manage import ArcaDeviceConfig, DeviceFacadeException
from secbaas.community.bootstrap import get_container

pytestmark = [pytest.mark.mock_paas_create_failure]


def _set_mock_failure(monkeypatch: pytest.MonkeyPatch, env_var: str) -> None:
    monkeypatch.setenv("PAAS_MOCK_MODE", "true")
    monkeypatch.setenv(env_var, "true")


class TestPaasFacadeCreateFailure:
    @pytest.mark.asyncio
    async def test_create_device_raises_facade_exception_with_mock_create_failure(
        self, api, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        """PAAS_MOCK_CREATE_FAILURE causes in-process facade.create_device to raise DeviceFacadeException."""
        _set_mock_failure(monkeypatch, "PAAS_MOCK_CREATE_FAILURE")
        facade = get_container().services.paas_facade()
        with pytest.raises(DeviceFacadeException):
            await facade.create_device(
                tenant_name="team_claw",
                device_template_uuid="TEMPLATE-4d0e2849d7004111836333de782b95d8",
                detail_config=ArcaDeviceConfig(
                    name=f"fail-device-{unique_id}",
                    ttl_in_minutes=60,
                ),
            )

    @pytest.mark.asyncio
    async def test_create_device_without_template_uuid_raises_facade_exception(
        self, api, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        """PAAS_MOCK_CREATE_FAILURE with omitted template_uuid still raises DeviceFacadeException."""
        _set_mock_failure(monkeypatch, "PAAS_MOCK_CREATE_FAILURE")
        facade = get_container().services.paas_facade()
        with pytest.raises(DeviceFacadeException):
            await facade.create_device(
                tenant_name="team_claw",
                detail_config=ArcaDeviceConfig(
                    name=f"fail-device-{unique_id}",
                    ttl_in_minutes=60,
                ),
            )
