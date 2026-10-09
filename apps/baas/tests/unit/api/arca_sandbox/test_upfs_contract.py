"""Preserve the common plugin signature; UPFS validation belongs to BaaS/SDK."""

from importlib import import_module
from inspect import signature

import pytest

from secbaas.community.spi.sandbox.arca import ArcaSandboxPlugin


@pytest.mark.parametrize(
    "module,class_name",
    [
        ("_stub", "StubArcaSandboxPlugin"),
        ("aliyun_ack._sandbox_plugin", "AliyunAckSandboxPlugin"),
        ("local_docker._sandbox_plugin", "LocalDockerArcaSandboxPlugin"),
        ("local_proc._sandbox_plugin", "LocalProcessArcaSandboxPlugin"),
    ],
)
def test_existing_plugin_signature_is_unchanged(module, class_name):
    cls = getattr(
        import_module(f"secbaas.community.plugins.sandbox.arca.{module}"), class_name
    )
    contract = signature(ArcaSandboxPlugin.create_sync_sandbox)
    implementation = signature(cls.create_sync_sandbox)
    assert set(contract.parameters) <= set(implementation.parameters)
    assert "upfs_volume_id" not in implementation.parameters
    assert implementation.parameters["storage"].default is None
