"""Service backing /api/internal/dormant/* endpoints.

list_pending:
    - Returns send_status='pending' AND dry_run=0 rows ordered by gmt_create ASC
    - For recycle notifications, joins ac_bots and filters out rows whose
      bot.status is no longer 'RECYCLED' (the user already activated). These
      filtered rows are marked send_status='cancelled' in the same call so
      they never appear again.

mark_sent:
    - Idempotent: a row that is no longer 'pending' returns 'already_resolved'
      without mutating state.
    - success=False without error_msg raises ValueError (the router translates
      to 400).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from injector import inject

from agentclaw.community.core.bot_dormant.protocols import BotServiceProtocol
from agentclaw.community.core.bot_dormant.sqlite_models import DormantNotifyLog
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.log import get_logger

logger = get_logger()


class DormantInternalService:
    """Pull pending dormant notifications and accept send results from the
    in-container notify-sender bot."""

    @inject
    def __init__(
        self,
        db: DatabasePlugin,
        bot_service: BotServiceProtocol,
    ) -> None:
        self._db = db
        self._bot_service = bot_service

    def list_pending(
        self,
        *,
        limit: int = 200,
        dt: Optional[str] = None,
        include_dry_run: bool = False,
    ) -> list[dict]:
        """Return notify_log rows ready for sending.

        Side effect: recycle rows whose bot is no longer RECYCLED get
        send_status='cancelled' and are NOT returned. Subsequent calls
        will not see them.

        include_dry_run: default False (production behaviour — only real rows
        are returned so dry-run scans never spam DingTalk). Set True in pre/dev
        to verify the bot-container loop end-to-end against dry_run=1 rows
        the morning scan wrote.
        """
        with self._db.orm_session() as session:
            q = session.query(DormantNotifyLog).filter(
                DormantNotifyLog.send_status == "pending",
            )
            if not include_dry_run:
                q = q.filter(DormantNotifyLog.dry_run == 0)
            if dt:
                q = q.filter(DormantNotifyLog.dt == dt)
            q = q.order_by(DormantNotifyLog.gmt_create.asc()).limit(limit)
            rows: list[DormantNotifyLog] = q.all()

            result: list[dict] = []
            for row in rows:
                # Recycle gate: skip + cancel if bot no longer RECYCLED.
                # Match by (bot_id, owner_id) — bot_id='default' is per-owner,
                # so a bot_id-only lookup would pick the first default bot
                # in the table and misjudge other owners' state.
                if row.notify_type == "recycle":
                    bot = (
                        session.query(BotModel)
                        .filter(
                            BotModel.bot_id == row.bot_id,
                            BotModel.owner_id == row.owner_id,
                        )
                        .first()
                    )
                    if bot is None or bot.status != "RECYCLED":
                        row.send_status = "cancelled"
                        row.error_msg = (
                            "recycle gate: bot.status is no longer RECYCLED"
                        )
                        logger.info(
                            "[DormantInternalService.list_pending] cancelled "
                            "recycle notify id=%s bot_id=%s owner_id=%s (status=%s)",
                            row.id, row.bot_id, row.owner_id,
                            (bot.status if bot else "MISSING"),
                        )
                        continue

                result.append({
                    "id": row.id,
                    "bot_id": row.bot_id,
                    "entity_id": row.entity_id,
                    "notify_target": row.notify_target,
                    "notify_type": row.notify_type,
                    "notify_source": row.notify_source,
                    "content": row.content,
                    "enqueued_at": (
                        row.gmt_create.isoformat()
                        if isinstance(row.gmt_create, datetime)
                        else str(row.gmt_create) if row.gmt_create else None
                    ),
                })

            session.commit()  # persist cancelled rows
            return result

    def mark_sent(
        self,
        *,
        notify_log_id: int,
        success: bool,
        error_msg: Optional[str] = None,
    ) -> str:
        """Update send_status. Returns one of:
          - 'sent'              : success=True, row was pending → set to sent
          - 'failed'            : success=False (error_msg required) → set to failed
          - 'already_resolved'  : row already sent/failed/cancelled → no-op
          - 'not_found'         : row id does not exist

        Raises ValueError when success=False but error_msg missing.
        """
        if not success and not error_msg:
            raise ValueError("error_msg is required when success=False")

        with self._db.orm_session() as session:
            row = (
                session.query(DormantNotifyLog)
                .filter(DormantNotifyLog.id == notify_log_id)
                .first()
            )
            if row is None:
                return "not_found"
            if row.send_status != "pending":
                logger.warning(
                    "[DormantInternalService.mark_sent] idempotent no-op: "
                    "id=%s already in send_status=%s",
                    notify_log_id, row.send_status,
                )
                return "already_resolved"

            if success:
                row.send_status = "sent"
                row.error_msg = None
                outcome = "sent"
            else:
                row.send_status = "failed"
                row.error_msg = error_msg
                outcome = "failed"
            session.commit()
            return outcome
