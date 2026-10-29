"""Read-only SQL facts source for the bot manager sync projector.

``SqlManagerSyncReadRepository`` implements the ``ManagerSyncReadRepository``
contract on the existing schema only — no table, index or migration is
created here, and nothing is ever written by the sync path. Three reads:

- :meth:`read_facts` — one short session per ``SyncKey``: the live Bot
  (PK + tenant + env + ``is_delete == 0`` — a key from another ownership
  is a stable ``bot_not_found``, never another tenant's facts), its
  explicit collaborator rows by ``bot_pk`` + env, and the Space that
  decides each row's eligibility. A Team Space vouches for its ACTIVE
  members, read in batches of 100 — never one query per user. A missing
  or broken Space is a *known-unavailable* snapshot
  (``SnapshotUnavailable("space_unavailable")``), not an empty roster,
  and a database failure maps onto ``SnapshotUnavailable("read_error")``
  — never a silently emptied projection either. An oversized eligible
  roster raises ``SnapshotTooLarge`` without judging the remaining rows
  from a truncated read.
- :meth:`page_space_keys` — the ``SpaceChange`` fan-out: every live Bot
  under the Space refs that has an explicit collaborator row, paged by
  PK. Numeric and ``team:<id>`` refs carry the Space id themselves (a
  deleted Space needs no post-delete query, the bots that referenced it
  are still the fan-out); only code-form refs resolve against the live
  Space table.
- :meth:`page_keys` — the repair-sweep page over every live Bot in one
  tenant/env scope, same key order and clamps.

No release-stage (online/dev) filtering belongs here: this reader is
intentionally by-PK, so a keyed request always reads exactly the row
the Editor path owns. The explicit tenant is bound with
``avernet_tenant_scope`` (restored even on failure) so the session-wide
BotModel tenant guard and the explicit predicates always agree, and the
environment is threaded explicitly — no helper that implicitly reads
the current request's env.
"""
from __future__ import annotations

from typing import Iterator, Sequence

from injector import inject
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.repository.protocols.bot import (
    ManagerSyncReadRepository,
)
from agentclaw.community.core.spaces.models import SpaceType
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.kernel.bot_manager_sync import (
    CollaboratorFact,
    SnapshotTooLarge,
    SnapshotUnavailable,
    SpaceChange,
    SyncFacts,
    SyncKey,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

#: Stable machine reasons for :class:`SnapshotUnavailable`.
BOT_NOT_FOUND = "bot_not_found"
SPACE_UNAVAILABLE = "space_unavailable"
READ_ERROR = "read_error"

#: Membership lookups run over ``user_id IN (...)`` batches of this size.
MEMBERSHIP_BATCH_SIZE = 100

#: Hard page clamp — callers may ask for less, never for more.
MAX_PAGE_LIMIT = 100

#: The projected manager cap the projector enforces after Owner exclusion;
#: the reader stops at the first point the verdict is decided instead of
#: building facts it will throw away.
MAX_ELIGIBLE_USER_IDS = 1000


class SqlManagerSyncReadRepository(ManagerSyncReadRepository):
    """The SQL body of ``ManagerSyncReadRepository`` (SQLite and prod alike)."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    # ── read_facts ────────────────────────────────────────────────────

    def read_facts(self, key: SyncKey) -> SyncFacts:
        with avernet_tenant_scope(key.avernet_tenant):
            try:
                with self._db.orm_session() as session:
                    return self._read_facts(session, key)
            except SQLAlchemyError as exc:
                # A database failure is a known-unavailable snapshot — it
                # must never surface as an empty roster fact set.
                raise SnapshotUnavailable(READ_ERROR) from exc

    def _read_facts(self, session: Session, key: SyncKey) -> SyncFacts:
        bot = (
            session.query(BotModel)
            .filter(
                BotModel.id == key.bot_pk,
                BotModel.env == key.env,
                BotModel.avernet_tenant == key.avernet_tenant,
                BotModel.is_delete == 0,
            )
            .one_or_none()
        )
        if bot is None:
            raise SnapshotUnavailable(BOT_NOT_FOUND)

        rows = (
            session.query(
                BotCollaboratorModel.user_id, BotCollaboratorModel.role
            )
            .filter(
                BotCollaboratorModel.bot_pk == key.bot_pk,
                BotCollaboratorModel.env == key.env,
            )
            .order_by(BotCollaboratorModel.id.asc())
            .all()
        )

        eligible_user_ids = self._eligible_user_ids(
            session, bot, key.env, [user_id for user_id, _role in rows]
        )
        return SyncFacts(
            key=key,
            bot_id=bot.bot_id,
            entity_id=bot.entity_id,
            owner_id=bot.owner_id,
            collaborators=tuple(
                CollaboratorFact(
                    user_id=user_id,
                    role=role,
                    space_eligible=(
                        True
                        if eligible_user_ids is None
                        else user_id in eligible_user_ids
                    ),
                )
                for user_id, role in rows
            ),
        )

    def _eligible_user_ids(
        self, session: Session, bot: BotModel, env: str, user_ids: list[str]
    ) -> set[str] | None:
        """Which roster users the Bot's Space still vouches for.

        ``None`` means "no Team membership contract applies — every row is
        eligible", mirroring :class:`EditorPolicy`: a Bot with no Space,
        a ``personal:`` reference, or a resolvable non-TEAM Space answers
        the editor question with *allowed*. Only a TEAM Space batches its
        ACTIVE membership lookups.
        """
        raw_ref = "" if bot.space_id is None else str(bot.space_id).strip()
        if not raw_ref or raw_ref.startswith("personal:"):
            return self._without_team_membership(user_ids, bot.owner_id)

        space = self._require_space_row(session, raw_ref, env)
        if space.space_type != SpaceType.TEAM.value:
            return self._without_team_membership(user_ids, bot.owner_id)

        active: set[str] = set()
        for batch in _batched(user_ids, MEMBERSHIP_BATCH_SIZE):
            active.update(
                self._active_member_user_ids(session, space.id, env, batch)
            )
            # The verdict is decided once the *eligible* set passes the
            # cap; every row judged so far was judged on its full batch,
            # so the remaining rows cannot un-decide it.
            if len(active - {bot.owner_id}) > MAX_ELIGIBLE_USER_IDS:
                raise SnapshotTooLarge()
        return active

    @staticmethod
    def _without_team_membership(
        user_ids: list[str], owner_id: str
    ) -> set[str] | None:
        if len(set(user_ids) - {owner_id}) > MAX_ELIGIBLE_USER_IDS:
            raise SnapshotTooLarge()
        return None

    @staticmethod
    def _active_member_user_ids(
        session: Session, space_id: int, env: str, user_ids: Sequence[str]
    ) -> set[str]:
        rows = (
            session.query(SpaceMemberModel.user_id)
            .filter(
                SpaceMemberModel.space_id == space_id,
                SpaceMemberModel.env == env,
                SpaceMemberModel.status == "ACTIVE",
                SpaceMemberModel.user_id.in_(list(user_ids)),
            )
            .all()
        )
        return {user_id for (user_id,) in rows}

    @staticmethod
    def _require_space_row(session: Session, ref: str, env: str) -> SpaceModel:
        """Resolve a persisted Space reference inside one explicit env.

        Accepts the same forms as the Editor path — numeric id,
        ``team:<id>`` (a type assertion that must match) and Space code —
        but queries with the *explicit* env instead of a helper that
        implicitly reads the current request's. Unresolvable means
        known-unavailable, fail-closed.
        """
        team_ref = ref.startswith("team:")
        normalized = ref.removeprefix("team:")
        if normalized.isdigit():
            row = (
                session.query(SpaceModel)
                .filter(
                    SpaceModel.id == int(normalized),
                    SpaceModel.env == env,
                    SpaceModel.deleted_at.is_(None),
                )
                .one_or_none()
            )
        else:
            row = (
                session.query(SpaceModel)
                .filter(
                    SpaceModel.space_code == normalized,
                    SpaceModel.env == env,
                    SpaceModel.deleted_at.is_(None),
                )
                .one_or_none()
            )
        if row is None or (
            team_ref and row.space_type != SpaceType.TEAM.value
        ):
            raise SnapshotUnavailable(SPACE_UNAVAILABLE)
        return row

    # ── page_space_keys ───────────────────────────────────────────────

    def page_space_keys(
        self, change: SpaceChange, *, after_pk: int, limit: int
    ) -> tuple[SyncKey, ...]:
        bounded = _clamp_limit(limit)
        if bounded <= 0:
            return ()
        with avernet_tenant_scope(change.avernet_tenant):
            try:
                with self._db.orm_session() as session:
                    space_ids = self._referenced_space_ids(
                        session, change.space_refs, change.env
                    )
                    if not space_ids:
                        return ()
                    query = session.query(BotModel.id).join(
                        BotCollaboratorModel,
                        BotCollaboratorModel.bot_pk == BotModel.id,
                    )
                    if change.user_ids:
                        query = query.filter(
                            BotCollaboratorModel.user_id.in_(change.user_ids)
                        )
                    rows = (
                        query.filter(
                            BotModel.id > after_pk,
                            BotModel.env == change.env,
                            BotModel.avernet_tenant == change.avernet_tenant,
                            BotModel.is_delete == 0,
                            BotModel.space_id.in_(space_ids),
                            BotCollaboratorModel.env == change.env,
                        )
                        .distinct()
                        .order_by(BotModel.id.asc())
                        .limit(bounded)
                        .all()
                    )
                    return tuple(
                        SyncKey(change.avernet_tenant, change.env, bot_pk)
                        for (bot_pk,) in rows
                    )
            except SQLAlchemyError as exc:
                raise SnapshotUnavailable(READ_ERROR) from exc

    @staticmethod
    def _referenced_space_ids(
        session: Session, space_refs: tuple[str, ...], env: str
    ) -> tuple[int, ...]:
        """Map every accepted Space reference form to candidate Bot space ids.

        Numeric and ``team:<id>`` refs already *are* the Space id — they are
        used as-is, because a ``SpaceChange`` about a deleted Space still
        has to fan out to the Bots that referenced it, with no deleted-Space
        lookup. Only code-form refs resolve against the live Space table in
        one query; a code nothing answers contributes nothing.
        """
        direct_ids: list[int] = []
        codes: list[str] = []
        for ref in space_refs:
            normalized = (ref or "").strip()
            if not normalized or normalized.startswith("personal:"):
                continue
            base = normalized.removeprefix("team:")
            if base.isdigit():
                direct_ids.append(int(base))
            else:
                codes.append(base)
        if codes:
            rows = (
                session.query(SpaceModel.id)
                .filter(
                    SpaceModel.space_code.in_(codes),
                    SpaceModel.env == env,
                    SpaceModel.deleted_at.is_(None),
                )
                .all()
            )
            direct_ids.extend(int(space_id) for (space_id,) in rows)
        return tuple(dict.fromkeys(direct_ids))

    # ── page_keys ──────────────────────────────────────────────────────

    def page_keys(
        self, *, avernet_tenant: str, env: str, after_pk: int, limit: int
    ) -> tuple[SyncKey, ...]:
        bounded = _clamp_limit(limit)
        if bounded <= 0:
            return ()
        with avernet_tenant_scope(avernet_tenant):
            try:
                with self._db.orm_session() as session:
                    rows = (
                        session.query(BotModel.id)
                        .filter(
                            BotModel.id > after_pk,
                            BotModel.env == env,
                            BotModel.avernet_tenant == avernet_tenant,
                            BotModel.is_delete == 0,
                        )
                        .order_by(BotModel.id.asc())
                        .limit(bounded)
                        .all()
                    )
                    return tuple(
                        SyncKey(avernet_tenant, env, bot_pk)
                        for (bot_pk,) in rows
                    )
            except SQLAlchemyError as exc:
                raise SnapshotUnavailable(READ_ERROR) from exc


def _clamp_limit(limit: int) -> int:
    return max(0, min(int(limit), MAX_PAGE_LIMIT))


def _batched(values: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


__all__ = [
    "BOT_NOT_FOUND",
    "MAX_ELIGIBLE_USER_IDS",
    "MAX_PAGE_LIMIT",
    "MEMBERSHIP_BATCH_SIZE",
    "READ_ERROR",
    "SPACE_UNAVAILABLE",
    "SqlManagerSyncReadRepository",
]