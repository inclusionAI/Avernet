"""Shared best-effort post-commit manager-sync notifications."""

from __future__ import annotations

from agentclaw.community.api.bot_manager_sync import BotManagerSyncProtocol
from agentclaw.community.kernel.bot_manager_sync import SpaceChange, SyncReason
from agentclaw.community.log import get_logger

logger = get_logger()


def notify_bot_manager_sync(
    sync: BotManagerSyncProtocol,
    *,
    bot_pk: int,
    env: str,
    avernet_tenant: str,
    reason: SyncReason,
) -> None:
    """Submit one post-commit Bot hint without changing the business result."""
    try:
        sync.request_sync(
            bot_pk=bot_pk,
            env=env,
            avernet_tenant=avernet_tenant,
            reason=reason,
        )
    except Exception as exc:
        logger.error(
            "bot manager sync notification failed",
            extra={
                "bot_pk": bot_pk,
                "env": env,
                "reason": reason.value,
                "exception_type": type(exc).__name__,
            },
        )


def notify_space_manager_sync(
    sync: BotManagerSyncProtocol,
    *,
    change: SpaceChange,
    space_id: int | None = None,
) -> None:
    """Submit one post-commit Space hint without changing the business result."""
    try:
        sync.request_space_sync(change)
    except Exception as exc:
        context = {
            "avernet_tenant": change.avernet_tenant,
            "env": change.env,
            "space_ref_count": len(change.space_refs),
            "user_id_count": len(change.user_ids),
            "exception_type": type(exc).__name__,
        }
        if space_id is not None:
            context["space_id"] = space_id
        logger.error("space manager sync notification failed", extra=context)
