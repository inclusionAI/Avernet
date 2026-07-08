"""OpenClawMcpPort — native port for MCP server management.

MCP operations are local-infra: they read/write the mcporter.json config
file and invoke the ``mcporter`` CLI subprocess directly — no gateway, no
pool, no token.  The port impl owns all mcporter.json merge/write logic and
subprocess invocations; the adapter builds core DTOs from the primitive dicts
returned here.

Decision 5 (leaf-safety): ``CapabilityNotSupportedError`` and ``Capability``
are core types; the port impl must never import them.  The capability-gated
ops (``list_tools``, ``list_resources``, ``read_resource``, ``list_prompts``,
``get_prompt``) are NOT on this port — the adapter raises
``CapabilityNotSupportedError`` directly.  Likewise, ``start_server`` /
``stop_server`` / ``restart_server`` always return ``False`` directly in the
adapter; no port method needed.
"""
from __future__ import annotations

from typing import Any, Protocol


class OpenClawMcpPort(Protocol):
    """Native port for OpenClaw MCP server management via mcporter."""

    async def list_servers(self) -> list[dict[str, Any]]:
        """Read mcporter.json and return raw server entries sorted by code.

        Each dict in the list has an additional ``"server_code"`` key
        populated from the JSON object key.  Returns an empty list when the
        file does not exist.
        """
        ...

    async def get_server(self, server_code: str) -> dict[str, Any] | None:
        """Look up a single raw entry by ``server_code``.

        Returns ``None`` when the code is not present in mcporter.json.
        """
        ...

    async def create_server(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Merge ``entry`` into mcporter.json and persist.

        ``entry`` must contain ``"server_code"`` (used as the JSON key).
        Raises ``FileExistsError`` when ``server_code`` already exists.
        Returns the raw entry dict as stored (same shape as ``get_server``).
        """
        ...

    async def update_server(
        self, server_code: str, entry: dict[str, Any]
    ) -> dict[str, Any]:
        """Replace the existing ``server_code`` entry and persist.

        Raises ``FileNotFoundError`` when the code is not present.
        Returns the raw entry dict as stored.
        """
        ...

    async def delete_server(self, server_code: str) -> bool:
        """Remove ``server_code`` from mcporter.json.

        Returns ``True`` if the entry existed and was removed, ``False`` if
        the code was not present.
        """
        ...

    async def get_server_status(self, server_code: str) -> dict[str, Any]:
        """Derive status from the ``enabled`` flag of the stored entry.

        Returns a dict with at least:
          ``"server_code"`` (str), ``"status"`` (``"running"`` | ``"stopped"``).
        The status is ``"running"`` when enabled, ``"stopped"`` otherwise.
        Returns ``{"server_code": server_code, "status": "stopped"}`` when the
        code is not found.
        """
        ...

    async def call_tool(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Invoke ``mcporter call <tool> [key=value ...]`` (timeout 30 s).

        Returns a dict with keys:
          ``"tool_name"`` (str), ``"server_code"`` (str, may be empty),
          ``"content"`` (list[dict]), ``"is_error"`` (bool).
        Raises ``RuntimeError`` when the ``mcporter`` binary is not found.
        Raises ``TimeoutError`` when the subprocess exceeds 30 s.
        """
        ...

    async def filter_servers(
        self, codes: list[str], timeout: int = 30,
    ) -> dict[str, Any]:
        """Invoke ``mcporter filter-servers <csv>`` with ``codes``.

        Empty ``codes`` uses the sentinel ``__EMPTY_FILTER_DISABLE_ALL__``.
        Returns a dict with keys:
          ``"server_codes"`` (list[str]), ``"command"`` (list[str]),
          ``"return_code"`` (int), ``"stdout"`` (str), ``"stderr"`` (str).
        Raises ``ValueError`` when any code contains a comma.
        Raises ``RuntimeError`` on non-zero exit or missing binary.
        Raises ``TimeoutError`` on timeout.
        """
        ...


__all__ = ["OpenClawMcpPort"]
