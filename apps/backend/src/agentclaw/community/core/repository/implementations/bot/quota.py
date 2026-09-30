"""Space-scoped quota counts for the unified Bot repository."""

from __future__ import annotations

from sqlalchemy import func, or_


class BotQuotaQueries:
    """Count live quota-consuming Bots, excluding desktop and TeClaw."""

    def count_cloud_bots_in_personal_space(
        self,
        *,
        owner_id: str,
        personal_space_id: int | None,
    ) -> int:
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.is_delete == 0,
                self.Model.owner_id == owner_id,
                or_(self.Model.bot_type.is_(None), self.Model.bot_type != "desktop"),
                or_(
                    self.Model.active_engine.is_(None),
                    func.lower(func.trim(self.Model.active_engine)) != "teclaw",
                ),
                self._env(),
            )
            if personal_space_id is None:
                query = query.filter(self.Model.space_id.is_(None))
            else:
                query = query.filter(
                    or_(
                        self.Model.space_id.is_(None),
                        self.Model.space_id == personal_space_id,
                    )
                )
            return query.count()

    def count_cloud_bots_by_space(
        self,
        *,
        space_id: int,
    ) -> int:
        with self._db.orm_session() as db:
            return (
                db.query(self.Model)
                .filter(
                    self.Model.is_delete == 0,
                    self.Model.space_id == space_id,
                    or_(
                        self.Model.bot_type.is_(None), self.Model.bot_type != "desktop"
                    ),
                    self._env(),
                    or_(
                        self.Model.active_engine.is_(None),
                        func.lower(func.trim(self.Model.active_engine)) != "teclaw",
                    ),
                )
                .count()
            )


__all__ = ["BotQuotaQueries"]
