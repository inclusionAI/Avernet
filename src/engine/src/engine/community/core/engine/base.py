"""
BaseEngine — abstract base providing default plumbing for engine implementations.

Engines may either satisfy the Engine Protocol directly or extend this class
to inherit common boilerplate:
  - config storage
  - private slots for each plugin
  - default property accessors (mandatory plugins raise CapabilityNotSupportedError
    if not assigned by subclass; optional plugins return None)
  - no-op lifecycle methods (initialize, shutdown) and a default healthy
    health_check
  - a validate_capabilities() hook that detects mismatches between declared
    capabilities and assigned plugins

BaseEngine deliberately does NOT inherit from Engine — see §3.3 of
src/engine/docs/heterogeneous-engine-architecture.md. Engine is the structural
contract; BaseEngine is an opt-in helper. A subclass of BaseEngine satisfies
the Engine Protocol structurally (verified by `isinstance(obj, Engine)` since
Engine is @runtime_checkable).

Subclasses must provide `name`, `version`, `capabilities`, and assign
`self._session` and `self._chat` during construction or initialize().
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.context import AuthContext
from engine.community.core.engine.exceptions import CapabilityNotSupportedError, EngineError
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


# Plugins are bucketed by capability domain. Used by validate_capabilities()
# to cross-check declared capabilities against assigned plugin instances.
# Each entry: (plugin attribute name on BaseEngine, capabilities served by that plugin).
#
# Covers only the plugin slots that actually have concrete implementations in
# this repo today (session, chat, cron — provided by engines/openclaw/). mcp
# and skills capabilities are declared by OpenClawEngine but serviced directly
# by api/mcp.py / api/skills.py; the plugin Protocols (core/mcp, core/skills)
# exist but no engine ships an implementation — lifting these into proper
# plugins is M6. Approval/file/node/channel/health/effect are similar: Protocol
# slots exist but aren't cross-checked until M6 adds implementations.
_PLUGIN_CAPABILITY_DOMAINS: tuple[tuple[str, tuple[Capability, ...]], ...] = (
    (
        "_session",
        (
            Capability.SESSION_LIST,
            Capability.SESSION_CREATE,
            Capability.SESSION_DELETE,
            Capability.SESSION_UPDATE,
            Capability.SESSION_HISTORY,
            Capability.SESSION_ARCHIVE,
        ),
    ),
    (
        "_chat",
        (
            Capability.CHAT_STREAM,
            Capability.CHAT_COMPLETE,
            Capability.CHAT_ABORT,
            Capability.CHAT_APPROVAL,
            Capability.CHAT_HISTORY,
        ),
    ),
    (
        "_cron",
        (
            Capability.CRON_LIST,
            Capability.CRON_CREATE,
            Capability.CRON_UPDATE,
            Capability.CRON_DELETE,
            Capability.CRON_RUN,
            Capability.CRON_HISTORY,
        ),
    ),
    (
        "_models",
        (
            Capability.MODEL_LIST,
            Capability.MODEL_SWITCH,
        ),
    ),
    (
        "_approval",
        (
            Capability.APPROVAL_GET,
            Capability.APPROVAL_SET,
            Capability.APPROVAL_LIST,
        ),
    ),
    (
        "_node",
        (
            Capability.NODE_LIST,
            Capability.NODE_REGISTER,
            Capability.NODE_UNREGISTER,
            Capability.NODE_STATUS,
        ),
    ),
    (
        "_file",
        (
            Capability.FILE_READ,
            Capability.FILE_WRITE,
            Capability.FILE_UPLOAD,
            Capability.FILE_DELETE,
            Capability.FILE_LIST,
            Capability.FILE_MKDIR,
        ),
    ),
    (
        "_bash",
        (
            Capability.BASH_EXEC,
        ),
    ),
    (
        "_default_config",
        (Capability.DEFAULT_CONFIG_GET,),
    ),
    (
        "_web_shell",
        (Capability.WEB_SHELL_OPEN,),
    ),
    (
        "_skills",
        (
            Capability.SKILLS_LIST,
            Capability.SKILLS_INSTALL,
            Capability.SKILLS_UNINSTALL,
            Capability.SKILLS_UPDATE,
            Capability.SKILLS_EXECUTE,
            Capability.SKILLS_DISCOVER,
            Capability.SKILLS_SYNC_SYMLINKS,
            Capability.SKILLS_SYNC_BINDPATHS,
            Capability.SKILLS_CLEAN_SYMLINKS,
        ),
    ),
    (
        "_cli_tools",
        (
            Capability.CLI_INSTALL,
            Capability.CLI_DELETE,
            Capability.CLI_LIST,
            Capability.CLI_REPLACE,
            Capability.CLI_DOWNLOAD,
        ),
    ),
    (
        "_mcp",
        (
            Capability.MCP_LIST,
            Capability.MCP_CREATE,
            Capability.MCP_UPDATE,
            Capability.MCP_DELETE,
            Capability.MCP_START,
            Capability.MCP_STOP,
            Capability.MCP_TOOLS_LIST,
            Capability.MCP_TOOLS_CALL,
            Capability.MCP_RESOURCES_LIST,
            Capability.MCP_RESOURCES_READ,
            Capability.MCP_PROMPTS_LIST,
            Capability.MCP_PROMPTS_GET,
            Capability.MCP_FILTER_SERVERS,
        ),
    ),
)


class BaseEngine(ABC):
    """Optional ABC for engine implementations.

    Subclasses should:
      1. Implement `name`, `version`, `capabilities` as class attributes or
         properties.
      2. Assign concrete plugin instances to `self._session`, `self._chat`,
         and any optional plugins they support — typically inside __init__ or
         initialize().
      3. Override `initialize`, `shutdown`, `health_check` if non-trivial.
      4. Trust EngineManager to call `validate_capabilities()` after
         `initialize()` to surface declaration/assignment mismatches early.
    """

    def __init__(self, config: dict | None = None) -> None:
        self._config: dict = config or {}
        self._session: SessionService | None = None
        self._chat: ChatService | None = None
        self._mcp: MCPService | None = None
        self._skills: SkillsService | None = None
        self._cli_tools: CliToolsService | None = None
        self._cron: CronService | None = None
        self._models: ModelsService | None = None
        self._node: NodeService | None = None
        self._file: FileService | None = None
        self._bash: BashService | None = None
        self._default_config: DefaultConfigService | None = None
        self._web_shell: WebShellService | None = None
        self._relay: RelayService | None = None
        self._approval: ApprovalService | None = None
        self._auto_initiate: Any | None = None

    # ── Metadata (subclass must implement) ──
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        ...

    @property
    @abstractmethod
    def capabilities(self) -> EngineCapabilities:
        ...

    # ── Mandatory plugins ──
    @property
    def session(self) -> SessionService:
        if self._session is None:
            raise CapabilityNotSupportedError(self.name, Capability.SESSION_LIST)
        return self._session

    @property
    def chat(self) -> ChatService:
        if self._chat is None:
            raise CapabilityNotSupportedError(self.name, Capability.CHAT_STREAM)
        return self._chat

    # ── Optional plugins ──
    @property
    def mcp(self) -> MCPService | None:
        return self._mcp

    @property
    def skills(self) -> SkillsService | None:
        return self._skills

    @property
    def cli_tools(self) -> CliToolsService | None:
        return self._cli_tools

    @property
    def cron(self) -> CronService | None:
        return self._cron

    @property
    def models(self) -> ModelsService | None:
        return self._models

    @property
    def node(self) -> NodeService | None:
        return self._node

    @property
    def file(self) -> FileService | None:
        return self._file

    @property
    def bash(self) -> BashService | None:
        return self._bash

    @property
    def default_config(self) -> DefaultConfigService | None:
        return self._default_config

    @property
    def web_shell(self) -> WebShellService | None:
        return self._web_shell

    @property
    def relay(self) -> RelayService | None:
        return self._relay

    @property
    def approval(self) -> ApprovalService | None:
        return self._approval

    @property
    def auto_initiate(self) -> Any | None:
        """Auto-initiate plugin (optional).

        Currently only AiCodingEngine implements this. Returns the concrete
        plugin instance or None if not assigned by the engine.
        """
        return self._auto_initiate

    # ── Lifecycle (override as needed) ──
    # Base implementations don't access self; they exist as overridable hooks.
    # noinspection PyMethodMayBeStatic
    async def initialize(self) -> None:
        """Default no-op. Override to open connections, warm caches, etc."""
        return None

    # noinspection PyMethodMayBeStatic
    async def shutdown(self) -> None:
        """Default no-op. Override to close connections, flush state, etc."""
        return None

    # noinspection PyMethodMayBeStatic
    async def health_check(self) -> HealthStatus:
        """Default optimistic healthy. Override for real checks."""
        return HealthStatus(healthy=True, message="OK")

    # ── Inbound-connection lifecycle (default no-ops) ──
    # Engines that track per-connection resources override these. OpenClaw
    # forwards to its TokenClientPool so per-MCP-token upstream clients are
    # refcounted against live inbound WebSocket connections.
    # noinspection PyMethodMayBeStatic, PyUnusedLocal
    async def on_connection_open(
        self, auth: AuthContext | None = None,
    ) -> None:
        return None

    # noinspection PyMethodMayBeStatic, PyUnusedLocal
    async def on_connection_close(
        self, auth: AuthContext | None = None,
    ) -> None:
        return None

    # ── Self-consistency check ──
    def validate_capabilities(self) -> None:
        """Cross-check declared capabilities against assigned plugin instances.

        Detects two classes of bug at engine setup time:
          1. Capability declared but plugin not assigned — caller would later
             see CapabilityNotSupportedError despite the capability check passing.
          2. Plugin assigned but capability not declared — web layer would
             refuse to dispatch even though the engine could serve the request.

        EngineManager calls this after initialize(). Subclasses generally do
        not need to override.

        Raises:
            EngineError: if any inconsistency is detected. Message lists every
                mismatch so all bugs surface in one shot.
        """
        declared = self.capabilities.supported | set(self.capabilities.limited.keys())
        problems: list[str] = []

        for attr_name, domain_caps in _PLUGIN_CAPABILITY_DOMAINS:
            assigned = getattr(self, attr_name, None) is not None
            domain_set = set(domain_caps)
            declared_in_domain = declared & domain_set

            if declared_in_domain and not assigned:
                caps_str = ", ".join(sorted(c.value for c in declared_in_domain))
                problems.append(
                    f"  - declares {{{caps_str}}} but did not assign "
                    f"self.{attr_name}"
                )
            elif assigned and not declared_in_domain:
                problems.append(
                    f"  - assigned self.{attr_name} but did not declare any "
                    f"matching capabilities (expected at least one of "
                    f"{sorted(c.value for c in domain_set)})"
                )

        if problems:
            raise EngineError(
                f"Engine {self.name!r} has inconsistent capability declaration:\n"
                + "\n".join(problems)
            )


__all__ = ["BaseEngine"]
