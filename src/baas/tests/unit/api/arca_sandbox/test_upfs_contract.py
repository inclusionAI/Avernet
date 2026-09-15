"""All community ARCA plugins must honor or explicitly reject UPFS mounts."""

from importlib import import_module
from inspect import signature

import pytest

from secbaas.community.api.device_manage import VolumeMountSpec
from secbaas.community.spi.sandbox.arca import ArcaSandboxPlugin


@pytest.mark.parametrize(
    "module,class_name,supports_upfs",
    [
        ("_stub", "StubArcaSandboxPlugin", True),
        ("aliyun_ack._sandbox_plugin", "AliyunAckSandboxPlugin", False),
        ("local_docker._sandbox_plugin", "LocalDockerArcaSandboxPlugin", False),
        ("local_proc._sandbox_plugin", "LocalProcessArcaSandboxPlugin", False),
    ],
)
def test_upfs_plugin_contract(module, class_name, supports_upfs):
    cls = getattr(
        import_module(f"secbaas.community.plugins.sandbox.arca.{module}"), class_name
    )
    contract = signature(ArcaSandboxPlugin.create_sync_sandbox)
    implementation = signature(cls.create_sync_sandbox)
    assert set(contract.parameters) <= set(implementation.parameters)
    assert implementation.parameters["volume_mounts"].default is None
    mounts = [
        VolumeMountSpec(
            volume_id="volume", subpath="bot/device", mount_path="/home/admin"
        )
    ]
    if supports_upfs:
        plugin = cls()
        sandbox = plugin.create_sync_sandbox("template", volume_mounts=mounts)
        assert sandbox.volume_mounts == mounts
        assert plugin.connect_sync_sandbox(sandbox.sandbox_id) is sandbox
        assert plugin.create_sync_sandbox("template").volume_mounts == []
    else:
        # Rejection must precede *any* client/process/filesystem setup or usage.
        plugin = cls.__new__(cls)
        with pytest.raises(NotImplementedError, match="UPFS"):
            plugin.create_sync_sandbox("template", volume_mounts=mounts)
