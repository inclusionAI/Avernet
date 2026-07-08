"""ClaudeCodeMcpPort — native port for MCP server management.

MCP operations go through the relay (client + pool), so port methods take
``token: str | None = None`` for per-token routing. Returns raw
dicts / list[dict] / bool — the adapter builds the core DTOs.

Relay RPC mapping (teamclaw-aicoding-relay v3 protocol):

============================  ================================================
Port method                   Relay RPC (method name on the wire)
============================  ================================================
``mcp_list_servers``          ``mcp.config.list``
``mcp_get_server``            ``mcp.config.get``
``mcp_create_server``         ``mcp.config.create``
``mcp_update_server``         ``mcp.config.update``
``mcp_delete_server``         ``mcp.config.delete``
``mcp_start_server``          ``mcp.server.start``
``mcp_stop_server``           ``mcp.server.stop``
``mcp_restart_server``        ``mcp.server.restart``
``mcp_get_server_status``     ``mcp.server.status``
``mcp_list_tools``            ``mcp.tools.list``
``mcp_call_tool``             ``mcp.tools.call``
``mcp_list_resources``        ``mcp.resources.list``
``mcp_read_resource``         ``mcp.resources.read``
``mcp_list_prompts``          ``mcp.prompts.list``
``mcp_get_prompt``            ``mcp.prompts.get``
``mcp_filter_servers``        (impl-side filter helper, no wire RPC)
``mcp_apply_server_filter``   (allow-list apply, wire RPC mcp.filter_servers)
============================  ================================================
"""
from __future__ import annotations

from typing import Protocol


class ClaudeCodeMcpPort(Protocol):
    """Native MCP server management over the claude_code gateway (vendored Node relay)."""

    async def mcp_list_servers(
        self,
        token: str | None = None,
    ) -> list[dict]:
        """Call ``mcp.config.list``; return raw server config dicts.

        Args:
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def mcp_get_server(
        self,
        server_code: str,
        token: str | None = None,
    ) -> dict | None:
        """Call ``mcp.config.get`` for a single server.

        Returns ``None`` when the code is not present.

        Args:
            server_code: The server identifier to look up.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def mcp_create_server(
        self,
        config: dict,
        token: str | None = None,
    ) -> dict:
        """Call ``mcp.config.create`` to add a new server.

        Args:
            config: Raw server config dict (name, command, args, env, ...).
            token: MCP token for per-token pool routing; None -> default client.

        Returns:
            Raw server config dict as stored.
        """
        ...

    async def mcp_update_server(
        self,
        server_code: str,
        patch: dict,
        token: str | None = None,
    ) -> dict:
        """Call ``mcp.config.update`` to patch an existing server.

        Args:
            server_code: The server identifier to update.
            patch: Partial config dict to merge.
            token: MCP token for per-token pool routing; None -> default client.

        Returns:
            Raw server config dict after update.
        """
        ...

    async def mcp_delete_server(
        self,
        server_code: str,
        token: str | None = None,
    ) -> bool:
        """Call ``mcp.config.delete``; return True on success, False on error.

        Args:
            server_code: The server identifier to remove.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def mcp_start_server(
        self,
        server_code: str,
        token: str | None = None,
    ) -> dict:
        """Call ``mcp.server.start`` to start a server.

        Args:
            server_code: The server identifier to start.
            token: MCP token for per-token pool routing; None -> default client.

        Returns:
            Raw ``{success, error, payload}`` dict.
        """
        ...

    async def mcp_stop_server(
        self,
        server_code: str,
        token: str | None = None,
    ) -> dict:
        """Call ``mcp.server.stop`` to stop a server.

        Args:
            server_code: The server identifier to stop.
            token: MCP token for per-token pool routing; None -> default client.

        Returns:
            Raw ``{success, error, payload}`` dict.
        """
        ...

    async def mcp_restart_server(
        self,
        server_code: str,
        token: str | None = None,
    ) -> dict:
        """Call ``mcp.server.restart`` to restart a server.

        Args:
            server_code: The server identifier to restart.
            token: MCP token for per-token pool routing; None -> default client.

        Returns:
            Raw ``{success, error, payload}`` dict.
        """
        ...

    async def mcp_get_server_status(
        self,
        server_code: str,
        token: str | None = None,
    ) -> dict:
        """Call ``mcp.server.status``; return raw status dict.

        Args:
            server_code: The server identifier to query.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def mcp_list_tools(
        self,
        server_code: str,
        token: str | None = None,
    ) -> list[dict]:
        """Call ``mcp.tools.list``; return raw tool definition dicts.

        Args:
            server_code: The server whose tools to enumerate.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def mcp_call_tool(
        self,
        server_code: str,
        tool_name: str,
        arguments: dict | None = None,
        token: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict:
        """Call ``mcp.tools.call`` to invoke a tool and return the raw result.

        Args:
            server_code: The server hosting the tool.
            tool_name: The tool identifier to invoke.
            arguments: Optional arguments dict for the tool call.
            token: MCP token for per-token pool routing; None -> default client.
            timeout_ms: Optional call timeout in milliseconds.

        Returns:
            Raw ``{success, error, payload}`` dict.
        """
        ...

    async def mcp_list_resources(
        self,
        server_code: str,
        token: str | None = None,
    ) -> list[dict]:
        """Call ``mcp.resources.list``; return raw resource descriptor dicts.

        Args:
            server_code: The server whose resources to enumerate.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def mcp_read_resource(
        self,
        server_code: str,
        resource_uri: str,
        token: str | None = None,
    ) -> dict:
        """Call ``mcp.resources.read``; return raw resource content dict.

        Args:
            server_code: The server hosting the resource.
            resource_uri: The URI of the resource to read.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def mcp_list_prompts(
        self,
        server_code: str,
        token: str | None = None,
    ) -> list[dict]:
        """Call ``mcp.prompts.list``; return raw prompt template dicts.

        Args:
            server_code: The server whose prompts to enumerate.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def mcp_get_prompt(
        self,
        server_code: str,
        prompt_name: str,
        arguments: dict | None = None,
        token: str | None = None,
    ) -> dict:
        """Call ``mcp.prompts.get`` to render a prompt template.

        Args:
            server_code: The server hosting the prompt.
            prompt_name: The prompt template identifier.
            arguments: Optional arguments for template rendering.
            token: MCP token for per-token pool routing; None -> default client.

        Returns:
            Raw ``{success, error, payload}`` dict.
        """
        ...

    async def mcp_filter_servers(
        self,
        query: str | None = None,
        token: str | None = None,
    ) -> list[dict]:
        """Impl-side filter helper: filter cached server list by query.

        No wire RPC — operates on the result of ``mcp_list_servers``.

        Args:
            query: Optional substring to filter server names/codes by.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def mcp_apply_server_filter(
        self,
        server_codes: list[str],
        timeout_seconds: int = 30,
        token: str | None = None,
    ) -> dict:
        """Apply a server allow-list via the relay ``mcp.filter_servers`` RPC.

        Keeps only ``server_codes`` enabled (empty list disables all), mirroring
        OpenClaw's ``mcporter filter-servers``. This is the wire RPC — distinct
        from ``mcp_filter_servers`` (impl-side substring search, no RPC).

        Returns the raw ``{serverCodes, command, returnCode, stdout, stderr}``
        payload for the adapter to map onto ``MCPFilterResult``.
        """
        ...


__all__ = ["ClaudeCodeMcpPort"]
