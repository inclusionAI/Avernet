"""Ownership-scoped lookups and soft deletion for the unified Bot repository."""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import func

class BotDeletionQueries:
    """The reads and the soft delete the deletion-retry contract relies on.

    One cohesive group: an owner-scoped LIVE lookup, an owner-scoped
    read that ALSO returns a soft-deleted row (the ONLY such read — it is
    what lets deletion retry finalization prove BOTH ownership and the
    actual deletion), and the soft delete itself.
    """

    def get_by_id_and_owner(
        self,
        bot_id: str,
        owner_id: str,
        *,
        execution_options: Optional[dict] = None,
        env: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._db.orm_session() as db:
            q = db.query(self.Model).filter(
                self.Model.bot_id == bot_id,
                self.Model.owner_id == owner_id,
                self.Model.is_delete == 0,
                self._env(env=env),
            )
            if execution_options:
                q = q.execution_options(**execution_options)
            bot = q.first()
            return bot.to_dict() if bot else None

    def get_by_id_and_owner_including_deleted(
        self, bot_id: str, owner_id: str, *, env: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Owner-scoped read that also returns the soft-deleted row.

        See the Protocol note: a live foreign id answers ``None`` exactly
        like an unknown one, so ownership is never inferred from a plain
        not-found."""

        with self._db.orm_session() as db:
            bot = (
                db.query(self.Model)
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.owner_id == owner_id,
                    self._env(env=env),
                )
                .first()
            )
            return bot.to_dict() if bot else None

    def soft_delete_by_owner(self, bot_id: str, owner_id: str) -> bool:
        with self._db.orm_session() as db:
            rowcount = (
                db.query(self.Model)
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.owner_id == owner_id,
                    self.Model.is_delete == 0,
                    self._env(),
                )
                .update(
                    {
                        self.Model.is_delete: 1,
                        self.Model.gmt_modified: func.now(),
                    },
                    synchronize_session=False,
                )
            )
            db.commit()
            return rowcount > 0
