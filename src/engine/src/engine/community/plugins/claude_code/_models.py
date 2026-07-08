"""_ModelsPortMixin — model + provider enumeration (relay RPC)."""
from __future__ import annotations

import logging

log = logging.getLogger("claude-code-community-port")


class _ModelsPortMixin:
    """Domain mixin: models.list + providers.list."""

    async def models_list(self, token: str | None = None) -> list[dict]:
        resp = await (await self._relay()).send_request("models.list", {}, timeout=5.0)
        if not resp.ok or not resp.payload:
            return []
        payload = resp.payload
        entries = payload.get("models") if isinstance(payload, dict) else payload
        return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []

    async def models_list_providers(self, token: str | None = None) -> list[dict]:
        resp = await (await self._relay()).send_request("providers.list", {})
        if not resp.ok:
            return []
        data = resp.payload or {}
        entries = data.get("providers") if isinstance(data, dict) else data
        return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []
