"""Request/response models for the MCP group.

Docstrings and field descriptions here are published verbatim into the OpenAPI
document external tenants read — keep them caller-facing prose. Rationale and
internal names belong in ``#`` comments.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.schemas_runtime_projection import (
    DesiredStateResult,
    RuntimeProjectionResult,
)

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


class McpDocs(BaseModel):
    """Marketplace documentation, preserving legacy detail fields."""

    model_config = ConfigDict(extra="allow")

    overview: Any | None = Field(
        default=None,
        description="The server overview as published by the marketplace.",
    )


class McpVendor(BaseModel):
    """Marketplace vendor object, preserving legacy extension fields."""

    model_config = ConfigDict(extra="allow")

    name: Any | None = Field(default=None, description="The vendor display name.")


class McpEndpoint(BaseModel):
    """A marketplace endpoint equivalent to the legacy detail response."""

    model_config = ConfigDict(extra="allow")

    network_type: Any | None = Field(
        default=None, description="The endpoint network type."
    )
    network_types: Any | None = Field(
        default=None, description="All endpoint network types when supplied."
    )
    transport_protocol: Any | None = Field(
        default=None, description=_TRANSPORT_RESPONSE_DESC
    )
    env: Any | None = Field(default=None, description="The endpoint environment.")
    url: Any | None = Field(default=None, description="The endpoint URL.")
    headers: Any | None = Field(
        default=None,
        description="Endpoint headers exactly as returned by the legacy market detail.",
    )


class McpPerson(BaseModel):
    """Marketplace identity equivalent to the legacy detail response."""

    model_config = ConfigDict(extra="allow")

    user_id: Any | None = Field(default=None, description="Marketplace user id.")
    user_name: Any | None = Field(default=None, description="Marketplace display name.")


class McpServerDetail(McpServer):
    """MCP marketplace detail preserving legacy business content."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "server_code": "mcp.example.weather",
                "source": "internal",
                "name": "Weather",
                "icon": "",
                "description": "Forecasts and current conditions.",
                "status": "ONLINE",
                "run_mode": "remote",
                "host_platform": "",
                "platform_server_code": "",
                "host_app_name": "",
                "site": "",
                "tenant": "",
                "category": "",
                "access_level": "PUBLIC",
                "network_types": ["INTERNET"],
                "transport_protocol": "STREAMABLE_HTTP",
                "docs": {"overview": "# Weather"},
                "endpoints": [
                    {
                        "network_type": "INTERNET",
                        "transport_protocol": "STREAMABLE_HTTP",
                        "env": "PROD",
                        "url": "https://mcp.example.invalid/mcp",
                        "headers": {},
                    }
                ],
                "stdio_configs": None,
                "bu_code": "",
                "product_code": "",
                "arch_domain_code": "",
                "creator": {"user_id": "1001", "user_name": "Creator"},
                "owner": {"user_id": "1002", "user_name": "Owner"},
                "owners_info": [],
                "tools": [],
                "tags": [],
                "code_repo_url": "",
                "launch_channels": [],
                "vendor": "",
            }
        },
    )

    server_code: Any = Field(default="", description=_SERVER_CODE_DESC)
    name: Any = Field(default="", description="The server display name.")
    description: Any | None = Field(
        default=None, description="The marketplace server description."
    )
    network_types: Any = Field(
        default_factory=list, description="The legacy marketplace network types."
    )
    transport_protocol: Any | None = Field(
        default=None, description=_TRANSPORT_RESPONSE_DESC
    )
    source: Any | None = Field(default=None, description="The marketplace source of the server.")
    icon: Any | None = Field(default=None, description="The server icon or icon reference.")
    status: Any | None = Field(default=None, description="The publication or availability status.")
    run_mode: Any | None = Field(default=None, description="The execution mode published by the marketplace.")
    host_platform: Any | None = Field(default=None, description="The host platform for the server.")
    platform_server_code: Any | None = Field(default=None, description="The platform-specific server identifier.")
    host_app_name: Any | None = Field(default=None, description="The host application name.")
    site: Any | None = Field(default=None, description="The marketplace site associated with the server.")
    tenant: Any | None = Field(default=None, description="The marketplace tenant associated with the server.")
    category: Any | None = Field(default=None, description="The marketplace category of the server.")
    access_level: Any | None = Field(default=None, description="The access level published by the marketplace.")
    docs: McpDocs | str | list[Any] | int | float | bool | None = Field(default=None, description="The server documentation published by the marketplace.")
    endpoints: (
        list[McpEndpoint | str | int | float | bool | None]
        | dict[str, Any]
        | str
        | int
        | float
        | bool
        | None
    ) = Field(default_factory=list, description="The server endpoints published by the marketplace.")
    stdio_configs: Any | None = Field(default=None, description="Local stdio launch configuration, when supplied.")
    bu_code: Any | None = Field(default=None, description="The business unit code.")
    product_code: Any | None = Field(default=None, description="The product code.")
    arch_domain_code: Any | None = Field(default=None, description="The architecture domain code.")
    creator: McpPerson | str | list[Any] | int | float | bool | None = Field(default=None, description="The creator identity published by the marketplace.")
    owner: McpPerson | str | list[Any] | int | float | bool | None = Field(default=None, description="The owner identity published by the marketplace.")
    owners_info: Any | None = Field(default=None, description="Additional owner information published by the marketplace.")
    tools: Any = Field(
        default_factory=list,
        description="Legacy tool declarations with internal extInfo removed.",
    )
    tags: Any = Field(default_factory=list, description="The marketplace tags assigned to the server.")
    code_repo_url: Any | None = Field(default=None, description="The source repository URL, when supplied.")
    launch_channels: Any = Field(default_factory=list, description="The channels through which the server can be launched.")
    vendor: McpVendor | str | list[Any] | int | float | bool | None = Field(default=None, description="The vendor information published by the marketplace.")


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
    active: bool = Field(
        description="Whether the MCP has a desired-state installation."
    )
    desired_state: DesiredStateResult | None = Field(
        default=None,
        description="Present on an activate/deactivate response; the durable Desired State result.",
    )
    runtime_projection: RuntimeProjectionResult | None = Field(
        default=None,
        description="Present on an activate/deactivate response; observed Runtime convergence.",
    )
