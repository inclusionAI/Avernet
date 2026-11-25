"""Lifecycle observations must not require a RUNNING sandbox connection."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from secbaas.community.api.device_manage import ArcaCredentials, ErrorCode, PaasError
from secbaas.community.core.service.paas import ArcaPaasService
from secbaas.community.spi.sandbox.arca import (
    ArcaSandboxError,
    ArcaSandboxInfo,
    ArcaSandboxNotFoundError,
)


class InspectablePlugin:
    def __init__(self, status="DESTROYED", error=None):
        self.status = status
        self.error = error
        self.connect_sync_sandbox = Mock(
            side_effect=ArcaSandboxError("sandbox is not running")
        )

    def get_sandbox_info(self, sandbox_id):
        if self.error is not None:
            raise self.error
        return ArcaSandboxInfo(
            sandbox_id=sandbox_id, status=self.status, template_id="template-test"
        )


def service(plugin):
    return ArcaPaasService(
        credentials=ArcaCredentials(
            base_url="https://example.invalid",
            api_key="test-key",
            template_id=1,
            template_uuid="template-test",
        ),
        arca_sandbox_plugin=plugin,
    )


@pytest.mark.parametrize(
    "status", ["DESTROYED", "RUNNING", "PAUSED", "PENDING", "FAILED"]
)
def test_inspection_does_not_require_running_connection(status):
    plugin = InspectablePlugin(status)
    result = service(plugin)._get_device_info_sync("sandbox-test")
    assert result.status == status
    assert result.sandbox_id == "sandbox-test"
    plugin.connect_sync_sandbox.assert_not_called()


@pytest.mark.parametrize(
    "error,code",
    [
        (ArcaSandboxNotFoundError("missing"), ErrorCode.DEVICE_NOT_FOUND),
        (ArcaSandboxError("connection failed"), ErrorCode.PLATFORM_UNAVAILABLE),
        (OSError("network failed"), ErrorCode.DEVICE_UNAVAILABLE),
    ],
)
def test_inspection_preserves_absence_vs_unavailability(error, code):
    plugin = InspectablePlugin(error=error)
    with pytest.raises(PaasError) as caught:
        service(plugin)._get_device_info_sync("sandbox-test")
    assert caught.value.code == code
    plugin.connect_sync_sandbox.assert_not_called()


def test_legacy_plugin_keeps_existing_connection_contract():
    info = ArcaSandboxInfo(
        sandbox_id="sandbox-test", status="RUNNING", template_id="template-test"
    )
    plugin = SimpleNamespace(
        connect_sync_sandbox=Mock(return_value=SimpleNamespace(get_info=lambda: info))
    )
    assert service(plugin)._get_device_info_sync("sandbox-test").status == "RUNNING"
    plugin.connect_sync_sandbox.assert_called_once_with("sandbox-test")
