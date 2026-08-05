"""Request/response models for the MCP group."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Request bodies reject unknown keys. Pydantic's default is to *ignore* them,
# which on a public API means a typo'd or unsupported field is silently dropped
# and the caller gets a 200 believing it was applied. With ``forbid`` the
# request fails validation (422, rendered as the standard Envelope) instead.
# This is what turns the dropped ``sync_mode`` field (no single-device push path
# exists) from a silent no-op into an honest rejection. Response models are
# server-constructed and take no caller input, so — as in ``bots/schemas.py`` —
# they are not guarded.
_STRICT = ConfigDict(extra="forbid")


class McpServer(BaseModel):
    """An MCP server in the marketplace."""

    server_code: str
    name: str
    description: str | None = None
    network_types: list[str] = Field(default_factory=list)
    transport_protocol: str | None = None


class McpServerDetail(McpServer):
    """An MCP server's detail, including its tools."""

    tools: list[dict[str, Any]] = Field(default_factory=list)


class McpPermission(BaseModel):
    """The caller's permission for an MCP server."""

    has_access: bool
    access_level: str | None = None
    tool_permissions: dict[str, Any] = Field(default_factory=dict)


class McpTenant(BaseModel):
    """An MCP tenant.

    Note: an "MCP tenant" is a marketplace concept from the upstream MCP Center,
    unrelated to the Avernet data-isolation tenant. The marketplace is a shared
    catalog — every Avernet tenant sees the same MCP tenants.
    """

    code: str
    name: str
    categories: list[str] = Field(default_factory=list)


class McpConfig(BaseModel):
    """The caller's unified config for an MCP server.

    ``api_key`` is always returned masked (first/last four for a long key, fully
    masked otherwise), never in full.
    """

    server_code: str
    api_key: str | None = Field(
        default=None, description="Masked API key; null when none is stored."
    )
    endpoint_env: str
    transport_protocol: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    has_config: bool = Field(
        description="True when a stored config row exists for this server; "
        "false only when the caller has never configured it."
    )


class McpConfigWrite(BaseModel):
    """Write the unified config. A null (omitted) field means "leave unchanged".

    ``extra="forbid"``: an unknown field is a 422, not a silent no-op. In
    particular the old ``sync_mode`` field is gone — the only push path is to
    every device under the caller, so a per-device mode would advertise
    something the server ignores.
    """

    model_config = _STRICT

    api_key: str | None = None
    endpoint_env: Literal["PROD", "PRE"] | None = None
    transport_protocol: Literal["SSE", "STREAMABLE_HTTP"] | None = None
    headers: dict[str, str] | None = None
