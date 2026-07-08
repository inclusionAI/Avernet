"""McporterComposer — assemble the MCP manifest in-backend, secrets inlined.

Today the published mcporter config is read back **from the device** (the build
step ``cat``s ``/home/admin/.mcporter/mcporter.json`` off the container). That
ties config assembly to a running ARCA sandbox. This composer instead builds the
equivalent MCP manifest **in the backend** from DB state — the same inputs the
device-sync path uses (``collect_bot_active_mcps`` + ``build_mcp_sync_payload``).

The output is byte-for-byte equivalent to what the device ``/api/mcp`` path
produces (see ``plugins/prod/mcp_device_payload.convert_to_device_format``):

1. **Secrets inlined.** The resolved credential rides in the entry exactly as the
   device path places it — an ``authorization`` key is appended to the endpoint
   URL (``?authorization=…``); an ``x-ling-auth`` key (and any other header) goes
   into ``headers``. The backend holds the plaintext at compose time and the
   engine uses it directly; there is no by-reference indirection / secret broker.
2. **Engine-agnostic shape.** Output is the portable ``McpManifest`` /
   ``McpServerRef`` contract, field-aligned with the device format so a foreign
   engine can reconstruct its own mcporter.json.

Scope: REMOTE (URL-based) MCP servers. ``LOCAL``/stdio servers carry
command/args/env which the current ``McpServerRef`` contract does not model;
they raise :class:`McporterComposeError` rather than being silently dropped.

The inlining rule is replicated from ``convert_to_device_format`` (a plugins-layer
function this core module must not import); a parity test
(``tests/core/config_compose/test_golden_equivalence.py``) locks the two together.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from agentclaw.community.core.config_compose.models import McpComposeInput
from agentclaw.community.kernel.bot_config import McpManifest, McpServerRef


__all__ = [
    "McporterComposer",
    "McporterComposeError",
    "TECLAW_MCP_NETWORK_PRIORITY",
    "mcp_network_priority_for",
]

# Engine types whose MCP endpoint selection follows the deterministic
# network-priority rule below. Kept local to avoid coupling config_compose to
# bot_management; "teclaw" is the stable engine token (see
# DEFAULT_TECLAW_ENGINE_TYPES).
_PRIORITY_ENGINE_TYPES = frozenset({"teclaw"})

# Teclaw containers reach networks in this order; an endpoint on an earlier
# network always wins over a later one (network is the primary sort key).
TECLAW_MCP_NETWORK_PRIORITY = ("OFFICE", "INTERNET", "INTRANET")

# Transport priority within a network tier (lower rank = higher priority).
_TRANSPORT_RANK = {"STREAMABLE_HTTP": 0, "SSE": 1}


def mcp_network_priority_for(engine_type: str | None) -> tuple[str, ...] | None:
    """Network priority (highest first) for an engine's MCP endpoint selection.

    Returns ``None`` for engines that keep the legacy filter +
    ``transport_protocol``-preference selection; returns the ordered network
    tuple for engines (teclaw) that select deterministically by
    ``(network rank, transport rank)``.
    """
    if engine_type in _PRIORITY_ENGINE_TYPES:
        return TECLAW_MCP_NETWORK_PRIORITY
    return None


class McporterComposeError(ValueError):
    """Raised when an MCP entry cannot be composed into the artifact contract."""


class McporterComposer:
    """Compose MCP manifest entries from merged DB config, secrets inlined."""

    def compose(self, inputs: Iterable[McpComposeInput]) -> McpManifest:
        """Build a full ``McpManifest`` from per-MCP merged config.

        The published artifact contract is still remote-MCP-only. Runtime device
        sync handles LOCAL/stdio MCPs separately, so artifact composition skips
        them instead of failing the whole bot config.
        """
        servers = [
            self.compose_server(item)
            for item in inputs
            if not self._is_local_stdio(item.mcp_data)
        ]
        return McpManifest(servers=servers)

    def compose_server(self, item: McpComposeInput) -> McpServerRef:
        """Build one ``McpServerRef`` — REMOTE only, resolved secrets inlined."""
        md = item.mcp_data
        server_code = md.get("server_code") or md.get("serverCode") or ""
        if not server_code:
            raise McporterComposeError("MCP entry missing server_code")

        name = md.get("name") or md.get("description")
        endpoint, transport = self._select_endpoint(
            server_code,
            md,
            item.endpoint_env,
            item.transport_protocol,
            item.network_priority,
        )
        endpoint, headers = self._inline_secrets(endpoint, item.api_key, item.headers)

        return McpServerRef(
            server_code=server_code,
            name=name,
            endpoint=endpoint,
            transport=transport,
            headers=headers,
        )

    def _is_local_stdio(self, md: dict[str, Any]) -> bool:
        run_mode = md.get("run_mode") or md.get("runMode", "REMOTE")
        return run_mode == "LOCAL"

    def _inline_secrets(
        self, endpoint: str | None, api_key: str | None, headers: dict[str, str]
    ) -> tuple[str | None, dict[str, str]]:
        """Inline the resolved credential, mirroring ``convert_to_device_format``.

        ``api_key`` is ``"name=value"``. ``authorization`` is appended to the
        endpoint URL query; ``x-ling-auth`` becomes a header; any other name is
        ignored (device-path parity). The ``api_key``-derived header is written
        first and ``headers`` merge on top — same precedence as the device path
        (``convert_to_device_format``: api_key header set, then custom_headers
        update over it), so a same-key collision resolves identically.
        """
        merged_headers: dict[str, str] = {}

        if api_key and "=" in api_key:
            key_name, key_value = api_key.split("=", 1)
            lowered = key_name.lower()
            if lowered == "authorization" and endpoint:
                separator = "&" if "?" in endpoint else "?"
                endpoint = f"{endpoint}{separator}{key_name}={key_value}"
            elif lowered == "x-ling-auth":
                merged_headers[key_name] = key_value

        merged_headers.update(headers or {})
        return endpoint, merged_headers

    def _select_endpoint(
        self,
        server_code: str,
        md: dict[str, Any],
        endpoint_env: str,
        transport_protocol: str | None,
        network_priority: tuple[str, ...] | None = None,
    ) -> tuple[str | None, str]:
        """Pick the (url, transport) for a REMOTE MCP, mirroring device rules.

        When ``network_priority`` is set (teclaw), select deterministically by
        ``(network rank, transport rank)`` — see :meth:`_select_by_priority`.
        Otherwise keep OFFICE/INTERNET endpoints matching ``endpoint_env`` and
        prefer the user's ``transport_protocol``, else ``STREAMABLE_HTTP``, else
        the first. The URL returned here is **clean** — the caller inlines any
        ``authorization`` secret afterwards.
        """
        run_mode = md.get("run_mode") or md.get("runMode", "REMOTE")
        if run_mode == "LOCAL":
            raise McporterComposeError(
                f"MCP {server_code}: stdio/LOCAL servers are not modeled in the "
                "BotConfigArtifact contract yet (command/args/env)."
            )

        endpoints = md.get("endpoints", [])
        if isinstance(endpoints, str):
            try:
                endpoints = json.loads(endpoints)
            except json.JSONDecodeError:
                endpoints = []

        if network_priority:
            return self._select_by_priority(
                server_code, endpoints, endpoint_env, network_priority
            )

        valid = [
            ep
            for ep in endpoints
            if ep.get("networkType") in ("OFFICE", "INTERNET")
            and ep.get("env") == endpoint_env
        ]
        if not valid:
            raise McporterComposeError(
                f"MCP {server_code}: no usable {endpoint_env} endpoint "
                "(OFFICE/INTERNET network)."
            )

        ep: dict[str, Any] | None = None
        if transport_protocol:
            ep = next(
                (
                    c
                    for c in valid
                    if c.get("transportProtocol") == transport_protocol
                ),
                None,
            )
        if ep is None:
            ep = next(
                (
                    c
                    for c in valid
                    if c.get("transportProtocol") == "STREAMABLE_HTTP"
                ),
                None,
            )
        if ep is None:
            ep = valid[0]

        url = ep.get("url")
        protocol = ep.get("transportProtocol", "SSE")
        transport = "http" if protocol == "STREAMABLE_HTTP" else "sse"
        return url, transport

    def _select_by_priority(
        self,
        server_code: str,
        endpoints: list[dict[str, Any]],
        endpoint_env: str,
        network_priority: tuple[str, ...],
    ) -> tuple[str | None, str]:
        """Pick the (url, transport) deterministically for a priority engine.

        Among the ``endpoint_env`` endpoints whose ``networkType`` is in
        ``network_priority``, choose the one ranked highest by
        ``(network rank, transport rank)`` — network is primary (earlier in
        ``network_priority`` wins), transport breaks ties (STREAMABLE_HTTP over
        SSE). The user's per-MCP ``transport_protocol`` preference does not apply
        here; the priority is fixed.
        """
        net_rank = {net: i for i, net in enumerate(network_priority)}
        candidates = [
            ep
            for ep in endpoints
            if ep.get("env") == endpoint_env and ep.get("networkType") in net_rank
        ]
        if not candidates:
            raise McporterComposeError(
                f"MCP {server_code}: no usable {endpoint_env} endpoint "
                f"(networks {'/'.join(network_priority)})."
            )

        def _key(ep: dict[str, Any]) -> tuple[int, int]:
            tp = ep.get("transportProtocol", "SSE")
            return (
                net_rank[ep.get("networkType")],
                _TRANSPORT_RANK.get(tp, len(_TRANSPORT_RANK)),
            )

        ep = min(candidates, key=_key)
        url = ep.get("url")
        protocol = ep.get("transportProtocol", "SSE")
        transport = "http" if protocol == "STREAMABLE_HTTP" else "sse"
        return url, transport
