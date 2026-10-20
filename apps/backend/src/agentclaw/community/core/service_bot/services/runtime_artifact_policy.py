"""Resolve one fail-closed runtime artifact decision per Bot build."""
import time

from agentclaw.community.core.repository.protocols.identity import UserListRepositoryProtocol
from agentclaw.community.core.user_list.server_managed import RUNTIME_ARTIFACT_USER_LIST_TYPE
from agentclaw.community.log import get_logger

logger = get_logger()


def resolve_runtime_artifacts(
    repository: UserListRepositoryProtocol, *, bot_id: str, entity_id: str, env: str,
) -> bool:
    """A same-environment global entry or exact Bot owner enables CLI snapshots."""
    started = time.monotonic()
    fields = "bot_id=%s entity_id=%s env=%s user_list_type=%s"
    context = (bot_id, entity_id, env, RUNTIME_ARTIFACT_USER_LIST_TYPE)
    logger.info("service_bot.runtime_artifacts.request " + fields, *context)
    if not entity_id:
        logger.warning("service_bot.runtime_artifacts.success " + fields
                       + " enabled=False match=missing_owner elapsed_ms=%s",
                       *context, round((time.monotonic() - started) * 1000))
        return False
    try:
        # COSEC: this server-owned allowlist is scoped to the Bot owner and
        # runtime environment; callers cannot opt in through build parameters.
        if repository.exists(entity_id="*", user_list_type=RUNTIME_ARTIFACT_USER_LIST_TYPE, env=env):
            enabled, match = True, "global"
        elif repository.exists(entity_id=entity_id, user_list_type=RUNTIME_ARTIFACT_USER_LIST_TYPE, env=env):
            enabled, match = True, "user"
        else:
            enabled, match = False, "none"
    except Exception as exc:
        logger.error("service_bot.runtime_artifacts.failure " + fields
                     + " enabled=False match=error error_type=%s elapsed_ms=%s",
                     *context, type(exc).__name__, round((time.monotonic() - started) * 1000))
        return False
    logger.info("service_bot.runtime_artifacts.success " + fields
                + " enabled=%s match=%s elapsed_ms=%s",
                *context, enabled, match, round((time.monotonic() - started) * 1000))
    return enabled
