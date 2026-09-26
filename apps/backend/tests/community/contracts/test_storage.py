"""Rule 25 conformance — StoragePlugin (trivial suite).

``StoragePlugin`` is currently a placeholder Protocol with no methods —
its only consumer (``core/harness/services/bot_profile.py``) only
mentions it in a docstring. The local impl ``LocalFsStorage`` is
likewise a 10-line stub.

Until the Protocol grows a real surface, the conformance suite asserts
only that DI resolves it to something assignable to the Protocol type.
When real methods land, this file must grow real consumer-level
assertions per the Rule 25 spec.
"""
from __future__ import annotations

from agentclaw.community.plugin_api.storage import StoragePlugin


def test_storage_plugin_resolves_via_di(world) -> None:
    plugin = world.get(StoragePlugin)
    # Structural check — the local impl satisfies the (currently empty)
    # Protocol surface.
    assert isinstance(plugin, StoragePlugin)
