"""Community ``DRMReaderPlugin`` — no dynamic config center.

A real, deployable impl for the open-source build: the community deployment has
no AntGroup DRM center, so every flag reads as unset (``None``) and each caller
uses its built-in default (NAS off, BCN-register off, rollout disabled). Not a
``MockSeam`` — bound directly by ``CommunityDRMModule``.
"""
from __future__ import annotations

from agentclaw.community.plugin_api.drm import DRMReaderPlugin


class CommunityDRMReader(DRMReaderPlugin):
    """No DRM center: every flag is unset."""

    def read(self, drm_id: str) -> str | None:
        return None
