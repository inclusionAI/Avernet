"""AutoReactivateWhitelistService — manage the auto-reactivate eligibility list.

Bots must be explicitly added to the ``ac_bot_dormant_auto_reactivate`` table
before the dormant scan will attempt to auto-reactivate them after a recycle.
The unique constraint ``uk_auto_reactivate_bot_owner`` prevents duplicate
(bot_id, owner_id) pairs.
"""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from injector import inject

from agentclaw.community.core.bot_dormant.sqlite_models import (
    DormantAutoReactivate,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.log import get_logger


logger = get_logger()


class AutoReactivateWhitelistService:
    """Manage the auto-reactivate whitelist.

    Uses constructor injection of ``DatabasePlugin`` so the DI module
    can provide a real or test database.  Each method opens its own
    ORM session.
    """

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def is_enabled(self, bot_id: str, owner_id: str) -> bool:
        """Return True if the (bot_id, owner_id) pair is on the whitelist."""
        with self._db.orm_session() as session:
            row = (
                session.query(DormantAutoReactivate)
                .filter(
                    DormantAutoReactivate.bot_id == bot_id,
                    DormantAutoReactivate.owner_id == owner_id,
                )
                .first()
            )
            return row is not None

    def add(self, bot_id: str, owner_id: str) -> bool:
        """Add a (bot_id, owner_id) pair to the whitelist.

        Returns True if the entry was newly inserted, False if it
        already existed (duplicate).
        """
        row = DormantAutoReactivate(bot_id=bot_id, owner_id=owner_id)
        with self._db.orm_session() as session:
            try:
                session.add(row)
                session.commit()
                logger.info(
                    "[auto_reactivate_whitelist] added bot_id=%s owner_id=%s",
                    bot_id, owner_id,
                )
                return True
            except IntegrityError:
                session.rollback()
                logger.debug(
                    "[auto_reactivate_whitelist] skip duplicate bot_id=%s owner_id=%s",
                    bot_id, owner_id,
                )
                return False

    def remove(self, bot_id: str, owner_id: str) -> bool:
        """Remove a (bot_id, owner_id) pair from the whitelist.

        Returns True if a row was deleted, False if no matching row existed.
        """
        with self._db.orm_session() as session:
            count = (
                session.query(DormantAutoReactivate)
                .filter(
                    DormantAutoReactivate.bot_id == bot_id,
                    DormantAutoReactivate.owner_id == owner_id,
                )
                .delete()
            )
            session.commit()
            if count:
                logger.info(
                    "[auto_reactivate_whitelist] removed bot_id=%s owner_id=%s",
                    bot_id, owner_id,
                )
            return count > 0

    def list_all(self) -> list[dict]:
        """Return all entries as list of dicts (for admin inspection)."""
        with self._db.orm_session() as session:
            rows = session.query(DormantAutoReactivate).all()
            return [
                {"bot_id": r.bot_id, "owner_id": r.owner_id,
                 "gmt_create": r.gmt_create}
                for r in rows
            ]
