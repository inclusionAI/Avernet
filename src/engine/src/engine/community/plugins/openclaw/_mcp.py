"""_McpPortMixin — MCP server management port methods."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("openclaw-port")

# mcporter 子进程钉在稳定 home，避免继承 engine 进程被 BaaS 平台清理的临时 cwd
# (报 "current working directory was deleted" → 500)。目录不存在则降级 None=原行为。
_MCPORTER_CWD = "/home/admin" if Path("/home/admin").is_dir() else None


class _McpPortMixin:
    """Domain mixin: MCP server CRUD + tool calls (local-infra, no gateway/pool/token)."""

    def _mcporter_config_path(self) -> Path:
        """Resolve the mcporter.json path, lazily via engine.community.config."""
        from engine.community.config import load_mcporter_config_path  # noqa: PLC0415
        return load_mcporter_config_path()

    def _mcp_load(self) -> "tuple[dict[str, Any], str, dict[str, Any]]":
        """Load mcporter.json; return (root, servers_key, servers_dict).

        Relocated intact from
        ``engines/openclaw/mcp.py:OpenClawMCPService._load``.
        """
        path = self._mcporter_config_path()
        if not path.exists():
            return {"mcpServers": {}}, "mcpServers", {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"mcporter.json 格式错误: {e}") from e
        if not isinstance(data, dict):
            raise RuntimeError("mcporter.json 顶层必须为 object")
        if isinstance(data.get("mcpServers"), dict):
            return data, "mcpServers", data["mcpServers"]
        if isinstance(data.get("servers"), dict):
            return data, "servers", data["servers"]
        data["mcpServers"] = {}
        return data, "mcpServers", data["mcpServers"]

    def _mcp_save(self, root: dict[str, Any]) -> None:
        """Persist ``root`` to mcporter.json.

        Relocated intact from
        ``engines/openclaw/mcp.py:OpenClawMCPService._save``.
        """
        path = self._mcporter_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(root, ensure_ascii=False, indent=2) + "\n"
        path.write_text(text, encoding="utf-8")

    @staticmethod
    def _mcp_serialize_entry(
        entry: dict[str, Any], existing_raw: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Merge ``entry`` with ``existing_raw`` preserving legacy key names.

        Relocated intact from
        ``engines/openclaw/mcp.py:_serialize_config`` but operates on plain
        dicts (DTO build lives in the adapter).  ``entry`` must use canonical
        keys (``url``, ``transport``, ``timeout_seconds``); this helper
        detects the legacy shape of ``existing_raw`` and keeps legacy key
        names when already present (prevents churn on re-save).
        """
        existing = existing_raw if isinstance(existing_raw, dict) else {}
        url_key = (
            "baseUrl"
            if "baseUrl" in existing and "url" not in existing
            else "url"
        )
        transport_key = (
            "type"
            if "type" in existing and "transport" not in existing
            else "transport"
        )
        payload: dict[str, Any] = {
            "description": entry.get("description", ""),
            transport_key: entry.get("transport", "sse"),
            "command": entry.get("command"),
            "args": entry.get("args", []),
            "env": entry.get("env", {}),
            "headers": entry.get("headers", {}),
            "timeout_seconds": entry.get("timeout_seconds", 30),
            "enabled": entry.get("enabled", True),
        }
        payload[url_key] = entry.get("url")
        return payload

    async def list_servers(self) -> list[dict[str, Any]]:
        """Return raw mcporter.json entries sorted by server_code.

        Each dict includes ``server_code`` plus all fields from the JSON
        object.  Relocated intact from
        ``engines/openclaw/mcp.py:OpenClawMCPService.list_servers`` (raw
        extraction only; DTO build moved to adapter).
        """
        _, _, servers = self._mcp_load()
        entries = []
        for code, raw in servers.items():
            entry = dict(raw) if isinstance(raw, dict) else {}
            entry["server_code"] = code
            entries.append(entry)
        entries.sort(key=lambda e: e["server_code"])
        return entries

    async def get_server(self, server_code: str) -> dict[str, Any] | None:
        """Look up a raw entry by code; ``None`` if not present.

        Relocated intact from
        ``engines/openclaw/mcp.py:OpenClawMCPService.get_server`` (raw
        extraction only).
        """
        _, _, servers = self._mcp_load()
        raw = servers.get(server_code)
        if raw is None:
            return None
        entry = dict(raw) if isinstance(raw, dict) else {}
        entry["server_code"] = server_code
        return entry

    async def create_server(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Merge ``entry`` into mcporter.json; raise ``FileExistsError`` on dup.

        ``entry`` must contain ``server_code``.  Relocated intact from
        ``engines/openclaw/mcp.py:OpenClawMCPService.create_server`` (the
        MCPServerConfig→entry serialization moved to the adapter).
        """
        server_code = entry["server_code"]
        root, key, servers = self._mcp_load()
        if server_code in servers:
            raise FileExistsError(f"MCP Server 已存在: {server_code}")
        stored = self._mcp_serialize_entry(entry, None)
        servers[server_code] = stored
        root[key] = servers
        self._mcp_save(root)
        result = dict(stored)
        result["server_code"] = server_code
        return result

    async def update_server(
        self, server_code: str, entry: dict[str, Any]
    ) -> dict[str, Any]:
        """Replace an existing entry; raise ``FileNotFoundError`` if absent.

        Relocated intact from
        ``engines/openclaw/mcp.py:OpenClawMCPService.update_server``.
        """
        root, key, servers = self._mcp_load()
        existing_raw = servers.get(server_code)
        if existing_raw is None:
            raise FileNotFoundError(f"MCP Server 不存在: {server_code}")
        stored = self._mcp_serialize_entry(
            entry,
            existing_raw if isinstance(existing_raw, dict) else None,
        )
        servers[server_code] = stored
        root[key] = servers
        self._mcp_save(root)
        result = dict(stored)
        result["server_code"] = server_code
        return result

    async def delete_server(self, server_code: str) -> bool:
        """Remove ``server_code`` from mcporter.json; ``False`` if not found.

        Relocated intact from
        ``engines/openclaw/mcp.py:OpenClawMCPService.delete_server``.
        """
        root, key, servers = self._mcp_load()
        if server_code not in servers:
            return False
        servers.pop(server_code, None)
        root[key] = servers
        self._mcp_save(root)
        return True

    async def get_server_status(self, server_code: str) -> dict[str, Any]:
        """Derive status from the ``enabled`` flag.

        Returns ``{"server_code": ..., "status": "running"|"stopped"}``.
        Relocated from
        ``engines/openclaw/mcp.py:OpenClawMCPService.get_server_status``
        (status-derivation logic stays impl-side operating on plain dict).
        """
        _, _, servers = self._mcp_load()
        raw = servers.get(server_code)
        if raw is None or not isinstance(raw, dict):
            return {"server_code": server_code, "status": "stopped"}
        status = "running" if raw.get("enabled", True) else "stopped"
        return {"server_code": server_code, "status": status}

    async def call_tool(
        self, tool: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Invoke ``mcporter call <tool> [key=value ...]`` (timeout 30 s).

        Relocated intact from
        ``engines/openclaw/mcp.py:OpenClawMCPService.call_tool`` (the
        MCPToolCallRequest→params unpacking and MCPToolCallResult build moved
        to the adapter).
        """
        import subprocess as _sp  # noqa: PLC0415

        config_path = self._mcporter_config_path()
        cmd = ["mcporter", "call", "--config", str(config_path), tool]
        for key, value in (args or {}).items():
            cmd.append(f"{key}={value}")

        timeout = 30
        try:
            proc = _sp.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                cwd=_MCPORTER_CWD,
            )
        except FileNotFoundError as e:
            raise RuntimeError(f"mcporter 命令不存在: {e}") from e
        except _sp.TimeoutExpired as e:
            raise TimeoutError(f"mcporter call 超时 ({timeout}s): {e}") from e

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        is_error = proc.returncode != 0
        content = stdout if not is_error else stderr or stdout
        return {
            "tool_name": tool,
            "server_code": "",
            "content": [{"type": "text", "text": content}],
            "is_error": is_error,
        }

    async def filter_servers(
        self, codes: list[str], timeout: int = 30,
    ) -> dict[str, Any]:
        """Invoke ``mcporter filter-servers <csv>`` with ``codes``.

        Relocated intact from
        ``engines/openclaw/mcp.py:OpenClawMCPService.filter_servers`` (the
        MCPFilterRequest→codes extraction and MCPFilterResult build moved to
        the adapter; the caller's ``timeout`` is now threaded through).
        """
        import subprocess as _sp  # noqa: PLC0415

        normalized: list[str] = []
        for code in codes or []:
            item = str(code).strip()
            if not item:
                continue
            if "," in item:
                raise ValueError(f"server_code 不能包含逗号: {item}")
            normalized.append(item)

        csv_codes = (
            ",".join(normalized) if normalized else "__EMPTY_FILTER_DISABLE_ALL__"
        )
        config_path = self._mcporter_config_path()
        command = ["mcporter", "filter-servers", "--config", str(config_path), csv_codes]
        try:
            proc = _sp.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                cwd=_MCPORTER_CWD,
            )
        except FileNotFoundError as e:
            raise RuntimeError(f"mcporter 命令不存在: {e}") from e
        except _sp.TimeoutExpired as e:
            raise TimeoutError(f"命令执行超时: {e}") from e

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        if proc.returncode != 0:
            raise RuntimeError(
                f"mcporter filter-servers 执行失败: code={proc.returncode}, stderr={stderr}"
            )
        return {
            "server_codes": normalized,
            "command": command,
            "return_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
