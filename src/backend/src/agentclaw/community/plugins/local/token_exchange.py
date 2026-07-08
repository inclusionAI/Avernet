"""LocalTokenExchangePlugin — returns a mock access token for local dev."""
from __future__ import annotations

from typing import Any

from fastapi import Request

from agentclaw.community.plugin_api.token_exchange import TokenExchangePlugin
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="no I/O",
)
class LocalTokenExchangePlugin(MockSeam, TokenExchangePlugin):
    """Local impl: ignores cookies, returns a fixed mock token.

    The local OpenClaw Gateway runs with ``auth.mode="none"``, so nothing
    validates the token — any opaque string suffices.
    """

    async def exchange_from_request(self, request: Request) -> dict[str, Any]:
        return {"access_token": "mock_access_token"}
