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

from typing import Any, Collection, Protocol, runtime_checkable

from agentclaw.community.core.config_compose.models import (
    CollectedCliTool,
    CollectedFile,
    CollectedSkill,
    ComposeRequest,
    McpComposeInput,
)


__all__ = [
    "ComposeInputCollector",
    "ManagedFilesReader",
    "PlatformOwnershipReader",
]


@runtime_checkable
class PlatformOwnershipReader(Protocol):
    """Whether the platform is the source of truth for a compose's categories (W8).

    Ownership follows the *operation*, not the bot's declarations: it is the
    platform's for the closing redeliver of a manifest apply and for the
    first artifact of a bot that carries a manifest, and the engine's for
    every runtime edit — a skill or resource upload, an MCP edit, a publish
    build. The reader also answers ``False`` for an engine family it does not
    serve and while the platform-managed switch is off. The composer turns the
    answer into the artifact's ``ownership`` map and into which source the
    file categories are read from. The engine decision is the reader's: the
    collector asks without knowing the engine.
    """

    def platform_owns(self, req: ComposeRequest) -> bool: ...


@runtime_checkable
class ManagedFilesReader(Protocol):
    """The platform's own copy of a teclaw bot's manifest-delivered files (W8).

    Store-relative refs, in the shape the collector already yields, read from
    the managed-files store rather than from the running container. The
    composer consults it only when the platform owns the compose.
    """

    def identity_files(self, req: ComposeRequest) -> list[CollectedFile]: ...

    def resources(self, req: ComposeRequest) -> list[CollectedFile]: ...

    def skills(self, req: ComposeRequest) -> list[CollectedSkill]:
        """Every local package the platform holds — the collector keeps only
        the active ones."""
        ...

    def skill_files(self, req: ComposeRequest, names: Collection[str]) -> list[CollectedFile]:
        """The named packages' files, as resources refs."""
        ...


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

    def identity_files(self, req: ComposeRequest) -> list[CollectedFile]:
        """User/platform-authored identity files (NOT engine-generated ones)."""
        ...

    def cli_tools(self, req: ComposeRequest) -> list[CollectedCliTool]:
        """Platform-managed command-line tools, from ``ac_bot_cli_tool`` (W9).

        Read from the platform's own table on **every** compose, not from a
        managed-files store and not from the container: this category is always
        platform-managed, like ``mcp``, so there is no engine-owned reading of
        it to fall back to and no switch to consult.
        """
        ...

    def engine_overrides(self, req: ComposeRequest) -> dict[str, Any]:
        """Bot-level engine override config (free-form, engine-interpreted)."""
        ...
