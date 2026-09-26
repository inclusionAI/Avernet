"""DRMReaderPlugin — read a dynamic-remote-config (DRM) flag value.

Corp reads feature flags / rollout config from the AntGroup DRM config center
(pushed dynamic config). Community / test have no such center, so their impls
return ``None`` and every caller falls back to its own built-in default (NAS off,
BCN-register off, rollout disabled).

The value is an opaque raw string keyed by an opaque DRM id; callers own the
parse + default. Injected into the components that read flags (Rule 14 / Rule 20)
— the vendor coupling stays inside the corp impl.

Implementations:
- ``plugins.prod.drm.LayottoDRMReader`` (AntGroup DRM via layotto)
- ``plugins.community.drm.CommunityDRMReader`` (no center ⇒ ``None``)
- ``plugins.local.drm.NoopDRMReader`` (test double ⇒ ``None``)
"""
from __future__ import annotations

from typing import Protocol

from agentclaw.community.plugin_api.base import Plugin


class DRMReaderPlugin(Plugin, Protocol):
    """Read a raw DRM flag value by its config id."""

    def read(self, drm_id: str) -> str | None:
        """Return the raw DRM value for ``drm_id``.

        Returns ``None`` when the key is unset or the config center is
        unavailable; callers treat ``None`` as "use the built-in default".
        """
        ...
