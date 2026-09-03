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

from agentclaw.community.kernel.bot_config import OwnershipCategory
from agentclaw.community.core.config_compose.models import (
    CollectedFile,
    CollectedSkill,
    ComposeRequest,
    McpComposeInput,
)


__all__ = [
    "ComposeInputCollector",
    "ManagedFilesReader",
    "PlatformManagedCategoriesReader",
]


@runtime_checkable
class PlatformManagedCategoriesReader(Protocol):
    """Which artifact categories the platform asserts for a bot (W8).

    Answered from the bot's stored manifest and the platform-managed switch:
    the file categories the manifest declares (``IDENTITY_FILES``,
    ``RESOURCES``, ``SKILLS``), or the empty set for an engine the reader does
    not serve, when the switch is off, or when the bot has no manifest. The
    composer turns the answer into the artifact's ``ownership`` map and into
    which source it reads the file categories from. The engine decision is
    the reader's: the collector asks without knowing the engine.
    """

    def platform_managed(self, req: ComposeRequest) -> frozenset[OwnershipCategory]: ...


@runtime_checkable
class ManagedFilesReader(Protocol):
    """The platform's own copy of a teclaw bot's manifest-delivered files (W8).

    Store-relative refs, in the shape the collector already yields, read from
    the managed-files store rather than from the running container. The
    composer consults it only for a category the platform asserts.
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

    def engine_overrides(self, req: ComposeRequest) -> dict[str, Any]:
        """Bot-level engine override config (free-form, engine-interpreted)."""
        ...
