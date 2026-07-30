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


def normalize_network_types(mcp_data: dict[str, Any]) -> list[str]:
    """A server's declared network types, from whichever shape the catalog uses.

    The plural ``networkTypes`` list is the primary shape; when it is absent/empty
    the singular ``networkType`` / ``network_type`` (scalar or list) is used — the
    shape ``LocalMCPRegistry`` declares and *filters its list on* (it reads those
    keys). Returns a flat list of strings (empty when none is declared).

    One definition so visibility and the response projection read the same shape:
    a server the catalog hides from the list on its network type must not resolve
    by code through the detail route, nor serialize an empty ``network_types``
    while carrying a known classification.
    """
    raw = mcp_data.get("networkTypes")
    if not raw:
        raw = mcp_data.get("networkType") or mcp_data.get("network_type") or []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [nt for nt in raw if isinstance(nt, str)]
    return []


def is_network_type_visible(mcp_data: dict[str, Any]) -> bool:
    """Whether a server's network types make it visible on this surface.

    A server with no network types is visible (nothing restricts it); one that
    declares types is visible only if at least one is in
    :data:`ALLOWED_NETWORK_TYPES`. Mirrors the internal detail route's rule.

    Reads every shape via :func:`normalize_network_types`, so a server hidden
    from the list on a singular ``networkType`` cannot resolve by code here.
    """
    network_types = normalize_network_types(mcp_data)
    if not network_types:
        return True
    return any(nt in ALLOWED_NETWORK_TYPES for nt in network_types)


def primary_transport_protocol(mcp_data: dict[str, Any]) -> str | None:
    """The transport protocol to advertise for a server in a flat projection.

    MCP Center carries ``transportProtocol`` per ``endpoints`` entry, not at the
    server top level (``config_compose``/``local_mcp_registry`` both read it from
    there), so a flat projection reading only the top level always yields
    ``None``. Prefer an endpoint on an allowed network type — the ones this
    surface would actually use — then any endpoint that declares a protocol;
    fall back to a top-level value for records that do carry one, else ``None``.
    First-in-list order breaks ties deterministically.
    """
    endpoints = mcp_data.get("endpoints")
    if isinstance(endpoints, list):
        with_protocol = [
            ep
            for ep in endpoints
            if isinstance(ep, dict) and isinstance(ep.get("transportProtocol"), str)
        ]
        allowed = [
            ep
            for ep in with_protocol
            if ep.get("networkType") in ALLOWED_NETWORK_TYPES
        ]
        for pool in (allowed, with_protocol):
            if pool:
                return pool[0]["transportProtocol"]
    top = mcp_data.get("transportProtocol")
    return top if isinstance(top, str) else None
