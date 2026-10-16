"""Explicit env-corrected reads/writes for the unified Bot repository."""

from __future__ import annotations

import json
from typing import Any, Dict

from sqlalchemy import func


class BotExplicitEnvQueries:
    """Live exact-env row lookups, deliberately unscoped by runtime env.

    These primitives serve admin cross-env corrections (default bot
    passport repair): callers name the target data env explicitly, and an
    exact match must be found there — never via the implicit runtime
    fallback used by the sibling owner-scoped queries.
    """

    def get_live_by_id_owner_and_env(
        self, *, bot_id: str, owner_id: str, env: str
    ) -> list[dict[str, Any]]:
        """Return every live exact match without consulting runtime env."""
        with self._db.orm_session() as db:
            bots = (
                db.query(self.Model)
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.owner_id == owner_id,
                    self.Model.is_delete == 0,
                    self.Model.env == env,
                )
                .order_by(self.Model.id.asc())
                .all()
            )
            return [bot.to_dict() for bot in bots]

    def update_ext_by_id_owner_and_env(
        self,
        *,
        bot_id: str,
        owner_id: str,
        env: str,
        ext: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Atomically update one live exact-env row or roll back."""
        with self._db.orm_session() as db:
            exact = db.query(self.Model).filter(
                self.Model.bot_id == bot_id,
                self.Model.owner_id == owner_id,
                self.Model.is_delete == 0,
                self.Model.env == env,
            )
            rowcount = exact.update(
                {
                    self.Model.ext: json.dumps(ext),
                    self.Model.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )
            if rowcount != 1:
                raise RuntimeError(
                    "explicit-env bot ext update must affect exactly one live row; "
                    f"affected={rowcount}"
                )
            bot = exact.one()
            return bot.to_dict()