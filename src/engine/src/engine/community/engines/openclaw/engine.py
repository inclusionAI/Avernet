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

from pathlib import Path

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
from engine.community.engines.center_content import build_center_content_adapter
from engine.community.core.adapters.openclaw.web_shell import OpenClawWebShellAdapter
from engine.community.core.bash.base import BaseBashService
from engine.community.core.engine.base import BaseEngine
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
from engine.community.plugins.openclaw.active_run_registry import ActiveRunRegistry

if TYPE_CHECKING:
    from engine.community.core.cron.services.systemevent_monitor import (
        SystemEventMonitorService,
    )

log = logging.getLogger("openclaw-engine")


#: Where this engine keeps a bot's command-line tools, as the deployment
#: defines it. A literal on purpose: the location is a property of the
#: image, not something to derive at runtime.
OPENCLAW_CLI_DIR = Path("/home/admin/.openclaw/cli")

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
        active_run_registry: ActiveRunRegistry | None = None,
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
        self._port = OpenClawPluginImpl(
            client=client,
            pool=pool,
            center_content_adapter=build_center_content_adapter(),
            active_run_registry=active_run_registry,
        )

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
        # CLI tools need no port: placing a command is local filesystem work,
        # so the only per-engine fact is the directory — stated once, as the
        # deployment's own constant. **This is the line to change if OpenClaw's
        # tool location moves.**
        self._cli_tools = LocalCliToolsService(OPENCLAW_CLI_DIR)
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

    async def query_active_sessions(
        self,
        *,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """Read-only Active Session query.

        Returns a dual-axis response derived from OpenClaw's own
        ``chat_stream`` run lifecycle (populated by ``ActiveRunRegistry``
        through ``_ChatPortMixin.chat_stream``):

        ``{query_status, verdict, engine, checked_at,
        active_session_count, sessions[...]}``

        Behavior matrix (summarized in the active-sessions design):
          * ok + empty        => verdict=clear
          * ok + ≥1 session   => verdict=active, sessions[] populated
          * stale/incomplete  => query_status=error, verdict=unknown
          * timeout           => query_status=timeout, verdict=unknown
          * exception         => query_status=error, verdict=unknown

        Never raises — failures are encoded into the response shape so the HTTP
        route can pass the envelope straight through. This method is the engine
        surface for the read-only ``GET /api/engine/active-sessions`` route;
        non-OpenClaw engines (or this engine never having been activated via
        ``EngineManager.initialize()``) are not reached here — the route itself
        returns ``query_status=unsupported, verdict=unknown`` for them.
        """
        import asyncio

        from datetime import UTC, datetime

        default_timeout_ms = 2000
        timeout_value = default_timeout_ms if timeout_ms is None else timeout_ms
        try:
            timeout_value_int = int(timeout_value)
        except (TypeError, ValueError):
            timeout_value_int = default_timeout_ms
        if timeout_value_int < 0:
            timeout_value_int = default_timeout_ms

        def _result(
            query_status: str,
            verdict: str,
            *,
            sessions: list[dict[str, Any]] | None = None,
            count: int | None = None,
            incomplete: bool = False,
            error_message: str | None = None,
        ) -> dict[str, Any]:
            data: dict[str, Any] = {
                "query_status": query_status,
                "verdict": verdict,
                "engine": self.name,
                "checked_at": datetime.now(tz=UTC).isoformat(),
                "active_session_count": count if count is not None else 0,
                "sessions": sessions or [],
            }
            if incomplete:
                data["incomplete"] = True
            if error_message:
                data["error_message"] = error_message
            return data

        registry = self._port.active_run_registry

        try:
            if timeout_value_int > 0:
                sessions, stale = await asyncio.wait_for(
                    registry.list_active_sessions_async(),
                    timeout=timeout_value_int / 1000.0,
                )
            else:
                sessions, stale = await registry.list_active_sessions_async()
        except asyncio.TimeoutError:
            return _result(
                "timeout",
                "unknown",
                error_message="active-sessions query exceeded timeout",
            )
        except Exception as exc:
            log.exception("OpenClawEngine.query_active_sessions failed: %s", exc)
            return _result(
                "error",
                "unknown",
                error_message=str(exc) or "active-sessions query failed",
            )

        if stale:
            # Data incomplete — registry couldn't observe a terminal transition
            # for at least one stale entry. The data is not query=ok;
            # the verdict must be unknown.
            return _result(
                "error",
                "unknown",
                sessions=sessions,
                count=len(sessions),
                incomplete=True,
                error_message="active-session data is incomplete (stale entries)",
            )
        return _result(
            "ok",
            "active" if sessions else "clear",
            sessions=sessions,
            count=len(sessions),
        )

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
