"""Request/response models for the MCP group."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Request bodies reject unknown keys. Pydantic's default is to *ignore* them,
# which on a public API means a typo'd or unsupported field is silently dropped
# and the caller gets a 200 believing it was applied. With ``forbid`` the
# request fails validation (422, rendered as the standard Envelope) instead.
# Response models are server-constructed and take no caller input, so — as in
# ``bots/schemas.py`` — they are not guarded.


_SERVER_CODE_DESC = (
    "Identifier of the MCP server. Use it in the path of the per-server "
    "endpoints."
)

_TRANSPORT_DESC = (
    "How the server is reached: 'SSE' or 'STREAMABLE_HTTP'. Null when the "
    "marketplace does not report one."
)


class McpServer(BaseModel):
    """An MCP server in the marketplace."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "server_code": "web-search",
                "name": "Web Search",
                "description": "Search the public web and read result pages.",
                "network_types": ["public"],
                "transport_protocol": "STREAMABLE_HTTP",
            }
        }
    )

    server_code: str = Field(description=_SERVER_CODE_DESC)
    name: str = Field(description="Human-readable server name.")
    description: str | None = Field(
        default=None, description="What the server does; null when the "
        "marketplace records none."
    )
    network_types: list[str] = Field(
        default_factory=list,
        description="Networks the server is reachable from; empty when the "
        "marketplace records none.",
    )
    transport_protocol: str | None = Field(default=None, description=_TRANSPORT_DESC)


class McpServerDetail(McpServer):
    """An MCP server, including the tools it offers."""

    tools: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Tools the server exposes. Each entry is the server's own "
        "tool definition, whose shape the server decides rather than this API.",
    )


class McpPermission(BaseModel):
    """Whether the caller may use an MCP server."""

    has_access: bool = Field(
        description="True when the caller may use the server. Advisory — the "
        "server itself is the enforcement point."
    )
    access_level: str | None = Field(
        default=None, description="Level of access granted; null when the "
        "marketplace reports none."
    )
    tool_permissions: dict[str, Any] = Field(
        default_factory=dict,
        description="Per-tool grants, keyed by tool name; empty when access is "
        "not restricted per tool.",
    )


class McpTenant(BaseModel):
    """A category grouping in the MCP marketplace.

    The marketplace is a shared catalogue: every caller sees the same groupings,
    whichever tenant they belong to.
    """

    # "MCP tenant" is the upstream marketplace's own term and is unrelated to
    # the Avernet data-isolation tenant. Kept as-is because it is the identifier
    # the marketplace publishes.

    code: str = Field(description="Identifier of the grouping.")
    name: str = Field(description="Human-readable grouping name.")
    categories: list[str] = Field(
        default_factory=list, description="Categories the grouping covers; empty "
        "when it records none."
    )


_ENDPOINT_ENV_DESC = (
    "Which of the server's endpoints to use: 'PROD' or 'PRE'."
)

_HEADERS_DESC = (
    "Extra HTTP headers sent to the MCP server on every call, as a name/value "
    "map."
)


class McpConfig(BaseModel):
    """The caller's own configuration for an MCP server."""

    server_code: str = Field(description=_SERVER_CODE_DESC)
    api_key: str | None = Field(
        default=None, description="Stored API key, always masked — first and "
        "last four characters for a long key, fully masked otherwise. It is "
        "never returned in full. Null when none is stored."
    )
    endpoint_env: str = Field(description=_ENDPOINT_ENV_DESC)
    transport_protocol: str | None = Field(default=None, description=_TRANSPORT_DESC)
    headers: dict[str, str] = Field(
        default_factory=dict, description=_HEADERS_DESC
    )
    has_config: bool = Field(
        description="True when a stored config row exists for this server; "
        "false only when the caller has never configured it."
    )


class McpConfigWrite(BaseModel):
    """Write the caller's configuration for an MCP server.

    Omit a field to leave it unchanged. The configuration applies to every
    device the caller owns; there is no per-device setting.
    """

    # extra="forbid": an unknown field is a 422, not a silent no-op. In
    # particular the old `sync_mode` field is gone — the only push path is to
    # every device under the caller, so a per-device mode would advertise
    # something the server ignores.

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "api_key": "sk-live-0f2c…",
                "endpoint_env": "PROD",
                "transport_protocol": "STREAMABLE_HTTP",
                "headers": {"X-Workspace": "team-alpha"},
            }
        },
    )

    api_key: str | None = Field(
        default=None, description="API key to store for this server, in full. "
        "It is returned masked on every read. Omit to leave it unchanged."
    )
    endpoint_env: Literal["PROD", "PRE"] | None = Field(
        default=None, description=f"{_ENDPOINT_ENV_DESC} Omit to leave unchanged."
    )
    transport_protocol: Literal["SSE", "STREAMABLE_HTTP"] | None = Field(
        default=None, description="How to reach the server. Omit to leave "
        "unchanged."
    )
    headers: dict[str, str] | None = Field(
        default=None, description=f"{_HEADERS_DESC} Replaces the stored map "
        "rather than merging into it. Omit to leave unchanged."
    )
