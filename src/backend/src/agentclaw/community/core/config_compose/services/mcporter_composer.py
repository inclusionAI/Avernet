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

Scope: both MCP forms. A REMOTE (URL-based) server gets an endpoint selected and
its credential inlined; a LOCAL/stdio server is emitted as a ``stdio`` entry
carrying its launch instruction. Which form an entry takes is **not decided
here** — the collector resolves it into ``McpComposeInput.stdio``, so this stays
a pure function of its inputs (and cannot be fooled by a failed MCP Center
enrichment; see that field's docstring).

The inlining rule is replicated from ``convert_to_device_format`` (a plugins-layer
function this core module must not import); a parity test
(``tests/core/config_compose/test_golden_equivalence.py``) locks the two together.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from agentclaw.community.core.config_compose.models import McpComposeInput, StdioLaunch
from agentclaw.community.kernel.bot_config import McpManifest, McpServerRef, StdioSpec


__all__ = [
    "McporterComposer",
    "McporterComposeError",
    "STDIO_TRANSPORT",
    "TECLAW_MCP_NETWORK_PRIORITY",
    "mcp_network_priority_for",
]

# ``transport`` value marking the local form of an ``McpServerRef``. The engine
# reads this to decide "spawn a child process" vs "make an HTTP request", so it
# is part of the published contract — see ``McpServerRef``. Lowercase to sit in
# the same vocabulary as the remote values this composer already emits
# ("http" / "sse", the device-format spelling), and it matches what the MCP spec
# itself calls this transport.
STDIO_TRANSPORT = "stdio"

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

        Every input yields an entry; LOCAL/stdio servers are no longer dropped.
        """
        return McpManifest(servers=[self.compose_server(item) for item in inputs])

    def compose_server(self, item: McpComposeInput) -> McpServerRef:
        """Build one ``McpServerRef``, in whichever form ``item`` calls for."""
        md = item.mcp_data
        server_code = md.get("server_code") or md.get("serverCode") or ""
        if not server_code:
            raise McporterComposeError("MCP entry missing server_code")

        name = md.get("name") or md.get("description")
        if item.stdio is not None:
            return self._compose_stdio(server_code, name, item.stdio)
        return self._compose_remote(server_code, name, item, md)

    def _compose_stdio(
        self, server_code: str, name: str | None, launch: StdioLaunch
    ) -> McpServerRef:
        """Emit the local form: a launch instruction, no endpoint, no credential.

        A stdio server is a child of the engine process on the same host, so there
        is nothing to authenticate against — the input's ``api_key`` / ``headers``
        are deliberately not passed here rather than being inlined into a
        non-existent endpoint.
        """
        return McpServerRef(
            server_code=server_code,
            name=name,
            transport=STDIO_TRANSPORT,
            stdio=StdioSpec(
                command=launch.command,
                args=list(launch.args),
                env=dict(launch.env),
            ),
        )

    def _compose_remote(
        self,
        server_code: str,
        name: str | None,
        item: McpComposeInput,
        md: dict[str, Any],
    ) -> McpServerRef:
        """Emit the remote form: selected endpoint with the credential inlined."""
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
            # Reachable only when an entry reads as LOCAL but arrived with no
            # resolved launch instruction — i.e. the collector could not find it
            # in the local-MCP registry. Fail with *that* cause rather than
            # falling through to a misleading "no usable endpoint": a local
            # server has no endpoint to find, so the endpoint error would send
            # the reader hunting in MCP Center for something that was never
            # there.
            raise McporterComposeError(
                f"MCP {server_code}: LOCAL server reached the remote path with no "
                "stdio launch instruction — the local-MCP registry has no entry "
                "for it (check that local-mcp-servers.yaml is readable)."
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
            # Two different faults, and this layer can only tell them apart by
            # how many endpoints arrived — so it reports exactly that and no
            # more. In particular it must NOT claim the detail was never
            # resolved: a record Center does hold but has published no endpoints
            # for (``{"runMode": "REMOTE"}``) reaches here with an empty list and
            # a perfectly successful lookup behind it. Asserting a cause this
            # frame cannot observe is the same misdirection the collector-level
            # raise exists to remove, just narrowed to a rarer input.
            if not endpoints:
                raise McporterComposeError(
                    f"MCP {server_code}: the server record carries no endpoints at "
                    f"all, so there is nothing to select a {endpoint_env} URL from. "
                    "Check the endpoints published for this server in MCP Center."
                )
            raise McporterComposeError(
                f"MCP {server_code}: no usable {endpoint_env} endpoint "
                f"(networks {'/'.join(network_priority)}); the server declares "
                f"{len(endpoints)} endpoint(s), none on a reachable network for "
                "this env."
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
