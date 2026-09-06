"""OpenClawEngine — the reference Engine implementation (ACL-assembled, F2).

The engine aggregate is the F2 **composition root**: its `__init__` builds the
concrete `OpenClawPluginImpl` (community transport — gateway client + token pool +
local OS) and wraps it in the `core/adapters/openclaw` ACL adapters, which
implement the core `*Service` protocols. `bash` reuses the core default
`BaseBashService` (no port). This is the only site that imports
`plugins/community/openclaw` — `engines → plugins` is contract-legal (core/api never
touch plugins). The idiomatic injector wiring (`di/modules/openclaw_module.py` +
request-scoped `Injected()`) lands in F5.

Behavior is preserved from the pre-F2 engine: `initialize()` eager-connects the
default gateway client and starts the SystemEvent monitor; `shutdown()` stops the
monitor, then tears down the token pool, then disconnects the default client.
The token pool (owned by the port impl) is exposed via `token_pool` so the
OpenClaw WS server can `register`/`release` on handshake/disconnect.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from engine.community.core.adapters.openclaw.approval import OpenClawApprovalAdapter
from engine.community.core.adapters.openclaw.chat import OpenClawChatAdapter
from engine.community.core.adapters.openclaw.cron import OpenClawCronAdapter
from engine.community.core.adapters.openclaw.default_config import OpenClawDefaultConfigAdapter
from engine.community.core.adapters.openclaw.file import OpenClawFileAdapter
from engine.community.core.adapters.openclaw.mcp import OpenClawMcpAdapter
from engine.community.core.adapters.openclaw.models import OpenClawModelsAdapter
from engine.community.core.adapters.openclaw.node import OpenClawNodeAdapter
from engine.community.core.adapters.openclaw.relay import OpenClawRelayAdapter
from engine.community.core.adapters.openclaw.session import OpenClawSessionAdapter
from engine.community.core.adapters.openclaw.skills import OpenClawSkillsAdapter
from engine.community.core.adapters.openclaw.web_shell import OpenClawWebShellAdapter
from engine.community.core.bash.base import BaseBashService
from engine.community.core.engine.base import BaseEngine
from engine.community.core.cli_tools.directories import cli_dir_resolver
from engine.community.core.cli_tools.service import LocalCliToolsService
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.context import AuthContext
from engine.community.openclaw.client.gateway_client import (
    OpenClawGatewayClient,
    close_client,
    get_client,
)
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl
from engine.community.plugins.openclaw.token_pool import TokenClientPool

if TYPE_CHECKING:
    from engine.community.core.cron.services.systemevent_monitor import (
        SystemEventMonitorService,
    )

log = logging.getLogger("openclaw-engine")


class OpenClawEngine(BaseEngine):
    """OpenClaw engine — assembled from the ACL over one gateway port impl."""

    name = "openclaw"
    version = "1.0.0"

    # Transcribed from heterogeneous-engine-architecture.md §4.2
    # `OPENCLAW_CAPABILITIES`. Keep this in sync with the doc; that's the
    # single source of truth for the capability matrix across engines.
    _CAPABILITIES = EngineCapabilities(
        supported={
            # Session
            Capability.SESSION_LIST,
            Capability.SESSION_CREATE,
            Capability.SESSION_DELETE,
            Capability.SESSION_UPDATE,
            Capability.SESSION_HISTORY,
            # Chat
            Capability.CHAT_STREAM,
            Capability.CHAT_COMPLETE,
            Capability.CHAT_ABORT,
            Capability.CHAT_APPROVAL,
            Capability.CHAT_HISTORY,
            # MCP (full)
            Capability.MCP_LIST,
            Capability.MCP_CREATE,
            Capability.MCP_UPDATE,
            Capability.MCP_DELETE,
            Capability.MCP_TOOLS_LIST,
            Capability.MCP_TOOLS_CALL,
            Capability.MCP_RESOURCES_LIST,
            Capability.MCP_RESOURCES_READ,
            Capability.MCP_PROMPTS_LIST,
            Capability.MCP_PROMPTS_GET,
            Capability.MCP_FILTER_SERVERS,
            # Skills (OpenClaw uses bulk symlink ops; per-skill ops are
            # not exposed — see core/adapters/openclaw/skills.py).
            Capability.SKILLS_SYNC_SYMLINKS,
            Capability.SKILLS_SYNC_BINDPATHS,
            Capability.SKILLS_CLEAN_SYMLINKS,
            Capability.SKILLS_CENTER_ENSURE,
            # CLI tools (W9) — model-callable binaries placed by a manifest.
            Capability.CLI_INSTALL,
            Capability.CLI_DELETE,
            Capability.CLI_LIST,
            Capability.CLI_REPLACE,
            Capability.CLI_DOWNLOAD,
            # Approval
            Capability.APPROVAL_GET,
            Capability.APPROVAL_SET,
            # File
            Capability.FILE_READ,
            Capability.FILE_WRITE,
            Capability.FILE_UPLOAD,
            Capability.FILE_DELETE,
            Capability.FILE_LIST,
            # Bash
            Capability.BASH_EXEC,
            # Node
            Capability.NODE_LIST,
            Capability.NODE_REGISTER,
            Capability.NODE_STATUS,
            # Channel
            Capability.CHANNEL_CONFIG_GET,
            Capability.CHANNEL_CONFIG_SET,
            Capability.CHANNEL_STATUS,
            # Cron
            Capability.CRON_LIST,
            Capability.CRON_CREATE,
            Capability.CRON_UPDATE,
            Capability.CRON_DELETE,
            Capability.CRON_RUN,
            Capability.CRON_HISTORY,
            # Model
            Capability.MODEL_LIST,
            Capability.MODEL_SWITCH,
            # Default config
            Capability.DEFAULT_CONFIG_GET,
            # Web shell
            Capability.WEB_SHELL_OPEN,
        },
        limited={
            Capability.MCP_START: "通过 mcporter 命令启动",
            Capability.MCP_STOP: "通过 mcporter 命令停止",
        },
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPABILITIES

    def __init__(
        self,
        config: dict | None = None,
        *,
        client: OpenClawGatewayClient | None = None,
        pool: TokenClientPool | None = None,
    ) -> None:
        """Assemble the engine from the ACL over one `OpenClawPluginImpl`.

        `client` / `pool` are optional injection seams for tests; production
        passes neither (the port impl reuses the shared default client + owns a
        fresh `TokenClientPool`).
        """
        super().__init__(config)
        self._injected_client = client  # None in production; set only by tests
        self._injected_pool = pool  # None in production

        # The single production transport impl shared by every adapter.
        self._port = OpenClawPluginImpl(client=client, pool=pool)

        # ACL adapters implementing the core *Service protocols.
        self._session = OpenClawSessionAdapter(self._port)
        self._chat = OpenClawChatAdapter(self._port)
        self._cron = OpenClawCronAdapter(self._port)
        self._relay = OpenClawRelayAdapter(self._port)
        self._approval = OpenClawApprovalAdapter(self._port)
        self._models = OpenClawModelsAdapter(self._port)
        self._node = OpenClawNodeAdapter(self._port)
        self._mcp = OpenClawMcpAdapter(self._port)
        self._skills = OpenClawSkillsAdapter(self._port)
        # CLI tools need no port: placing a command is local filesystem work.
        # Where they land is a deployment fact — override with BOT_CLI_DIR_OPENCLAW
        # or BOT_CLI_DIR, or add an entry to ENGINE_CLI_DIRS; see
        # core/cli_tools/directories.py.
        self._cli_tools = LocalCliToolsService(cli_dir_resolver("openclaw"))
        self._file = OpenClawFileAdapter(self._port)
        self._default_config = OpenClawDefaultConfigAdapter(self._port)
        self._web_shell = OpenClawWebShellAdapter(self._port)
        # Bash reuses the core default (not OpenClaw-native; no port).
        self._bash = BaseBashService()

        # SystemEvent monitor — OpenClaw-specific cron worker that replaces
        # `systemEvent`-typed jobs with `agentTurn` jobs. Lifecycle below.
        self._systemevent_monitor: SystemEventMonitorService | None = None

    @property
    def token_pool(self) -> TokenClientPool:
        """Expose the pool so the (still-OpenClaw-specific) WS server can call
        `register` / `release` on handshake / disconnect."""
        return self._port.pool

    async def initialize(self) -> None:
        """Best-effort eager connect of the shared gateway client + start the
        SystemEvent monitor.

        Adapters also connect lazily on first use, so gateway-connect failure
        here only delays surfacing of gateway errors until first request.
        SystemEvent monitor failure is also non-fatal — logged and skipped.

        Tests inject their own client (`_injected_client is not None`) and own
        its lifecycle; the monitor is a background asyncio task that would dirty
        the test loop, so we skip it on the test path too.
        """
        if self._injected_client is not None:
            return  # tests own their client's lifecycle
        try:
            await get_client()
            log.info("Client connected: openclaw")
        except Exception as e:
            log.warning(f"Initial client connection failed (will retry on demand): {e}")
        await self._start_systemevent_monitor()

    async def _start_systemevent_monitor(self) -> None:
        """Start the SystemEvent → agentTurn replacement worker.

        Lives inside the engine (not EngineManager) so the manager's cron
        startup stays engine-agnostic. Failure is logged but non-fatal — cron
        jobs still run via the polling service; only the auto-replace feature is
        degraded.
        """
        try:
            from engine.community.core.cron.services.systemevent_monitor import (
                SystemEventMonitorService,
            )

            self._systemevent_monitor = SystemEventMonitorService(
                engine=self.name,
                cron_api=self._cron,
                poll_interval_secs=5,
                default_timeout_secs=86400,
                default_model=None,
            )
            await self._systemevent_monitor.start()
            log.info("SystemEvent monitor started")
        except Exception as e:
            log.error(f"Failed to start SystemEvent monitor: {e}")
            self._systemevent_monitor = None

    async def shutdown(self) -> None:
        """Stop SystemEvent monitor, then disconnect per-token pool, then the
        module-level default client.

        Order matters: the monitor uses the cron adapter (which uses the gateway
        client), so it must be torn down before we tear the client out from
        under it.
        """
        if self._systemevent_monitor is not None:
            try:
                await self._systemevent_monitor.stop()
                log.info("SystemEvent monitor stopped")
            except Exception as e:
                log.warning(f"SystemEvent monitor shutdown failed: {e}")
            self._systemevent_monitor = None

        # Shut down the pool first — per-token clients should be torn down
        # before we drop the default singleton. Tests that injected their own
        # pool keep ownership.
        if self._injected_pool is None:
            try:
                await self._port.pool.shutdown()
            except Exception as e:
                log.warning(f"Token pool shutdown failed: {e}")

        # Tests that injected their own default client manage its lifecycle
        # themselves; skip the module-level disconnect in that case.
        if self._injected_client is not None:
            return
        try:
            await close_client()
            log.info("Client disconnected: openclaw")
        except Exception as e:
            log.warning(f"Close client failed: {e}")

    # ── Inbound-connection lifecycle — refcount the token pool ──
    async def on_connection_open(
        self, auth: AuthContext | None = None,
    ) -> None:
        """Register this inbound connection on the pool.

        `pool.register` is synchronous (pure dict mutation) — wrapped here as
        async only to satisfy the Protocol. No-op when no token present. The
        core `AuthContext` is unwrapped to its token at this boundary (the pool
        is token-keyed and leaf-side).
        """
        self._port.pool.register(auth.token if auth else None)

    async def on_connection_close(
        self, auth: AuthContext | None = None,
    ) -> None:
        """Release this inbound connection from the pool.

        Disconnects and drops the per-token upstream client when the last
        reference leaves. No-op when no token present.
        """
        await self._port.pool.release(auth.token if auth else None)


__all__ = ["OpenClawEngine"]
