"""OpenClawPortBase — shared plumbing for the OpenClaw port implementation.

Provides __init__, pool property, _pooled_client, and _default_client.
All per-domain mixin classes inherit (transitively) from this base via
OpenClawPluginImpl's MRO.
"""
from __future__ import annotations

import logging

from engine.community.openclaw.client.gateway_client import (
    OpenClawGatewayClient,
    get_client,
)
from engine.community.plugins.openclaw.token_pool import TokenClientPool

log = logging.getLogger("openclaw-port")


class OpenClawPortBase:
    """Shared gateway plumbing: client singleton + token pool."""

    def __init__(
        self,
        client: OpenClawGatewayClient | None = None,
        pool: TokenClientPool | None = None,
    ) -> None:
        self._client = client
        self._pool = pool if pool is not None else TokenClientPool()
        # Lifetime cache: `providers.available` is global config (doesn't vary
        # per tenant), so we build the map at most once per process lifetime.
        self._model_provider_map: dict[str, str] | None = None

    @property
    def pool(self) -> TokenClientPool:
        """The token pool — the assembled engine forwards connection
        register/release here."""
        return self._pool

    async def _pooled_client(self, token: str | None) -> OpenClawGatewayClient:
        """Per-token routed client (session/chat/cron/relay/approval)."""
        return await self._pool.get(token)

    async def _default_client(self) -> OpenClawGatewayClient:
        """Default client (models/node — token-agnostic).

        Prefers the injected client if present and connected (supports
        test injection); otherwise falls back to the connection pool.
        """
        if self._client is not None and self._client.connected:
            return self._client
        return await get_client()
