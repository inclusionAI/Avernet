"""Helpers for writing dormant notification rows."""
from __future__ import annotations

from datetime import date

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


def flush_notify_log_idempotent(session, log_row: DormantNotifyLog) -> bool:
    """Insert under a savepoint; never commit or roll back the caller's work."""
    key = {name: getattr(log_row, name) for name in ("bot_id", "owner_id", "dt", "notify_type")}
    if session.query(DormantNotifyLog).filter_by(**key).first() is not None:
        return False
    try:
        with session.begin_nested():
            session.add(log_row)
            session.flush()
        return True
    except IntegrityError as exc:
        if not _is_notify_unique_violation(exc):
            raise
        if session.query(DormantNotifyLog).filter_by(**key).first() is None:
            raise
        return False


def recycle_notification(*, bot_id: str, owner_id: str, bot_name: str,
                         action_link_pattern: str, dry_run: bool = False) -> DormantNotifyLog:
    """The existing manual/scan copy and daily recipient key."""
    from agentclaw.community.core.bot_dormant.templates import render_recycle

    return DormantNotifyLog(
        bot_id=bot_id, owner_id=owner_id, entity_id=owner_id,
        notify_type="recycle", notify_target=owner_id, notify_source="internal_scan",
        content=render_recycle(bot_name=bot_name or bot_id, bot_id=bot_id,
                               action_link_pattern=action_link_pattern),
        dt=date.today().strftime("%Y%m%d"), send_status="pending", dry_run=1 if dry_run else 0,
    )
