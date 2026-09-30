"""DRM concern — test binding (NoopDRMReader)."""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.drm import DRMReaderPlugin


class TestDRMModule(Module):
    """test: NoopDRMReader (offline double ⇒ flags unset)."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.local.drm import NoopDRMReader

        binder.bind(DRMReaderPlugin, to=NoopDRMReader, scope=singleton)
