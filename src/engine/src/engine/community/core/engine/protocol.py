"""
Engine Protocol — the structural interface every engine must satisfy.

EngineManager talks to engines exclusively through this Protocol. Each engine
implementation (under engines/<name>/) provides the listed properties and
lifecycle methods. Two plugins are mandatory (session, chat); the rest are
optional and may return None when the engine doesn't implement them.

See src/engine/docs/heterogeneous-engine-architecture.md §3.1.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from engine.community.core.engine.capability import EngineCapabilities
from engine.community.core.engine.context import AuthContext
from engine.community.core.engine.health import HealthStatus

if TYPE_CHECKING:
    from engine.community.core.approval.protocol import ApprovalService
    from engine.community.core.bash.protocol import BashService
    from engine.community.core.chat.protocol import ChatService
    from engine.community.core.cli_tools.protocol import CliToolsService
    from engine.community.core.cron.protocol import CronService
    from engine.community.core.default_config.protocol import DefaultConfigService
    from engine.community.core.file.protocol import FileService
    from engine.community.core.mcp.protocol import MCPService
    from engine.community.core.models.protocol import ModelsService
    from engine.community.core.node.protocol import NodeService
    from engine.community.core.relay.protocol import RelayService
    from engine.community.core.session.protocol import SessionService
    from engine.community.core.skills.protocol import SkillsService
    from engine.community.core.web_shell.protocol import WebShellService


@runtime_checkable
class Engine(Protocol):
    """Structural interface every engine implementation must satisfy.

    Mandatory plugins (`session`, `chat`) must be non-None. Optional plugins
    return None when the engine does not implement that capability — callers
    should also consult `capabilities.supports(...)` before dispatching.
    """

    # ── Metadata ──
    @property
    def name(self) -> str:
        """Stable identifier (e.g. 'openclaw', 'aicoding')."""
        ...

    @property
    def version(self) -> str:
        """Engine implementation version."""
        ...

    @property
    def capabilities(self) -> EngineCapabilities:
        """Declared capabilities for this engine."""
        ...

    # ── Mandatory plugins ──
    @property
    def session(self) -> SessionService:
        """Session management plugin (required)."""
        ...

    @property
    def chat(self) -> ChatService:
        """Chat plugin (required)."""
        ...

    # ── Optional plugins ──
    # Additional optional plugins (approval, file, node, channel, health, effect)
    # are added as their Protocols land in core/<feature>/protocol.py.

    @property
    def mcp(self) -> MCPService | None:
        """MCP plugin, or None if unsupported."""
        ...

    @property
    def skills(self) -> SkillsService | None:
        """Skills plugin, or None if unsupported."""
        ...

    @property
    def cli_tools(self) -> CliToolsService | None:
        """CLI tools plugin, or None if unsupported."""
        ...

    @property
    def cron(self) -> CronService | None:
        """Cron plugin, or None if unsupported."""
        ...

    @property
    def models(self) -> ModelsService | None:
        """Model-catalogue plugin, or None if the engine exposes a fixed set."""
        ...

    @property
    def node(self) -> NodeService | None:
        """Node-registry plugin, or None if the engine has no node concept."""
        ...

    @property
    def file(self) -> FileService | None:
        """Workspace-FS plugin, or None if the engine has no workspace."""
        ...

    @property
    def bash(self) -> BashService | None:
        """Bash execution plugin, or None if unsupported."""
        ...

    @property
    def default_config(self) -> DefaultConfigService | None:
        """Default-config plugin, or None if the engine has no default
        config to surface."""
        ...

    @property
    def web_shell(self) -> WebShellService | None:
        """In-pod debug-terminal plugin, or None if the engine doesn't
        ship one."""
        ...

    @property
    def relay(self) -> RelayService | None:
        """Transparent-relay plugin for engines fronting an upstream process.

        Non-None for engines that want the server to forward unknown methods
        / raw frames through to their upstream (OpenClaw's gateway pattern).
        None for engines that handle every supported method through a
        dedicated plugin and want unknown methods to 501.
        """
        ...

    @property
    def approval(self) -> ApprovalService | None:
        """Session-level approval-mode plugin, or None if unsupported.

        Non-None for engines that expose a session-level approval policy
        toggle (OpenClaw's `exec.approvals.get/set`). None for engines that
        manage approvals differently (AiCoding has no session-level policy)
        — the HTTP route 501s in that case via `check_capability`.
        """
        ...

    # ── Lifecycle ──
    async def initialize(self) -> None:
        """Set up resources (open connections, load config, etc.)."""
        ...

    async def shutdown(self) -> None:
        """Tear down resources cleanly."""
        ...

    async def health_check(self) -> HealthStatus:
        """Report current liveness/readiness."""
        ...

    # ── Inbound-connection lifecycle ──
    # The generic WebSocket server calls these on handshake / disconnect so
    # engine-owned per-connection resources (upstream pooling, tenant tracking,
    # auth-scoped caches) stay an engine implementation detail. BaseEngine
    # provides no-op defaults; OpenClaw forwards to its TokenClientPool.
    async def on_connection_open(
        self, auth: AuthContext | None = None,
    ) -> None:
        """Called when an inbound WebSocket connection has completed handshake."""
        ...

    async def on_connection_close(
        self, auth: AuthContext | None = None,
    ) -> None:
        """Called when an inbound WebSocket connection has closed."""
        ...


__all__ = ["Engine"]
