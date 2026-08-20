"""Request/response models for the MCP group.

Docstrings and field descriptions here are published verbatim into the OpenAPI
document external tenants read — keep them caller-facing prose. Rationale and
internal names belong in ``#`` comments.
"""

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

_SERVER_CODE_DESC = (
    "The server's unique identifier in the marketplace catalogue — an opaque, "
    "case-sensitive string, e.g. 'mcp.example.weather'. Obtain it from the "
    "server listing; do not parse it."
)

# The catalogue reads are pass-throughs of an upstream marketplace, so most
# enum-looking strings here are upstream-defined open sets: the descriptions
# name the known values without publishing a closed enum that a
# backward-compatible catalogue change would violate. Only the two request
# fields on McpConfigWrite are closed sets enforced by this API.
_TRANSPORT_RESPONSE_DESC = (
    "The MCP transport, as the catalogue publishes it — typically 'SSE' "
    "(HTTP + Server-Sent Events), 'STREAMABLE_HTTP' (streamable HTTP), or "
    "'STDIO' for a server the deployment runs locally. Echoed as published, "
    "so casing can vary; null when the catalogue declares none."
)


class McpServer(BaseModel):
    """An MCP server in the marketplace."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "server_code": "mcp.example.weather",
                "name": "Weather",
                "description": "Forecasts and current conditions.",
                "network_types": ["INTERNET"],
                "transport_protocol": "STREAMABLE_HTTP",
            }
        }
    )

    server_code: str = Field(description=_SERVER_CODE_DESC)
    name: str = Field(description="The server's display name.")
    description: str | None = Field(
        default=None,
        description="What the server offers; null when the catalogue has none.",
    )
    network_types: list[str] = Field(
        default_factory=list,
        description="Networks the server is reachable on, as the catalogue "
        "declares them: 'INTERNET' — the public internet; 'OFFICE' — the "
        "corporate network. Only servers reachable on at least one of those "
        "two (or declaring none) are visible on this API. Empty when the "
        "catalogue declares none.",
    )
    transport_protocol: str | None = Field(
        default=None, description=_TRANSPORT_RESPONSE_DESC
    )


class McpServerDetail(McpServer):
    """An MCP server's detail, including its tools."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "server_code": "mcp.example.weather",
                "name": "Weather",
                "description": "Forecasts and current conditions.",
                "network_types": ["INTERNET"],
                "transport_protocol": "STREAMABLE_HTTP",
                "tools": [
                    {
                        "name": "getForecast",
                        "description": "Forecast for a city and date range",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "city": {"type": "string"},
                                "days": {"type": "integer"},
                            },
                            "required": ["city"],
                        },
                    }
                ],
            }
        }
    )

    tools: list[dict[str, Any]] = Field(
        default_factory=list,
        description="The server's tools as the catalogue publishes them. Each "
        "entry is one MCP tool declaration — typically 'name' (the callable "
        "name), 'description', and 'inputSchema' (a JSON Schema for the "
        "tool's arguments); any further catalogue keys pass through "
        "unchanged.",
    )


class McpPermission(BaseModel):
    """The caller's permission for an MCP server."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "has_access": True,
                "access_level": "PUBLIC",
                "tool_permissions": {
                    "getForecast": {"code": "AUTHORIZED", "name": "Authorized"},
                    "setAlert": {"code": "UNAUTHORIZED", "name": "Unauthorized"},
                },
            }
        }
    )

    has_access: bool = Field(
        description="Whether the caller may use this server at all."
    )
    access_level: str | None = Field(
        default=None,
        description="The marketplace's access label for the server: 'PUBLIC' "
        "or 'PRIVATE' for marketplace servers, 'LOCAL' for a server the "
        "deployment serves itself, 'COMMUNITY' on community deployments. "
        "Empty when the marketplace could not classify the server. Not a "
        "closed set — treat unknown values as informational.",
    )
    tool_permissions: dict[str, Any] = Field(
        default_factory=dict,
        description="Per-tool authorization, keyed by tool name. Each value "
        "carries 'code' — the state, e.g. 'AUTHORIZED' or 'UNAUTHORIZED' — "
        "and 'name', a display label whose language is deployment-defined. "
        "Empty when the server is local, unknown, or declares no tools.",
    )


class McpTenant(BaseModel):
    """An MCP tenant.

    Note: an "MCP tenant" is a marketplace concept from the upstream MCP Center,
    unrelated to the Avernet data-isolation tenant. The marketplace is a shared
    catalog — every Avernet tenant sees the same MCP tenants.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "default",
                "name": "Community",
                "categories": ["Productivity", "Developer Tools"],
            }
        }
    )

    code: str = Field(
        description="The tenant's identifier in the marketplace catalogue, "
        "e.g. 'default'."
    )
    name: str = Field(description="The tenant's display name.")
    categories: list[str] = Field(
        default_factory=list,
        description="Category labels from the marketplace taxonomy — display "
        "strings for grouping, not stable machine codes.",
    )


_ENDPOINT_ENV_DESC = (
    "Which of the server's published endpoints the caller's bots connect to: "
    "'PROD' — the production endpoint (the default); 'PRE' — the "
    "pre-production endpoint, for testing against a server's staging "
    "deployment."
)


class McpConfig(BaseModel):
    """The caller's unified config for an MCP server.

    The api_key is always returned masked (first/last four characters for a
    long key, fully masked otherwise), never in full.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "server_code": "mcp.example.weather",
                "api_key": "auth****mnop",
                "endpoint_env": "PROD",
                "transport_protocol": "STREAMABLE_HTTP",
                "headers": {"x-region": "eu-1"},
                "has_config": True,
            }
        }
    )

    server_code: str = Field(description=_SERVER_CODE_DESC)
    api_key: str | None = Field(
        default=None, description="Masked API key; null when none is stored."
    )
    endpoint_env: str = Field(description=_ENDPOINT_ENV_DESC)
    transport_protocol: str | None = Field(
        default=None,
        description="The caller's stored transport preference — 'SSE' or "
        "'STREAMABLE_HTTP'; null when never set, in which case the server's "
        "own published transport applies.",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Extra HTTP headers sent to the server on every call; "
        "empty when none are stored.",
    )
    has_config: bool = Field(
        description="True when a stored config row exists for this server; "
        "false only when the caller has never configured it."
    )


class McpConfigWrite(BaseModel):
    """Write the unified config. An omitted (null) field means "leave unchanged".

    The write is pushed to every device under the caller before it is
    reported successful; a failed push rolls the change back and answers an
    upstream error.
    """

    # extra="forbid": an unknown field is a 422, not a silent no-op. In
    # particular the old sync_mode field is gone — the only push path is to
    # every device under the caller, so a per-device mode would advertise
    # something the server ignores.
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "api_key": "authorization=sk-abcdefghijklmnop",
                "endpoint_env": "PROD",
                "transport_protocol": "STREAMABLE_HTTP",
            }
        },
    )

    api_key: str | None = Field(
        default=None,
        description="New credential to store, or omit to leave unchanged. "
        "Never send back the masked value from a read — it would be stored "
        "literally and replace the real credential.",
    )
    endpoint_env: Literal["PROD", "PRE"] | None = Field(
        default=None, description=_ENDPOINT_ENV_DESC
    )
    transport_protocol: Literal["SSE", "STREAMABLE_HTTP"] | None = Field(
        default=None,
        description="Transport preference to store: 'SSE' — HTTP + "
        "Server-Sent Events; 'STREAMABLE_HTTP' — streamable HTTP. Omit to "
        "leave unchanged.",
    )
    headers: dict[str, str] | None = Field(
        default=None,
        description="Replacement header map sent to the server on every "
        "call; omit to leave unchanged.",
    )


class BotMcpItem(BaseModel):
    """An MCP server in one Bot's desired-state projection."""

    server_code: str = Field(description=_SERVER_CODE_DESC)
    active: bool = Field(description="Whether the MCP has a desired-state installation.")
