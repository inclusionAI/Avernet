"""Best-effort audit persistence for explicit dormant lifecycle operations."""

from __future__ import annotations

import uuid

from injector import inject

from agentclaw.community.core.bot_dormant.sqlite_models import DormantCheckAudit
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin


logger = get_logger()


class DormantAuditService:
    """Persist public lifecycle audit rows without changing operation outcome."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def record_openapi_recycle(
        self,
        *,
        request_id: str,
        bot_id: str,
        owner_id: str,
    ) -> None:
        try:
            with self._db.orm_session() as session:
                row = DormantCheckAudit(
                    run_id=request_id or f"openapi-{uuid.uuid4()}",
                    bot_id=bot_id,
                    owner_id=owner_id,
                    check_result="manual",
                    action_taken="recycled",
                    days_inactive=None,
                    source="openapi",
                    dry_run=0,
                )
                session.add(row)
                session.commit()
        except Exception:
            logger.exception(
                "[DormantAuditService.record_openapi_recycle] audit failed "
                "request_id=%s bot_id=%s owner_id=%s",
                request_id,
                bot_id,
                owner_id,
            )
