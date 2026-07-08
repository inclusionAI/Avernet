"""
MCP (Model Context Protocol) data models.

See src/engine/docs/heterogeneous-engine-architecture.md §6.2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TransportType(Enum):
    """How an MCP server exposes itself to the host."""

    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


class MCPServerStatus(Enum):
    """Runtime state of an MCP server."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class MCPServerConfig:
    """Declared configuration for an MCP server.

    `server_code` is the stable identifier used to look up the server.
    Transport-dependent fields:
      - HTTP / SSE: `url` (and optional `headers`)
      - stdio: `command` + `args` (and optional `env`)
    """

    server_code: str
    transport: TransportType = TransportType.SSE
    description: str | None = None
    url: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 30
    enabled: bool = True


@dataclass
class MCPTool:
    """A tool exposed by an MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any]
    server_code: str


@dataclass
class MCPResource:
    """A resource (file, URL, etc.) exposed by an MCP server."""

    uri: str
    name: str
    description: str | None
    mime_type: str | None
    server_code: str


@dataclass
class MCPPrompt:
    """A prompt template exposed by an MCP server."""

    name: str
    description: str
    arguments: list[dict[str, Any]]
    server_code: str


@dataclass
class MCPServer:
    """A configured MCP server with its current status and exposed inventory."""

    config: MCPServerConfig
    status: MCPServerStatus
    tools: list[MCPTool] = field(default_factory=list)
    resources: list[MCPResource] = field(default_factory=list)
    prompts: list[MCPPrompt] = field(default_factory=list)


@dataclass
class MCPToolCallRequest:
    """Request to invoke a tool.

    `server_code` is optional: when None, the engine resolves the server
    automatically by scanning tool registrations.
    """

    tool_name: str
    arguments: dict[str, Any]
    server_code: str | None = None


@dataclass
class MCPToolCallResult:
    """Result of a tool invocation.

    `content` follows the MCP content format — a list of dicts with shape
    `{"type": "text" | "image" | ..., ...}`. Passing through as-is lets
    callers handle all MCP content types without this layer re-parsing.
    """

    tool_name: str
    server_code: str
    content: list[dict[str, Any]]
    is_error: bool = False


@dataclass
class MCPFilterRequest:
    """Request to apply a server allow-list to the engine's MCP layer.

    `server_codes` is the list of server codes to keep enabled — every other
    configured server should be filtered out. An empty list means "disable
    everything"; the engine implementation decides how that materialises
    (e.g. OpenClaw passes a sentinel to ``mcporter filter-servers``).
    `timeout_seconds` bounds the engine-side operation.
    """

    server_codes: list[str] = field(default_factory=list)
    timeout_seconds: int = 30


@dataclass
class MCPFilterResult:
    """Result of :meth:`MCPService.filter_servers`.

    Engines that wrap an external command (e.g. OpenClaw's ``mcporter``)
    surface the command-line and stdout/stderr so frontends can render
    diagnostics; engines that filter in-process leave them empty.
    """

    server_codes: list[str]
    command: list[str] = field(default_factory=list)
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""


__all__ = [
    "MCPFilterRequest",
    "MCPFilterResult",
    "MCPPrompt",
    "MCPResource",
    "MCPServer",
    "MCPServerConfig",
    "MCPServerStatus",
    "MCPTool",
    "MCPToolCallRequest",
    "MCPToolCallResult",
    "TransportType",
]
