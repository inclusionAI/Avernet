"""ClaudeCodeCommunityEngine — the community Engine implementation (ACL-assembled).

The community composition root: its ``__init__`` builds the concrete
``ClaudeCodePluginImpl`` (shared profile-neutral transport — vendored
claude_code relay client, ``plugins/claude_code/``) and wraps it in the
``core/adapters/claude_code`` ACL adapters, which implement the core
``*Service`` protocols.

This is the open-source community engine. The composition root lives here in
``engines/claude_code_community/`` (mirroring ``engines/openclaw/engine.py``; NOT
``engines/claude_code/`` — that directory is the corp保护区, export-excluded, and
holds the legacy 18900-relay in-tree impl which this round does NOT touch). The
community engine connects the vendored Node relay at ``ws://127.0.0.1:18900``
(18789 is OpenClaw's gateway port; the vendored claude_code relay defaults to
18900 — see ``community/claude_code_gateway/src/server.ts``) and is the only claude_code
engine shipped in the public export tree.

Capability matrix is declared here (community-side), mirroring the corp
``engines/claude_code/capabilities.py`` matrix but owned by the community engine
(so the open-source export does not depend on the export-excluded corp module).
``bash`` reuses the core default ``BaseBashService`` (no port).
"""
from __future__ import annotations

import logging

from engine.community.core.cli_tools.directories import claude_code_cli_dir
from engine.community.core.cli_tools.service import LocalCliToolsService
from engine.community.core.adapters.claude_code.chat import ClaudeCodeChatAdapter
from engine.community.core.adapters.claude_code.cron import ClaudeCodeCronAdapter
from engine.community.core.adapters.claude_code.file import ClaudeCodeFileAdapter
from engine.community.core.adapters.claude_code.mcp import ClaudeCodeMcpAdapter
from engine.community.core.adapters.claude_code.models import ClaudeCodeModelsAdapter
from engine.community.core.adapters.claude_code.relay import ClaudeCodeRelayAdapter
from engine.community.core.adapters.claude_code.session import ClaudeCodeSessionAdapter
from engine.community.core.adapters.claude_code.skills import ClaudeCodeSkillsAdapter
from engine.community.core.bash.base import BaseBashService
from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.plugins.claude_code._base import ClaudeCodeRelayClient
from engine.community.plugins.claude_code.plugin_impl import ClaudeCodePluginImpl

log = logging.getLogger("claude-code-community-engine")


# Community-side capability matrix (mirrors corp engines/claude_code/capabilities.py
# CLAUDE_CODE_CAPABILITIES, but owned here so the OSS export tree is self-contained).
CLAUDE_CODE_COMMUNITY_CAPABILITIES: EngineCapabilities = EngineCapabilities(
    supported={
        # ── Chat ──
        Capability.CHAT_STREAM,
        Capability.CHAT_HISTORY,
        Capability.CHAT_ABORT,
        Capability.CHAT_APPROVAL,
        Capability.CHAT_INTERACTION,
        Capability.CHAT_MODE_TRANSITION,
        # ── Session ──
        Capability.SESSION_LIST,
        Capability.SESSION_DELETE,
        Capability.SESSION_UPDATE,
        Capability.SESSION_HISTORY,
        # ── MCP ──
        Capability.MCP_LIST,
        Capability.MCP_CREATE,
        Capability.MCP_UPDATE,
        Capability.MCP_DELETE,
        Capability.MCP_TOOLS_LIST,
        Capability.MCP_FILTER_SERVERS,
        # ── Skills ──
        Capability.SKILLS_LIST,
        Capability.SKILLS_INSTALL,
        Capability.SKILLS_UNINSTALL,
        Capability.SKILLS_UPDATE,
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
        # ── Cron ──
        Capability.CRON_LIST,
        Capability.CRON_CREATE,
        Capability.CRON_UPDATE,
        Capability.CRON_DELETE,
        Capability.CRON_RUN,
        Capability.CRON_HISTORY,
        # ── Model ──
        Capability.MODEL_LIST,
        Capability.MODEL_SWITCH,
        # ── File ──
        Capability.FILE_READ,
        Capability.FILE_WRITE,
        Capability.FILE_UPLOAD,
        Capability.FILE_DELETE,
        Capability.FILE_LIST,
        # ── Bash ──
        Capability.BASH_EXEC,
    },
    limited={
        Capability.SESSION_CREATE: (
            "teamclaw-aicoding-relay has no explicit sessions.create; "
            "OCB pre-allocates the sessionKey, first chat.send establishes it."
        ),
        Capability.MCP_TOOLS_CALL: (
            "Direct tool call only on SDK bridge; CLI bridge routes via chat."
        ),
        Capability.SKILLS_EXECUTE: (
            "Triggered via chat (/ <skill-name> or description), not direct exec."
        ),
    },
)


class ClaudeCodeCommunityEngine(BaseEngine):
    """claude_code community engine — assembled from the ACL over one relay port impl."""

    name = "claude_code"
    version = "1.0.0"

    _CAPABILITIES = CLAUDE_CODE_COMMUNITY_CAPABILITIES

    def __init__(
        self,
        config: dict | None = None,
        *,
        client: ClaudeCodeRelayClient | None = None,
    ) -> None:
        """Assemble the engine from the ACL over one ``ClaudeCodePluginImpl``.

        ``client`` is an optional injection seam for tests; production passes
        None (the port impl lazily connects a fresh ``ClaudeCodeRelayClient``).
        """
        super().__init__(config)
        self._injected_client = client  # None in production; set only by tests

        # The single community transport impl shared by every adapter.
        self._port = ClaudeCodePluginImpl(client=client)

        # ACL adapters implementing the core *Service protocols.
        self._chat = ClaudeCodeChatAdapter(self._port)
        self._session = ClaudeCodeSessionAdapter(self._port)
        self._mcp = ClaudeCodeMcpAdapter(self._port)
        self._skills = ClaudeCodeSkillsAdapter(self._port)
        # Claude Code keeps its own tool tree — its workspace is
        # <home>/.claude_code/workspace, not OpenClaw's.
        self._cli_tools = LocalCliToolsService(claude_code_cli_dir)
        self._cron = ClaudeCodeCronAdapter(self._port)
        self._models = ClaudeCodeModelsAdapter(self._port)
        self._file = ClaudeCodeFileAdapter(self._port)
        self._relay = ClaudeCodeRelayAdapter(self._port)
        # Bash reuses the core default (not claude_code-native; no port).
        self._bash = BaseBashService()

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPABILITIES

    # ── BaseEngine service slots ──────────────────────────────────────────

    @property
    def session(self):
        return self._session

    @property
    def chat(self):
        return self._chat

    @property
    def mcp(self):
        return self._mcp

    @property
    def skills(self):
        return self._skills

    @property
    def cron(self):
        return self._cron

    @property
    def models(self):
        return self._models

    @property
    def file(self):
        return self._file

    @property
    def bash(self):
        return self._bash

    @property
    def relay(self):
        return self._relay

    # claude_code-specific extras (not on BaseEngine core slots): the four HITL
    # resolve methods + chat inject. Exposed as engine attributes so the
    # delivery layer / engine manager can reach them. They delegate to the
    # chat adapter (which delegates to the port).

    @property
    def chat_extras(self):
        """HITL resolution surface (inject + 3 resolves), via the chat adapter."""
        return self._chat

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Best-effort eager connect of the relay client.

        Adapters also connect lazily on first use, so a connect failure here
        only delays surfacing of relay errors until the first request. Tests
        inject their own client and own its lifecycle.
        """
        if self._injected_client is not None:
            return  # tests own their client's lifecycle
        try:
            await self._port._relay()  # lazy-connect the shared client
            log.info("Client connected: claude_code community")
        except Exception as e:  # noqa: BLE001
            log.warning("Initial relay connection failed (will retry on demand): %s", e)

    async def shutdown(self) -> None:
        """Disconnect the shared relay client (tests own their injected client)."""
        if self._injected_client is not None:
            return
        try:
            client = self._port._client
            if client is not None and client.connected:
                await client.disconnect()
                log.info("Client disconnected: claude_code community")
        except Exception as e:  # noqa: BLE001
            log.warning("Relay client shutdown failed: %s", e)


__all__ = ["ClaudeCodeCommunityEngine", "CLAUDE_CODE_COMMUNITY_CAPABILITIES"]
