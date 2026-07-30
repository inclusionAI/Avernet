"""Presentation rules shared by both MCP API surfaces.

Masking, ``extInfo`` stripping, and the network-type allowlist were duplicated
inside the internal ``/api/mcp`` router's handler bodies. They live here so the
internal and public surfaces cannot drift on how much of a credential they
reveal or which servers they show — a single definition each.

Nothing here touches HTTP, the database, or a service; these are pure functions
over the plain dicts MCP Center returns.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

# The network types the surface exposes. MCP Center may carry others; a server
# reachable only on a type outside this set is not shown, and its detail answers
# not-found. Historically duplicated at two sites in the internal router.
ALLOWED_NETWORK_TYPES: tuple[str, ...] = ("INTERNET", "OFFICE")


def mask_api_key(api_key: str | None) -> str | None:
    """Mask a stored API key for display.

    Keeps the first and last four characters for a key long enough to spare
    them (``> 8``); anything shorter is fully masked so the reveal never exceeds
    the concealment. ``None`` stays ``None`` — "no key" is not "masked key".

    The one definition both surfaces use, so a credential is never revealed more
    on one door than the other.
    """
    if not api_key:
        return None
    if len(api_key) > 8:
        return api_key[:4] + "****" + api_key[-4:]
    return "****"


def strip_ext_info(mcp_data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of one MCP server's data with tool ``extInfo`` removed.

    ``extInfo`` is internal plumbing carried on a tool's input-schema
    properties; it must not reach an external caller. Non-dict shapes are left
    untouched. The input is not mutated.
    """
    sanitized = deepcopy(mcp_data)
    tools = sanitized.get("tools")
    if not isinstance(tools, list):
        return sanitized

    for tool in tools:
        if not isinstance(tool, dict):
            continue
        input_schema = tool.get("inputSchema")
        if not isinstance(input_schema, dict):
            continue
        properties = input_schema.get("properties")
        if isinstance(properties, dict):
            properties.pop("extInfo", None)

    return sanitized


def strip_ext_info_from_list(result: dict[str, Any]) -> dict[str, Any]:
    """Apply :func:`strip_ext_info` to every server in a market-list result.

    Operates on the ``data`` list of an MCP Center list response, leaving the
    surrounding envelope (``total``, paging) intact. The input is not mutated.
    """
    sanitized = deepcopy(result)
    data = sanitized.get("data")
    if isinstance(data, list):
        sanitized["data"] = [
            strip_ext_info(item) if isinstance(item, dict) else item
            for item in data
        ]
    return sanitized


def is_network_type_visible(mcp_data: dict[str, Any]) -> bool:
    """Whether a server's network types make it visible on this surface.

    A server with no network types is visible (nothing restricts it); one that
    declares types is visible only if at least one is in
    :data:`ALLOWED_NETWORK_TYPES`. Mirrors the internal detail route's rule.

    The plural ``networkTypes`` list is the primary shape; when it is absent the
    check falls back to the singular ``networkType`` / ``network_type`` (scalar
    or list) that the local registry declares and *filters its list on*
    (``LocalMCPRegistry`` reads those keys). Without the fallback a server the
    catalog hides from the list on its network type would still resolve by code
    through the detail route — a visibility rule the two paths must not split on.
    """
    network_types = mcp_data.get("networkTypes")
    if not network_types:
        network_types = mcp_data.get("networkType") or mcp_data.get("network_type") or []
    if isinstance(network_types, str):
        network_types = [network_types]
    if not network_types:
        return True
    return any(nt in ALLOWED_NETWORK_TYPES for nt in network_types)
