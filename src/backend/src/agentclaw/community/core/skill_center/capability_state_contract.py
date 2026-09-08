"""Core contract for reading a Bot's active capability state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    # Deferred: skills_pool consumers import this contract while their own
    # package __init__ is still executing, so a module-level import of
    # skills_pool.models here would close an import cycle.
    from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset


@dataclass(frozen=True)
class BotCapabilitySnapshot:
    """One reader-backed projection input, never cached across mutations.

    This groups reads after one synchronization, not a database isolation
    guarantee. Policy defaults remain the effective-MCP collector's concern.
    """

    bot_id: str
    owner_id: str
    skills: tuple[RegisteredSkillAsset, ...]
    installed_mcp_server_codes: frozenset[str]


@runtime_checkable
class BotCapabilityStateReaderProtocol(Protocol):
    """The one read model for a Bot's active capabilities.

    Installation is the active-identity source of truth.  The configured
    migration mode materializes either full legacy SkillSet state or only the
    applicable Default+exclusion state before effective reads.  The reader
    never triggers a runtime projection.
    """

    def member_skill_ids(self, *, bot: Mapping[str, Any]) -> frozenset[int]:
        """Read which Skills the Bot's Sets bring to it, without a flush.

        The listing filter needs this half of the flush plan: a bridged
        Skill belongs on the page even though only a Set ties it to the Bot.
        """
        ...

    def initialize_installations(
        self,
        *,
        bot_id: str,
        owner_id: str,
        bot: Mapping[str, Any] | None = None,
    ) -> None:
        """Initialize a newly persisted Bot's Installation rows without Runtime I/O."""
        ...

    def synchronize_installations(
        self,
        *,
        bot_id: str,
        owner_id: str,
        bot: Mapping[str, Any] | None = None,
    ) -> None:
        """Materialize the configured reader scope before an effective read."""
        ...

    def active_skill_assets(
        self,
        *,
        bot_id: str,
        owner_id: str,
        bot: Mapping[str, Any] | None = None,
    ) -> tuple[RegisteredSkillAsset, ...]:
        """Flush, then return Runtime-ready assets with exact Center Versions."""
        ...

    def active_capabilities(
        self,
        *,
        bot_id: str,
        owner_id: str,
        bot: Mapping[str, Any] | None = None,
    ) -> BotCapabilitySnapshot:
        """Synchronize once, then read exact Skills and installed MCP codes.

        Consume only within the current projection; after a write or retry,
        obtain a new snapshot. This does not include engine Policy MCPs.
        """
        ...

    def active_mcp_server_codes(
        self,
        *,
        bot_id: str,
        owner_id: str,
        bot: Mapping[str, Any] | None = None,
    ) -> frozenset[str]:
        """Flush, then read ``ac_bot_mcp_installation``."""
        ...


__all__ = ["BotCapabilityStateReaderProtocol"]
