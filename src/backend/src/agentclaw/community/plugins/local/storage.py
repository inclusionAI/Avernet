"""LocalFsStorage — local-mode StoragePlugin (placeholder).

Target source: ``agentclaw.infrastructure.local_fs_storage``.
"""
from agentclaw.community.plugin_api.storage import StoragePlugin
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.STUB,
    rationale="10-line placeholder; no methods implemented",
)
class LocalFsStorage(MockSeam, StoragePlugin):
    """Stub StoragePlugin implementation for local mode."""
    pass
