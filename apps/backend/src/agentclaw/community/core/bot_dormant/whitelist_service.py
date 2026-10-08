"""WhitelistService — batch-add bots to the dormant whitelist.

Bots on the whitelist are excluded from the dormant-scan candidate set.
Duplicate entries (same bot_id) are skipped silently — the unique
constraint ``uk_wl_bot`` on ``ac_bot_dormant_whitelist`` is used to
detect them via IntegrityError.
"""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from injector import inject

from agentclaw.community.core.bot_dormant.sqlite_models import DormantWhitelist
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.log import get_logger

logger = get_logger()


class WhitelistService:
    """Batch-write bots onto the dormant whitelist."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def batch_add(self, entries: list[dict], created_by: str) -> dict:
        """Add a list of whitelist entries, skipping duplicates.

        Args:
            entries: list of dicts with keys ``bot_id``, ``owner_id``,
                     and optionally ``governance_source`` / ``reason``.
            created_by: operator's user_id (for audit trail).

        Returns:
            dict with keys ``inserted`` (int) and ``skipped`` (int).
        """
        inserted = 0
        skipped = 0
        with self._db.orm_session() as session:
            for entry in entries:
                row = DormantWhitelist(
                    bot_id=entry["bot_id"],
                    owner_id=entry["owner_id"],
                    governance_source=entry.get("governance_source", "manual"),
                    reason=entry.get("reason"),
                    created_by=created_by,
                )
                try:
                    session.add(row)
                    session.commit()
                    inserted += 1
                except IntegrityError:
                    session.rollback()
                    skipped += 1
                    logger.debug(
                        "[whitelist] skip duplicate bot_id=%s", entry.get("bot_id")
                    )
        return {"inserted": inserted, "skipped": skipped}
