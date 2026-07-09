"""Local ``DRMReaderPlugin`` — no DRM center in offline/test mode.

``read`` returns ``None`` for every key ⇒ all flags read as their built-in
default, matching the pre-seam local behavior exactly.
"""
from __future__ import annotations

from agentclaw.community.plugin_api.drm import DRMReaderPlugin
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(mode=Mode.LOCAL, flavor=Flavor.NOOP, rationale="no DRM center offline")
class NoopDRMReader(MockSeam, DRMReaderPlugin):
    """Test/offline double: DRM is unavailable."""

    def read(self, drm_id: str) -> str | None:
        return None
