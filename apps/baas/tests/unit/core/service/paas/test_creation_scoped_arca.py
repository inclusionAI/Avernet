"""Replacement defers NAS retirement only for an explicitly capable provider."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.api.device_manage import ArcaCredentials, PaasError
from secbaas.community.api.device_manage.creation_context import (
    DeviceCreationContext,
    device_creation_scope,
)
from secbaas.community.core.service.paas import ArcaPaasService
from secbaas.community.core.service.paas._arca_paas_service import (
    ArcaPaasService as LegacyService,
)
from secbaas.community.core.service.paas._factory import (
    ArcaPaasService as FactoryService,
)
from secbaas.community.spi.sandbox.arca import (
    ArcaSandboxError,
    ArcaSandboxNotFoundError,
)


@pytest.mark.parametrize("capable", [True, False, None])
@pytest.mark.parametrize("storage_type", ["nas", "local", None])
def test_scoped_retirement_requires_capability_and_known_nas(capable, storage_type):
    plugin = MagicMock()
    plugin.supports_nas_replacement_recovery = capable
    plugin.connect_sync_sandbox.return_value.get_info.return_value = SimpleNamespace(
        storage={"type": storage_type}
    )
    service = ArcaPaasService(
        ArcaCredentials(
            base_url="http://fixture",
            api_key="fixture",
            tenant_name="t",
            template_id=1,
            template_uuid="fixture",
        ),
        plugin,
    )
    context = DeviceCreationContext("t", "test", "d", "op", "old@1")
    with patch.object(
        LegacyService, "_destroy_device_sync", return_value=True
    ) as legacy:
        with device_creation_scope(context):
            if capable is True and storage_type is None:
                with pytest.raises(ArcaSandboxError):
                    service._destroy_device_sync("old")
                legacy.assert_not_called()
            else:
                assert service._destroy_device_sync("old") is True
                assert legacy.call_count == (
                    0 if capable is True and storage_type == "nas" else 1
                )
    plugin.delete_storage.assert_not_called()
    plugin.connect_sync_sandbox.return_value.destroy.assert_not_called()
    assert FactoryService is ArcaPaasService


def test_scoped_identity_mismatch_never_destroys():
    plugin = MagicMock(supports_nas_replacement_recovery=True)
    service = ArcaPaasService(
        ArcaCredentials(
            base_url="http://fixture",
            api_key="fixture",
            tenant_name="t",
            template_id=1,
            template_uuid="fixture",
        ),
        plugin,
    )
    with device_creation_scope(DeviceCreationContext("t", "test", "d", "op", "old@1")):
        with pytest.raises(ArcaSandboxError):
            service._destroy_device_sync("new")
    plugin.connect_sync_sandbox.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [True, False])
async def test_scoped_query_failure_preserves_paas_contract(missing):
    plugin = MagicMock(supports_nas_replacement_recovery=True)
    plugin.connect_sync_sandbox.side_effect = (
        ArcaSandboxNotFoundError("sandbox not found")
        if missing
        else RuntimeError("query failed")
    )
    service = ArcaPaasService(
        ArcaCredentials(
            base_url="http://fixture",
            api_key="fixture",
            tenant_name="t",
            template_id=1,
            template_uuid="fixture",
        ),
        plugin,
    )
    with device_creation_scope(DeviceCreationContext("t", "test", "d", "op", "old@1")):
        if missing:
            assert await service.destroy_device("old") is True
        else:
            with pytest.raises(PaasError):
                await service.destroy_device("old")
    plugin.delete_storage.assert_not_called()
