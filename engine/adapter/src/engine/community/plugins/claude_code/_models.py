"""_ModelsPortMixin — model + provider enumeration and default model (relay RPC)."""
from __future__ import annotations

import logging

from engine.community.core.models.exceptions import DefaultModelNotConfiguredError

log = logging.getLogger("claude-code-community-port")


class _ModelsPortMixin:
    """Domain mixin: models.list + providers.list + models.default."""

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

    async def models_default(self, token: str | None = None) -> dict:
        resp = await (await self._relay()).send_request("models.default", {}, timeout=5.0)
        if not resp.ok or not isinstance(resp.payload, dict):
            detail = resp.error.message if resp.error else "no payload"
            raise DefaultModelNotConfiguredError(
                f"relay did not answer models.default: {detail}"
            )
        model = resp.payload.get("model")
        if not isinstance(model, str) or not model.strip():
            raise DefaultModelNotConfiguredError(
                "relay answered models.default without a model id"
            )
        return dict(resp.payload)
