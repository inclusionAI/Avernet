"""_McpPortMixin — MCP server management + tool/resource/prompt calls (relay RPC)."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("claude-code-community-port")


class _McpPortMixin:
    """Domain mixin: mcp.config.* / mcp.tools.* / mcp.resources.* / mcp.prompts.*
    / mcp.server.* / mcp.filter_servers."""

    async def mcp_list_servers(self, token: str | None = None) -> list[dict]:
        resp = await (await self._relay()).send_request("mcp.config.list", {})
        if not resp.ok:
            return []
        payload = resp.payload or {}
        servers = payload.get("servers", []) if isinstance(payload, dict) else payload
        return [s for s in servers if isinstance(s, dict)] if isinstance(servers, list) else []

    async def mcp_get_server(self, server_code: str,
                             token: str | None = None) -> dict | None:
        resp = await (await self._relay()).send_request(
            "mcp.config.get", {"serverCode": server_code})
        if not resp.ok or not resp.payload:
            return None
        return resp.payload if isinstance(resp.payload, dict) else None

    async def mcp_create_server(self, config: dict,
                                token: str | None = None) -> dict:
        resp = await (await self._relay()).send_request(
            "mcp.config.create", config)
        if not resp.ok:
            msg = resp.error.message if resp.error else "unknown"
            if resp.error and ("already exists" in msg or "已存在" in msg):
                raise FileExistsError(msg)
            raise RuntimeError(f"mcp.config.create failed: {msg}")
        return resp.payload if isinstance(resp.payload, dict) else config

    async def mcp_update_server(self, server_code: str, patch: dict,
                                token: str | None = None) -> dict:
        params = dict(patch)
        params["serverCode"] = server_code
        resp = await (await self._relay()).send_request("mcp.config.update", params)
        if not resp.ok:
            msg = resp.error.message if resp.error else "unknown"
            raise RuntimeError(f"mcp.config.update failed: {msg}")
        return resp.payload if isinstance(resp.payload, dict) else params

    async def mcp_delete_server(self, server_code: str,
                                token: str | None = None) -> bool:
        resp = await (await self._relay()).send_request(
            "mcp.config.delete", {"serverCode": server_code})
        if not resp.ok:
            return False
        payload = resp.payload
        return bool(payload.get("deleted") if isinstance(payload, dict) else True)

    async def _mcp_server_lifecycle(self, method: str, server_code: str) -> dict:
        resp = await (await self._relay()).send_request(
            method, {"serverCode": server_code})
        if resp.ok:
            return {"success": True, "payload": resp.payload or {}}
        err = resp.error
        return {"success": False,
                "error": {"code": err.code if err else "UNKNOWN",
                          "message": err.message if err else "Unknown error"}}

    async def mcp_start_server(self, server_code: str,
                               token: str | None = None) -> dict:
        return await self._mcp_server_lifecycle("mcp.server.start", server_code)

    async def mcp_stop_server(self, server_code: str,
                              token: str | None = None) -> dict:
        return await self._mcp_server_lifecycle("mcp.server.stop", server_code)

    async def mcp_restart_server(self, server_code: str,
                                 token: str | None = None) -> dict:
        return await self._mcp_server_lifecycle("mcp.server.restart", server_code)

    async def mcp_get_server_status(self, server_code: str,
                                    token: str | None = None) -> dict:
        resp = await (await self._relay()).send_request(
            "mcp.server.status", {"serverCode": server_code})
        return resp.payload if isinstance(resp.payload, dict) else {}

    async def mcp_list_tools(self, server_code: str,
                             token: str | None = None) -> list[dict]:
        resp = await (await self._relay()).send_request(
            "mcp.tools.list", {"serverCode": server_code})
        if not resp.ok:
            return []
        payload = resp.payload or {}
        tools = payload.get("tools", []) if isinstance(payload, dict) else payload
        return [t for t in tools if isinstance(t, dict)] if isinstance(tools, list) else []

    async def mcp_call_tool(
        self,
        server_code: str,
        tool_name: str,
        arguments: dict | None = None,
        token: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict:
        params: dict[str, Any] = {"toolName": tool_name, "arguments": arguments or {}}
        if server_code:
            params["serverCode"] = server_code
        timeout = (timeout_ms / 1000.0) if timeout_ms else 60.0
        resp = await (await self._relay()).send_request(
            "mcp.tools.call", params, timeout=timeout)
        if resp.ok:
            return {"success": True, "payload": resp.payload or {}}
        err = resp.error
        return {"success": False,
                "error": {"code": err.code if err else "UNKNOWN",
                          "message": err.message if err else "Unknown error"}}

    async def mcp_list_resources(self, server_code: str,
                                 token: str | None = None) -> list[dict]:
        resp = await (await self._relay()).send_request(
            "mcp.resources.list", {"serverCode": server_code})
        if not resp.ok:
            return []
        payload = resp.payload or {}
        res = payload.get("resources", []) if isinstance(payload, dict) else payload
        return [r for r in res if isinstance(r, dict)] if isinstance(res, list) else []

    async def mcp_read_resource(self, server_code: str, resource_uri: str,
                                token: str | None = None) -> dict:
        resp = await (await self._relay()).send_request(
            "mcp.resources.read",
            {"serverCode": server_code, "uri": resource_uri})
        return resp.payload if isinstance(resp.payload, dict) else {}

    async def mcp_list_prompts(self, server_code: str,
                               token: str | None = None) -> list[dict]:
        resp = await (await self._relay()).send_request(
            "mcp.prompts.list", {"serverCode": server_code})
        if not resp.ok:
            return []
        payload = resp.payload or {}
        p = payload.get("prompts", []) if isinstance(payload, dict) else payload
        return [x for x in p if isinstance(x, dict)] if isinstance(p, list) else []

    async def mcp_get_prompt(
        self,
        server_code: str,
        prompt_name: str,
        arguments: dict | None = None,
        token: str | None = None,
    ) -> dict:
        params: dict[str, Any] = {
            "serverCode": server_code, "name": prompt_name,
            "arguments": arguments or {},
        }
        resp = await (await self._relay()).send_request("mcp.prompts.get", params)
        if resp.ok:
            return {"success": True, "payload": resp.payload or {}}
        err = resp.error
        return {"success": False,
                "error": {"code": err.code if err else "UNKNOWN",
                          "message": err.message if err else "Unknown error"}}

    async def mcp_filter_servers(self, query: str | None = None,
                                 token: str | None = None) -> list[dict]:
        """Impl-side filter over ``mcp_list_servers`` (no wire RPC)."""
        servers = await self.mcp_list_servers(token=token)
        if not query:
            return servers
        q = query.lower()
        return [s for s in servers
                if q in str(s.get("serverCode", "")).lower()
                or q in str(s.get("name", "")).lower()]

    async def mcp_apply_server_filter(self, server_codes: list[str],
                                      timeout_seconds: int = 30,
                                      token: str | None = None) -> dict:
        """Allow-list apply via wire RPC ``mcp.filter_servers`` (mirrors corp)."""
        resp = await (await self._relay()).send_request(
            "mcp.filter_servers",
            {"serverCodes": server_codes or [], "timeoutSeconds": timeout_seconds},
        )
        if not resp.ok:
            msg = resp.error.message if resp.error else "unknown"
            raise RuntimeError(f"mcp.filter_servers failed: {msg}")
        return resp.payload if isinstance(resp.payload, dict) else {}
