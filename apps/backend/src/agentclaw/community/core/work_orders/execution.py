"""Safe phase diagnostics for work-order execution (no business payloads)."""

from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic

from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)

from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()


@contextmanager
def execution_phase(
    phase: str, *, work_order_id: int | None, **context: object
) -> Iterator[None]:
    """Log explicit, caller-selected identifiers, never exception text or bodies."""
    started = monotonic()
    fields = {
        "phase": phase,
        "work_order_id": work_order_id,
        "env": get_current_env(),
        **context,
    }
    logger.info("work-order phase started", extra={**fields, "outcome": "started"})
    try:
        yield
    except Exception as exc:
        logger.error(
            "work-order phase failed",
            extra={
                **fields,
                "outcome": "failed",
                "exception_type": type(exc).__name__,
                "duration_ms": round((monotonic() - started) * 1000, 2),
            },
        )
        raise
    else:
        logger.info(
            "work-order phase completed",
            extra={
                **fields,
                "outcome": "completed",
                "duration_ms": round((monotonic() - started) * 1000, 2),
            },
        )


def notify_bot_collaboration_changed(
    collaborators: CollaboratorServiceProtocol,
    *,
    work_order_id: int,
    bot_id: str,
    owner_id: str,
) -> None:
    """Post-commit projection failure must not misreport an approval as failed."""
    try:
        with execution_phase(
            "bot_post_commit_sync",
            work_order_id=work_order_id,
            bot_id=bot_id,
            owner_id=owner_id,
            local_committed=True,
        ):
            collaborators.on_collaboration_changed(bot_id, owner_id, get_current_env())
    except Exception:
        # execution_phase logs the exception class and identifiers, not payloads.
        # This is not a durable retry mechanism. Persistence never runs here.
        return
