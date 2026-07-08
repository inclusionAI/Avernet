"""Local ``EngineExtClient`` — Noop (no external engine; empty payload)."""
from __future__ import annotations

from typing import Any

from agentclaw.community.plugin_api.engine_ext_client import EngineExtClient
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.NOOP,
    rationale="No external engine in local/dev; engine_ext is empty.",
)
class LocalEngineExtClient(MockSeam, EngineExtClient):
    """Returns an empty ``engine_ext`` — local/dev has no engine to ask."""

    def fetch(self, bot: dict[str, Any]) -> dict[str, Any]:
        return {}
