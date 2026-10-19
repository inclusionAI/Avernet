"""Fixed-target transport contract; no engine or restart policy in this module."""
from unittest.mock import Mock

import pytest

from agentclaw.community.core.devices.errors import DeviceServiceError
from agentclaw.community.core.devices.models import AllocatedDevice
from agentclaw.community.core.devices.services.device_service_router import DeviceServiceRouter


def make_router():
    provider = Mock()
    repo = Mock()
    router = DeviceServiceRouter(
        repository=repo, bot_query=Mock(), providers={"local": provider},
        default_provider_key="local",
    )
    return router, provider, repo


def test_transport_snapshots_target_without_binding_lookup():
    router, provider, repo = make_router()
    device = AllocatedDevice(device_id="logical", device_provider="local",
                             device_props={"physical_id": "old"})
    seen = []

    def execute(*, device, shell_cmd):
        seen.append((device.device_props["physical_id"], shell_cmd))
        device.device_props["physical_id"] = "provider-mutation"
        return "result"

    provider._exec_shell_new.side_effect = execute
    bound = router.bind_device_executor(device=device)
    device.device_props["physical_id"] = "caller-mutation"
    assert bound("first") == "result"
    assert bound("second") == "result"
    assert seen == [("old", "first"), ("old", "second")]
    assert repo.mock_calls == []


def test_unknown_provider_does_not_use_default():
    router, provider, repo = make_router()
    with pytest.raises(DeviceServiceError):
        router.bind_device_executor(device=AllocatedDevice(
            device_id="logical", device_provider="unknown", device_props={},
        ))
    provider._exec_shell_new.assert_not_called()
    assert repo.mock_calls == []


def test_transport_propagates_command_failure_without_policy():
    router, provider, _ = make_router()
    error = TimeoutError("unavailable")
    provider._exec_shell_new.side_effect = error
    bound = router.bind_device_executor(device=AllocatedDevice(
        device_id="logical", device_provider="local", device_props={},
    ))
    with pytest.raises(TimeoutError) as caught:
        bound("command")
    assert caught.value is error
