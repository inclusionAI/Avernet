"""Contract tests for local Arca storage cleanup implementations."""

import pytest

from secbaas.plugins.sandbox.arca import (
    LocalProcessArcaSandboxPlugin,
    StubArcaSandboxPlugin,
)
from secbaas.plugins.sandbox.arca.local_docker import LocalDockerArcaSandboxPlugin


@pytest.mark.parametrize(
    "plugin_factory",
    [
        StubArcaSandboxPlugin,
        LocalDockerArcaSandboxPlugin,
        LocalProcessArcaSandboxPlugin,
    ],
)
def test_delete_storage_is_successful_noop(plugin_factory):
    """Local plugins have no remote NAS resource, so cleanup succeeds as a no-op."""
    plugin = plugin_factory()

    assert plugin.delete_storage("storage-abc", "tenant-a") is True
