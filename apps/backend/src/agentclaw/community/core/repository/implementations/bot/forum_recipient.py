"""Single-snapshot Bot classification for BBS reply notifications."""

from sqlalchemy import case


class BotForumRecipientQueries:
    def classify_forum_recipient(self, bot_id: str) -> tuple[str, str | None]:
        """Read at most two candidates without projecting deleted owners."""
        with self._db.orm_session() as db:
            rows = (
                db.query(self.Model)
                .with_entities(
                    self.Model.is_delete,
                    case((self.Model.is_delete == 0, self.Model.owner_id), else_=None),
                )
                .filter(self.Model.bot_id == bot_id, self._env())
                .limit(2)
                .all()
            )
        if not rows:
            return ("missing", None)
        if len(rows) != 1:
            return ("ambiguous", None)
        deleted, owner = rows[0]
        if deleted == 1:
            return ("deleted", None)
        if deleted == 0:
            return ("live", owner)
        return ("ambiguous", None)
