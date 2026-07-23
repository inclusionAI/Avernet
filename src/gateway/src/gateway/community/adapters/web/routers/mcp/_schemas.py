"""Request/response models for the MCP group."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
    """An MCP tenant."""

    code: str
    name: str
    categories: list[str] = Field(default_factory=list)


class McpConfig(BaseModel):
    """The caller's unified config for an MCP server (``api_key`` is masked)."""

    server_code: str
    api_key: str | None = None
    endpoint_env: str
    transport_protocol: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    has_config: bool


class McpConfigWrite(BaseModel):
    """Write the unified config. A null field means "leave unchanged"."""

    api_key: str | None = None
    endpoint_env: str | None = None
    transport_protocol: str | None = None
    headers: dict[str, str] | None = None
    sync_mode: str | None = None  # single | broadcast
