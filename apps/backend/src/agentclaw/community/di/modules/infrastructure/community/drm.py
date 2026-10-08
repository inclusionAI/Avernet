"""DRM concern — community binding (CommunityDRMReader)."""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.drm import DRMReaderPlugin


class CommunityDRMModule(Module):
    """community: CommunityDRMReader (no DRM center ⇒ flags unset)."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.community.drm import CommunityDRMReader

        binder.bind(DRMReaderPlugin, to=CommunityDRMReader, scope=singleton)
