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

    Installation is the active-identity source of truth; the tables are not
    backfilled, so every read first flushes SkillSet configuration into
    Installation, then answers from Installation alone. Center identities are
    resolved to an exact PUBLISHED Version before they leave this seam. The
    reader never triggers a runtime projection.
    """

    def member_skill_ids(self, *, bot: Mapping[str, Any]) -> frozenset[int]:
        """Flush, then answer which Skills the Bot's Sets bring to it.

        The listing filter needs this half of the flush plan: a bridged
        Skill belongs on the page even though only a Set ties it to the Bot.
        """
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
