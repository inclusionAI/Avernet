"""StoragePlugin — blob/object storage interface (placeholder).

Current implementations:
- ``agentclaw.infrastructure.oss_storage`` (prod / OSS)
- ``agentclaw.infrastructure.local_fs_storage`` (local FS)
"""

from typing import Protocol
from agentclaw.community.plugin_api.base import Plugin


class StoragePlugin(Plugin, Protocol):
    """Put/get/delete blobs by key."""
    ...
