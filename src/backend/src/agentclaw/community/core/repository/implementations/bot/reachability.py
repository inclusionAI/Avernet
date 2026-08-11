"""Which bots are live, and which a given person can get at.

Split out of ``bot.py`` as one concern rather than for size alone. Both queries
answer the same shape of question — *given a set of bots or a person, which rows
still count* — and both exist because of one fact the rest of the repository can
take for granted but they cannot:

> ``ac_bots`` has no unique key on ``bot_id``.

The retired ``default`` convention gave many owners a bot of that id, so an
id-only answer reports a deleted bot as live whenever any owner still has a live
one of that id. :meth:`filter_live_bots` is keyed on the ``(bot_id, owner_id)``
pair for exactly that reason, and the owner is on every grant record, so there
is nothing to discover.

A mixin rather than a module of functions because every query needs the
repository's session, model and env scoping. Neither decides anything:
reachability here is ownership or a collaborator row at **any** level, which is
below the operator bar, so callers must still adjudicate what comes back.
"""

from __future__ import annotations

from typing import List

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

        Backs the paginated "bots I can get at" listing. Reachability is
        ownership **or** a collaborator row at any level — below the operator
        bar, so a caller must still adjudicate.

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


__all__ = ["BotReachabilityQueries"]
