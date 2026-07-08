"""_CommandsPortMixin — slash-command enumeration (relay RPC)."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("claude-code-community-port")


class _CommandsPortMixin:
    """Domain mixin: commands.{list,get}."""

    async def commands_list(self, scope: str | None = None,
                            token: str | None = None) -> list[dict]:
        params: dict[str, Any] = {}
        if scope is not None:
            params["scope"] = scope
        resp = await (await self._relay()).send_request("commands.list", params)
        if not resp.ok:
            return []
        payload = resp.payload or {}
        cmds = payload.get("commands", []) if isinstance(payload, dict) else payload
        return [c for c in cmds if isinstance(c, dict)] if isinstance(cmds, list) else []

    async def commands_get(self, command_id: str,
                           token: str | None = None) -> dict | None:
        # corp: key is ``name`` when command_id starts with "/", else ``id``.
        key = "name" if command_id.startswith("/") else "id"
        resp = await (await self._relay()).send_request(
            "commands.get", {key: command_id})
        if not resp.ok or not resp.payload:
            return None
        return resp.payload if isinstance(resp.payload, dict) else None
