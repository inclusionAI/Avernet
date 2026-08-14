"""Skills Pool cutover observability helpers."""

from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from agentclaw.community.log import get_logger

logger = get_logger()


def log_missing_quarantine_path(
    scope: BotSkillLayoutScope,
    migration_generation: str,
) -> None:
    logger.error(
        "[skills_pool.cutover] commit rejected "
        "reason=missing_quarantine_path env=%s entity_id=%s "
        "bot_id=%s migration_generation=%s",
        scope.env,
        scope.entity_id,
        scope.bot_id,
        migration_generation,
    )
