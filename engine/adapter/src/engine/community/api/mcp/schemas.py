"""MCP router HTTP schemas."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

TransportType = Literal["stdio", "http", "sse"]


class MCPServerConfig(BaseModel):
    server_code: str = Field(..., description="MCP Server 唯一主键")
    description: Optional[str] = Field(default=None, description="MCP Server 描述")
    transport: TransportType = Field(default="sse", description="传输类型")
    url: Optional[str] = Field(default=None, description="远程 MCP Server 地址")
    command: Optional[str] = Field(default=None, description="本地 MCP Server 启动命令")
    args: list[str] = Field(default_factory=list, description="本地启动参数")
    env: dict[str, str] = Field(default_factory=dict, description="环境变量")
    headers: dict[str, str] = Field(default_factory=dict, description="远程请求头")
    timeout_seconds: Optional[int] = Field(default=30, description="请求超时时间（秒）")
    enabled: bool = Field(default=True, description="是否启用")


class MCPServerCreateRequest(BaseModel):
    server_code: str = Field(..., description="MCP Server 唯一主键")
    description: Optional[str] = Field(default=None, description="MCP Server 描述")
    transport: Optional[str] = Field(default=None, description="传输类型")
    url: Optional[str] = Field(default=None, description="远程 MCP Server 地址")
    command: Optional[str] = Field(default=None, description="本地 MCP Server 启动命令")
    args: list[str] = Field(default_factory=list, description="本地启动参数")
    env: dict[str, str] = Field(default_factory=dict, description="环境变量")
    headers: dict[str, str] = Field(default_factory=dict, description="远程请求头")
    timeout_seconds: Optional[int] = Field(default=30, description="请求超时时间（秒）")
    enabled: bool = Field(default=True, description="是否启用")


class MCPServerUpdateRequest(BaseModel):
    description: Optional[str] = Field(default=None, description="MCP Server 描述")
    transport: Optional[str] = Field(default=None, description="传输类型")
    url: Optional[str] = Field(default=None, description="远程 MCP Server 地址")
    command: Optional[str] = Field(default=None, description="启动命令")
    args: Optional[list[str]] = Field(default=None, description="启动参数")
    env: Optional[dict[str, str]] = Field(default=None, description="环境变量")
    headers: Optional[dict[str, str]] = Field(default=None, description="远程请求头")
    timeout_seconds: Optional[int] = Field(default=None, description="请求超时时间（秒）")
    enabled: Optional[bool] = Field(default=None, description="是否启用")


class MCPServerOperationRequest(BaseModel):
    reason: Optional[str] = Field(default=None, description="操作原因")


class MCPFilterServersRequest(BaseModel):
    server_codes: list[str] = Field(default_factory=list, description="需要保留的 server_code 列表")
    timeout_seconds: Optional[int] = Field(default=30, description="命令执行超时时间（秒）")


class MCPCallToolRequest(BaseModel):
    tool: str = Field(..., description="MCP 工具全限定名，如 mcp.ant.faas.skylarkmcpserver...skylark_resolve_url")
    args: list[str] = Field(default_factory=list, description="工具参数，如 ['url=...']")
    timeout_seconds: Optional[int] = Field(default=30, description="命令执行超时时间（秒）")


__all__ = [
    "TransportType",
    "MCPServerConfig",
    "MCPServerCreateRequest",
    "MCPServerUpdateRequest",
    "MCPServerOperationRequest",
    "MCPCallToolRequest",
    "MCPFilterServersRequest",
]
