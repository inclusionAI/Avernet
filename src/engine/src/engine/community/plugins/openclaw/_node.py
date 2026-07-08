"""_NodePortMixin — node_list port method."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("openclaw-port")


class _NodePortMixin:
    """Domain mixin: node listing (token-agnostic, default client)."""

    async def node_list(self) -> list[dict[str, Any]]:
        """Raw node payload dicts from the `node.list` RPC; `[]` on failure.

        Relocated intact from `engines/openclaw/node.py:list_nodes` up to the
        raw-payload extraction; the `_to_node` DTO build + filter/paging moved to
        `core/adapters/openclaw/node.py`.
        """
        try:
            client = await self._default_client()
            resp = await client.send_request("node.list", {})
        except ConnectionError as e:
            log.error(f"[node_list] connection failed: {e}")
            return []

        if not resp.ok:
            err = resp.error.message if resp.error else "Unknown error"
            log.error(f"[node_list] node.list RPC failed: {err}")
            return []

        payload = resp.payload or {}
        if isinstance(payload, dict):
            raw = payload.get("nodes", [])
        elif isinstance(payload, list):
            raw = payload
        else:
            log.warning(f"[node_list] unexpected payload: {type(payload).__name__}")
            return []
        return list(raw)
