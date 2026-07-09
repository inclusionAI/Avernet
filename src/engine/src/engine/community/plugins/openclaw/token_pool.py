"""TokenClientPool — per-MCP-token OpenClawGatewayClient pool (leaf copy).

The F2 ACL copy of ``engines/openclaw/token_pool.py``, with one change: the
public surface takes ``token: str | None`` instead of the core
``AuthContext`` (the leaf rule forbids ``plugins → core``, and the pool only
ever read ``auth.token``). The engine's ``on_connection_open/close(auth)`` stay
core/engine-side and forward ``auth.token`` here.

Semantics are otherwise identical to the legacy pool:
  - Each distinct MCP token gets its own ``OpenClawGatewayClient`` built with the
    token-forwarding upstream header.
  - Inbound connections sharing a token share one upstream client.
  - No token / forward disabled → the module-level default singleton.
  - ``register`` / ``release`` refcount inbound connections; the per-token client
    is disconnected and dropped when the count hits zero.

(The legacy pool stays live for the un-converted ``engines/openclaw`` path until
the Group-E cutover; both are deleted together in Group F.)
"""
from __future__ import annotations

import asyncio
import logging

from engine.community.config import MCPTokenSettings, load_mcp_token_settings
from engine.community.openclaw.client.gateway_client import (
    OpenClawGatewayClient,
    get_client,
)
from engine.community.shared.mcp_token import build_upstream_headers

log = logging.getLogger("openclaw-pool")


class TokenClientPool:
    """Per-MCP-token OpenClaw gateway client pool (token-keyed, leaf-safe)."""

    def __init__(self, settings: MCPTokenSettings | None = None) -> None:
        self._settings = settings or load_mcp_token_settings()
        self._clients: dict[str, OpenClawGatewayClient] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._refcount: dict[str, int] = {}

    def _lock_for(self, token: str) -> asyncio.Lock:
        """Idempotently fetch the per-token lock (``setdefault`` is atomic
        within one asyncio tick)."""
        return self._locks.setdefault(token, asyncio.Lock())

    async def get(self, token: str | None = None) -> OpenClawGatewayClient:
        """Return (lazily connecting) the right client for this token.

        ``token`` is the routing key. When no token is available or
        ``forward_to_wss`` is disabled, returns the module-level default
        singleton — same fallback as the legacy server.
        """
        upstream_headers = build_upstream_headers(token, self._settings)

        if token and upstream_headers:
            async with self._lock_for(token):
                client = self._clients.get(token)
                if client is None:
                    client = OpenClawGatewayClient(upstream_headers=upstream_headers)
                    self._clients[token] = client
                if not client.connected:
                    await client.connect()
                return client

        # No token / forward disabled — shared default singleton.
        return await get_client()

    def register(self, token: str | None) -> None:
        """Record an inbound connection using this token. No-op when ``token``
        is falsy. Pure synchronous dict mutation; no lock needed."""
        if not token:
            return
        self._refcount[token] = self._refcount.get(token, 0) + 1

    async def release(self, token: str | None) -> None:
        """Record an inbound connection leaving; disconnect + drop the per-token
        client when its refcount hits zero. No-op when ``token`` is falsy.

        Mutates per-token state inside the lock; runs ``disconnect()`` outside it
        so it can't stall a concurrent ``get()`` on a freshly-registered token.
        """
        if not token:
            return

        client: OpenClawGatewayClient | None = None

        async with self._lock_for(token):
            count = self._refcount.get(token, 0) - 1
            if count > 0:
                self._refcount[token] = count
                return
            self._refcount.pop(token, None)
            client = self._clients.pop(token, None)
            self._locks.pop(token, None)

        if client is None:
            return
        try:
            await client.disconnect()
        except Exception as e:
            log.warning(f"Disconnect token client failed (token={token!r}): {e}")

    async def shutdown(self) -> None:
        """Disconnect every tracked client. Called by the assembled engine's
        ``shutdown()``."""
        clients = list(self._clients.items())
        self._clients.clear()
        self._locks.clear()
        self._refcount.clear()
        for token, client in clients:
            try:
                await client.disconnect()
            except Exception as e:
                log.warning(f"Pool shutdown: disconnect failed (token={token!r}): {e}")


__all__ = ["TokenClientPool"]
