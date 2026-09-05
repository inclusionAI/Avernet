"""Core contract for reading a Bot's active capability state."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    # Deferred: skills_pool consumers import this contract while their own
    # package __init__ is still executing, so a module-level import of
    # skills_pool.models here would close an import cycle.
    from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset


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
