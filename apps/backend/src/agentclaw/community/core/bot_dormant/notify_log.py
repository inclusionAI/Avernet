"""Helpers for writing dormant notification rows."""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.bot_dormant.sqlite_models import DormantNotifyLog


def commit_notify_log_idempotent(
    session, log_row: DormantNotifyLog,
) -> tuple[DormantNotifyLog | None, bool]:
    """Commit a notify_log row, treating the daily unique key as idempotent.

    Returns:
        ``(row, True)`` when a new row was inserted; ``(existing, False)`` when
        the same ``(bot_id, owner_id, dt, notify_type)`` row already exists.
    """
    key = {
        "bot_id": log_row.bot_id,
        "owner_id": log_row.owner_id,
        "dt": log_row.dt,
        "notify_type": log_row.notify_type,
    }
    session.add(log_row)
    try:
        session.commit()
        return log_row, True
    except IntegrityError as exc:
        session.rollback()
        if not _is_notify_unique_violation(exc):
            raise
        existing = session.query(DormantNotifyLog).filter_by(**key).first()
        return existing, False


def _is_notify_unique_violation(exc: IntegrityError) -> bool:
    orig = exc.orig if getattr(exc, "orig", None) is not None else exc
    msg = str(orig).lower()
    return (
        "uk_bot_owner_dt_type" in msg
        or "ac_bot_dormant_notify_log.bot_id" in msg
    )
