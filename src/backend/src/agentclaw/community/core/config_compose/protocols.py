"""Protocols the ConfigComposer depends on.

Following the codebase convention (cf. ``core/mcp/services/repositories.py``'s
``BotMCPProvider``), the composer depends on a narrow Protocol rather than on
concrete cross-module services. The concrete implementation fans out to the
existing parsers — ``get_symlink_mappings``, ``collect_bot_active_mcps`` +
``build_mcp_sync_payload``, ``ResourceService.list_resources``,
``IdentityService.list_*_files`` — and is wired at DI time. Keeping it behind a
Protocol means the composer needs no cross-module imports and is unit-testable
with a fake collector.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.config_compose.models import (
    CollectedFile,
    CollectedSkill,
    ComposeRequest,
    McpComposeInput,
)


__all__ = ["ComposeInputCollector"]


@runtime_checkable
class ComposeInputCollector(Protocol):
    """Gathers a bot's compose inputs from the various source-of-truth services.

    Each method returns *container-view* / merged data; turning it into the
    portable artifact (source resolution, secret-by-reference) is the composer's
    job, not the collector's.
    """

    def skills(self, req: ComposeRequest) -> list[CollectedSkill]:
        """Active skills (shared + user) with container-view sources."""
        ...

    def mcps(self, req: ComposeRequest) -> list[McpComposeInput]:
        """Active MCP servers with merged per-server config (api_key/headers/…)."""
        ...

    def resources(self, req: ComposeRequest) -> list[CollectedFile]:
        """Bot resource files (or URL resources) with their sources."""
        ...

    def bot_files(self, req: ComposeRequest) -> list[CollectedFile]:
        """Teclaw workspace files tracked in ac_file (empty for non-teclaw)."""
        ...

    def identity_files(self, req: ComposeRequest) -> list[CollectedFile]:
        """User/platform-authored identity files (NOT engine-generated ones)."""
        ...

    def engine_overrides(self, req: ComposeRequest) -> dict[str, Any]:
        """Bot-level engine override config (free-form, engine-interpreted)."""
        ...
