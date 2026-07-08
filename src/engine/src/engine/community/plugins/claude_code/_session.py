"""_SessionPortMixin — session CRUD + history (relay RPC, single-tenant)."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("claude-code-community-port")


class _SessionPortMixin:
    """Domain mixin: sessions.{list,patch,delete,reset} + chat.history."""

    async def sessions_list(
        self,
        token: str | None = None,
        offset: int = 0,
        limit: int = 50,
        agent_id: str | None = None,
    ) -> list[dict]:
        client = await self._relay()
        try:
            resp = await client.send_request("sessions.list", {})
        except Exception as e:  # noqa: BLE001
            log.error("[sessions_list] RPC failed: %s", e)
            return []
        if not resp.ok:
            log.error("[sessions_list] failed: %s",
                      resp.error.message if resp.error else "unknown")
            return []
        payload = resp.payload or []
        if isinstance(payload, dict):
            sessions = payload.get("sessions", [])
        elif isinstance(payload, list):
            sessions = payload
        else:
            return []
        sessions = [s for s in sessions if isinstance(s, dict)]
        # agent_id filter (relay doesn't filter server-side for this field).
        if agent_id is not None:
            sessions = [s for s in sessions if s.get("agentId") == agent_id]
        return sessions[offset: offset + limit]

    async def session_create(
        self,
        key: str,
        label: str | None = None,
        model: str | None = None,
        cwd: str | None = None,
        token: str | None = None,
    ) -> dict:
        client = await self._relay()
        params: dict[str, Any] = {
            "key": key,
            "permissionMode": "bypassPermissions",
        }
        if label is not None:
            params["label"] = label
        if model is not None:
            params["model"] = model
        if cwd is not None:
            params["cwd"] = cwd
        resp = await client.send_request("sessions.patch", params, timeout=60.0)
        if not resp.ok:
            raise RuntimeError(
                f"Failed to create session: "
                f"{resp.error.message if resp.error else 'unknown'}"
            )
        return resp.payload if isinstance(resp.payload, dict) else {"key": key}

    async def session_delete(self, key: str, token: str | None = None) -> bool:
        client = await self._relay()
        try:
            resp = await client.send_request(
                "sessions.delete", {"key": key, "force": True})
        except Exception as e:  # noqa: BLE001
            log.error("[session_delete] RPC failed: %s", e)
            return False
        return bool(resp.ok)

    async def session_reset(self, key: str, token: str | None = None) -> dict:
        return await self._reset_inband(key, "sessionKey")

    async def session_clear(self, key: str, token: str | None = None) -> dict:
        return await self._reset_inband(key, "key")

    async def _reset_inband(self, key: str, param_name: str) -> dict:
        """``sessions.reset`` — mirror the corp key-name inconsistency:
        ``session_reset`` uses ``sessionKey``, ``session_clear`` uses ``key``."""
        try:
            client = await self._relay()
        except Exception as e:  # noqa: BLE001
            return {"success": False,
                    "error": {"code": "INTERNAL_ERROR", "message": str(e)}}
        try:
            resp = await client.send_request(
                "sessions.reset", {param_name: key}, timeout=15.0)
        except Exception as e:  # noqa: BLE001
            log.exception("[sessions.reset] RPC failed: %s", e)
            return {"success": False,
                    "error": {"code": "INTERNAL_ERROR", "message": str(e)}}
        if resp.ok:
            return {"success": True, "payload": resp.payload or {}}
        err = resp.error
        return {"success": False,
                "error": {"code": err.code if err else "UNKNOWN",
                          "message": err.message if err else "Unknown error"}}

    async def session_get_history(
        self,
        key: str,
        limit: int = 100,
        token: str | None = None,
    ) -> list[dict]:
        client = await self._relay()
        try:
            resp = await client.send_request(
                "chat.history", {"sessionKey": key, "limit": limit})
        except Exception as e:  # noqa: BLE001
            log.error("[session_get_history] RPC failed: %s", e)
            return []
        if not resp.ok:
            return []
        payload = resp.payload or []
        if isinstance(payload, dict):
            messages = payload.get("messages", [])
        elif isinstance(payload, list):
            messages = payload
        else:
            return []
        return [m for m in messages if isinstance(m, dict)]
