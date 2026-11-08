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

from agentclaw.community.utils.env_utils import get_current_env
from sqlalchemy import or_, tuple_


class BotReachabilityQueries:
    """Liveness and reach queries for :class:`BotRepository`."""

    def page_live_bots_by_pks(
        self, bot_pks: List[int], *, exclude_owner_id: str, entity_pairs: List[tuple[str, str]] | None,
        page: int, page_size: int, stage: str | None = None,
    ) -> tuple[int, list[dict]]:
        if not bot_pks or entity_pairs == []:
            return 0, []
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.id.in_(set(bot_pks)), self.Model.is_delete == 0, self._env(),
                self.Model.owner_id != exclude_owner_id,
            )
            if entity_pairs is not None:
                query = query.filter(tuple_(self.Model.bot_id, self.Model.entity_id).in_(set(entity_pairs)))
            if stage is not None:
                from .stage_catalog import apply_stage_filter

                query = apply_stage_filter(db, query, self.Model, stage)
            total = query.count()
            rows = query.order_by(self.Model.gmt_create.desc(), self.Model.id.desc()).offset(
                (page - 1) * page_size
            ).limit(page_size).all()
            return total, [row.to_dict() for row in rows]


    def get_live_bot_owners_by_entity(self, pairs: List[tuple[str, str]]) -> dict[tuple[str, str], str]:
        """Project current responsibility separately from the immutable grant address."""
        if not pairs:
            return {}
        with self._db.orm_session() as db:
            rows = db.query(self.Model.bot_id, self.Model.entity_id, self.Model.owner_id).filter(
                tuple_(self.Model.bot_id, self.Model.entity_id).in_(set(pairs)),
                self.Model.is_delete == 0,
                self._env(),
            ).all()
            return {(row[0], row[1]): row[2] for row in rows}

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
        from agentclaw.community.core.bot_collaborator.models import (
            BotCollaboratorModel,
        )

        env = get_current_env()
        return (
            db.query(self.Model)
            # ``outerjoin`` is a LEFT outer join in SQLAlchemy — there is no
            # separate ``leftjoin``. That direction is the whole mechanism here:
            # bots the user *owns* have no collaborator row, so an inner join
            # would silently drop every one of them and answer "you can reach
            # only what you collaborate on".
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
