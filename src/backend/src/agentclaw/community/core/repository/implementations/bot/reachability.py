"""Which bots are live, and which a given person can get at.

Split out of ``bot.py`` as one concern rather than for size alone. These three
queries answer the same shape of question — *given a set of bots or a person,
which rows still count* — and they exist because of one fact the rest of the
repository can take for granted but they cannot:

> ``ac_bots`` has no unique key on ``bot_id``.

The retired ``default`` convention gave many owners a bot of that id, so an
id-only answer is wrong in both directions here: it reports a deleted bot as
live whenever any owner still has a live one of that id, and it refuses a
caller whose bot is perfectly unambiguous *within their own reach* because a
stranger's bot shares the name. Both are corrected by asking with more than the
bare id — the owner alongside it, or the caller whose bots are in question.

A mixin rather than a module of functions because every query needs the
repository's session, model and env scoping. It decides nothing: reachability
here is ownership or a collaborator row at **any** level, which is below the
operator bar, so callers must still adjudicate what comes back.
"""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import or_

from agentclaw.community.utils.env_utils import get_current_env


class BotReachabilityQueries:
    """Liveness and reach queries for :class:`BotRepository`."""

    def filter_live_bots(
        self, pairs: List[tuple[str, str]]
    ) -> set[tuple[str, str]]:
        """Which of these ``(bot_id, owner_id)`` bots are live, in one query."""
        if not pairs:
            # No query at all rather than an ``IN ()``, which MySQL rejects
            # outright and SQLAlchemy warns about.
            return set()
        wanted = set(pairs)
        with self._db.orm_session() as db:
            rows = (
                db.query(self.Model.bot_id, self.Model.owner_id)
                .filter(
                    self.Model.bot_id.in_({bot_id for bot_id, _ in wanted}),
                    self.Model.owner_id.in_({owner for _, owner in wanted}),
                    self.Model.is_delete == 0,
                    self._env(),
                )
                .all()
            )
            # The two ``IN`` clauses are a cross product, so a row can match one
            # column from one pair and the other from a different pair. Only the
            # pairs actually asked for survive.
            return {(row[0], row[1]) for row in rows} & wanted

    def _reachable_by(self, db, user_id: str):
        """Live bots this user owns or collaborates on, as an unordered query.

        Shared by the two reads that mean "bots this person can get at": the
        paginated listing, and the by-id resolve that breaks a duplicated
        ``bot_id``.

        COSEC: SQLAlchemy binds the request-derived id; never interpolate it
        into a raw SQL expression.
        """
        from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel

        env = get_current_env()
        return (
            db.query(self.Model)
            .outerjoin(
                BotCollaboratorModel,
                (BotCollaboratorModel.bot_pk == self.Model.id)
                & (BotCollaboratorModel.env == env)
                & (BotCollaboratorModel.user_id == user_id),
            )
            .filter(
                self.Model.is_delete == 0,
                self.Model.env == env,
                or_(
                    self.Model.owner_id == user_id,
                    BotCollaboratorModel.id.isnot(None),
                ),
            )
            .distinct()
        )

    def list_reachable_by_bot_id(
        self, bot_id: str, caller_id: str, limit: int = 8
    ) -> List[Dict[str, Any]]:
        """Live bots with this id that ``caller_id`` owns or collaborates on."""
        with self._db.orm_session() as db:
            bots = (
                self._reachable_by(db, caller_id)
                .filter(self.Model.bot_id == bot_id)
                .limit(limit)
                .all()
            )
            return [self._to_caller_identity_dict(b) for b in bots]


__all__ = ["BotReachabilityQueries"]
