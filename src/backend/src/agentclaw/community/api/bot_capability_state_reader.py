"""Public Service API for reading a Bot's active capability state."""

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.repository.capability_desired_state_types import (
    InstallationFlushPlan,
)
from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset


@runtime_checkable
class BotCapabilityStateReaderProtocol(Protocol):
    """The one read model for a Bot's active capabilities.

    Installation is the single source of truth; the tables are not
    backfilled, so every read first flushes SkillSet configuration into
    Installation, then answers from Installation alone. The flush is
    DB-side only — the reader never triggers a runtime projection.
    """

    def flush(self, *, bot: Mapping[str, Any]) -> InstallationFlushPlan:
        """Make Installation agree with SkillSet configuration for one Bot."""
        ...

    def active_skill_assets(
        self,
        *,
        bot_id: str,
        owner_id: str,
        bot: Mapping[str, Any] | None = None,
    ) -> tuple[RegisteredSkillAsset, ...]:
        """Flush, then read the Installation→``ac_skill`` join."""
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
