"""Structured data carried by the small set of actionable public errors."""

from agentclaw.community.api.bot_config_manifest_service import (
    ManifestValidationError,
)
from agentclaw.community.core.bot_management.bot_quota import (
    BotQuotaExceededError,
)
from agentclaw.community.core.bot_management.errors import (
    BotCreationRetainedError,
)
from agentclaw.community.core.skill_center.errors import (
    SkillAssetInUseError,
    SkillOfflineBlockedError,
)


def error_data(exc: Exception) -> object | None:
    """Return caller-actionable error data for explicitly admitted types."""
    if isinstance(exc, SkillOfflineBlockedError):
        return exc.impact
    if isinstance(exc, SkillAssetInUseError):
        return {"blockers": exc.blocker_counts}
    if isinstance(exc, ManifestValidationError):
        return exc.as_payload()
    if isinstance(exc, BotQuotaExceededError):
        return exc.as_payload()
    if isinstance(exc, BotCreationRetainedError):
        return {"bot_id": exc.bot_id, "retryable": True}
    return None
