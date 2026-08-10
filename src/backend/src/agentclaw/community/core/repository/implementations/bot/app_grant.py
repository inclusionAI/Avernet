"""Bot→app authorization repository (prod OceanBase + local SQLite).

One ORM implementation behind ``BotAppGrantRepositoryProtocol``. The only
per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so this
single body runs unchanged on OceanBase (prod) and SQLite (local).

Behavior:
- The live table holds authorizations that are in force; withdrawal deletes the
  row rather than flagging it, and the log preserves the closed period.
- Each mutation writes its live row and its log event inside **one**
  ``transactional_orm_session()``, so the history cannot disagree with the live
  state. ``orm_session()`` would not do: the corp engine runs it at
  ``AUTOCOMMIT`` (``plugin_api/database.py``), so its two statements commit
  independently and a failure between them would leave a ``revoked`` event
  beside a live row — the app still reaching the bot while the audit says it
  cannot. Reads stay on ``orm_session()``; only the two mutations need a
  transaction.
- ``revoke`` and the two sweeps take ``with_for_update()`` on the rows they are
  about to delete, so a concurrent withdrawal of the same authorization waits
  rather than double-logging. (SQLite compiles the clause away and every session
  a test can reach is SQLite, so a green suite says nothing about this; only
  MySQL/OceanBase exercises it.)
  ``grant`` deliberately does **not**: there is no row to lock before the first
  insert, and on InnoDB at REPEATABLE READ a ``FOR UPDATE`` matching nothing
  takes a *gap* lock instead. Two such locks are mutually compatible while the
  insert-intention locks that follow them are not, so both callers would grant
  their gap lock and then deadlock on each other's — surfacing as
  ``OperationalError`` (ER 1213), a different class from the duplicate key and
  therefore an unhandled 500. The unique key already serializes the insert
  correctly; the loser is handled below.
- The tenant is never passed in or filtered on here. ``register_avernet_tenant_guard``
  stamps it on insert and appends the tenant predicate to every read, so a
  query written without it is still tenant-scoped — and one written *with* it
  would merely restate what the guard already guarantees.
- ``env`` is always this process's ``get_current_env()``, never a caller's. A
  row written under one env and read under another would be live, invisible to
  every read, and impossible to revoke while still occupying the unique key.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from injector import inject
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.bot_app_grant.models import (
    BotAppGrantLogModel,
    BotAppGrantModel,
    BotAppGrantRecord,
    GrantAction,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotAppGrantRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()


class BotAppGrantRepository(
    BotAppGrantRepositoryProtocol,
):
    """Unified ``BotAppGrantRepositoryProtocol`` implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        """Initialize with DatabasePlugin instance.

        Args:
            db: DatabasePlugin providing both session entrypoints — reads take
                ``orm_session()``, the two mutations take
                ``transactional_orm_session()``.
        """
        self._db = db
        self._Grant = BotAppGrantModel
        self._Log = BotAppGrantLogModel

    # ========================================================================
    # Mutations — each writes its live row and its log event in one session
    # ========================================================================

    def grant(self, data: Dict[str, Any]) -> BotAppGrantRecord:
        """Authorize an app for a bot, appending a ``granted`` event.

        Idempotent under concurrency, not just in sequence. Two callers can both
        pass the existence check before either inserts — the row they are looking
        for does not exist, so there is nothing to lock — and the loser hits the
        unique key. That is caught and resolved rather than raised: the state the
        caller asked for now holds, which is precisely what idempotency promises.
        Letting it escape would 500 a partner retrying a request that had in fact
        succeeded.

        Resolving it means one of two things. Usually the winner's row is there
        and is returned. Occasionally it is not — the winner revoked between the
        conflict and the re-read — and then the caller's own request has still
        not been served, so the insert is retried once. Retrying is bounded at a
        single attempt: a caller racing an endless stream of revocations has a
        problem no amount of looping here fixes, and an unbounded retry would
        turn it into a hot loop.
        """
        app_id = data["app_id"]
        bot_id = data["bot_id"]
        user_id = data["user_id"]
        owner_id = data["owner_id"]
        app_name = data["app_name"]
        env = get_current_env()

        try:
            return self._insert_grant(app_id, app_name, bot_id, user_id, owner_id, env)
        except IntegrityError:
            existing = self._reread_after_conflict(bot_id, user_id, app_id, env)
            if existing is not None:
                return existing
            # The winner's row was revoked before we could read it, so nothing
            # is live and this caller's grant still has not happened. One more
            # attempt; a second conflict propagates rather than looping.
            logger.info(
                "[bot_app_grant] insert conflict resolved to nothing live, "
                "retrying once: app_id=%s bot_id=%s user_id=%s",
                app_id,
                bot_id,
                user_id,
            )
            return self._insert_grant(
                app_id, app_name, bot_id, user_id, owner_id, env
            )

    def _reread_after_conflict(
        self, bot_id: str, user_id: str, app_id: int, env: str
    ) -> Optional[BotAppGrantRecord]:
        """The live row for this scope after an ``IntegrityError``, if any.

        A fresh session, deliberately: the failed transaction has already rolled
        back and closed, so this snapshot is taken after any winner committed.

        The read is scoped to exactly the tuple the insert targeted, plus the
        tenant guard's own predicate, so anything it can return is by
        construction a live grant for precisely the requested scope — never some
        other row that happened to violate a constraint.
        """
        with self._db.orm_session() as db:
            row = self._live_row(db, bot_id, user_id, app_id, env)
            if row is None:
                return None
            logger.info(
                "[bot_app_grant] lost the insert race, returning the winner's "
                "row: app_id=%s bot_id=%s user_id=%s",
                app_id,
                bot_id,
                user_id,
            )
            return row.to_record()

    def _insert_grant(
        self,
        app_id: int,
        app_name: str,
        bot_id: str,
        user_id: str,
        owner_id: str,
        env: str,
    ) -> BotAppGrantRecord:
        """The write half of :meth:`grant`, in one transaction."""
        with self._db.transactional_orm_session() as db:
            # No lock: see the module docstring. The unique key is what
            # serializes this, and a FOR UPDATE matching no row would take a gap
            # lock that deadlocks a concurrent inserter instead.
            existing = self._live_row(db, bot_id, user_id, app_id, env)
            if existing is not None:
                # Idempotent, and deliberately silent in the log: a repeated
                # grant is the same authorization, not a new period. Returning
                # the row untouched is what keeps gmt_create meaning "since
                # when", rather than "when someone last called this".
                logger.info(
                    "[bot_app_grant] grant is already live, returning it "
                    "unchanged: app_id=%s bot_id=%s user_id=%s",
                    app_id,
                    bot_id,
                    user_id,
                )
                return existing.to_record()

            row = self._Grant(
                app_id=app_id,
                app_name=app_name,
                bot_id=bot_id,
                user_id=user_id,
                owner_id=owner_id,
                env=env,
            )
            db.add(row)
            db.add(
                self._Log(
                    app_id=app_id,
                    app_name=app_name,
                    bot_id=bot_id,
                    user_id=user_id,
                    owner_id=owner_id,
                    action=GrantAction.GRANTED,
                    env=env,
                )
            )
            db.flush()
            db.refresh(row)
            logger.info(
                "[bot_app_grant] granted app_id=%s on bot_id=%s as user_id=%s "
                "(owner_id=%s)",
                app_id,
                bot_id,
                user_id,
                owner_id,
            )
            return row.to_record()

    def revoke(self, bot_id: str, user_id: str, app_id: int) -> bool:
        """Withdraw one user's delegation, appending a ``revoked`` event."""
        env = get_current_env()
        with self._db.transactional_orm_session() as db:
            row = self._live_row(db, bot_id, user_id, app_id, env, lock=True)
            if row is None:
                logger.info(
                    "[bot_app_grant] nothing to revoke: app_id=%s bot_id=%s "
                    "user_id=%s",
                    app_id,
                    bot_id,
                    user_id,
                )
                return False

            self._log_revocation(db, row)
            db.delete(row)
            logger.info(
                "[bot_app_grant] revoked app_id=%s on bot_id=%s as user_id=%s",
                app_id,
                bot_id,
                user_id,
            )
            return True

    def revoke_all_for_app_on_bot(self, bot_id: str, app_id: int) -> int:
        """Withdraw every user's delegation of one app on one bot."""
        return self._sweep(
            bot_id,
            app_id=app_id,
            reason="owner revoked an app outright",
        )

    def revoke_all_for_bot(self, bot_id: str) -> int:
        """Withdraw every authorization standing against a bot."""
        return self._sweep(bot_id, app_id=None, reason="bot deleted")

    def _sweep(self, bot_id: str, *, app_id: Optional[int], reason: str) -> int:
        """Delete a set of live rows and log one ``revoked`` event for each.

        One transaction for the whole set, not one per row: a sweep that
        committed halfway would leave a deleted bot — or a supposedly withdrawn
        application — still reaching it through whatever it did not get to.

        ``app_id=None`` means "every application", which is the bot-deletion
        case. It is an explicit parameter rather than two near-identical bodies
        so the two sweeps cannot drift in their locking or their logging.

        The rows are locked before deletion for the reason :meth:`revoke` locks
        its single row: a concurrent withdrawal of the same authorization must
        wait rather than log the revocation twice.
        """
        env = get_current_env()
        with self._db.transactional_orm_session() as db:
            query = db.query(self._Grant).filter(
                self._Grant.bot_id == bot_id,
                self._Grant.env == env,
            )
            if app_id is not None:
                query = query.filter(self._Grant.app_id == app_id)
            rows = query.with_for_update().all()
            for row in rows:
                self._log_revocation(db, row)
                db.delete(row)
            if rows:
                logger.info(
                    "[bot_app_grant] swept %s authorization(s) on bot_id=%s "
                    "(app_id=%s): %s",
                    len(rows),
                    bot_id,
                    app_id if app_id is not None else "all",
                    reason,
                )
            return len(rows)

    def _log_revocation(self, db: Any, row: BotAppGrantModel) -> None:
        """Append the ``revoked`` event for one live row, from the row itself.

        Built from the row rather than from the caller's arguments, so the
        history records what was actually in force — including the app name as
        it stood when consent was given, and the delegating user, neither of
        which the sweeping callers supply.
        """
        db.add(
            self._Log(
                app_id=row.app_id,
                app_name=row.app_name,
                bot_id=row.bot_id,
                user_id=row.user_id,
                owner_id=row.owner_id,
                action=GrantAction.REVOKED,
                env=row.env,
            )
        )

    # ========================================================================
    # Reads
    # ========================================================================

    def list_for_bot(self, bot_id: str) -> List[BotAppGrantRecord]:
        """The bot's view — every app that may reach it, and who let each in.

        No delegating-user predicate, deliberately: the bot's owner has to see a
        grant a collaborator made, or machine access to their own bot would be
        invisible to them. Served by ``idx_bot_app_grant_bot_owner``'s
        ``(avernet_tenant, bot_id)`` prefix.
        """
        with self._db.orm_session() as db:
            rows = (
                db.query(self._Grant)
                .filter(
                    self._Grant.bot_id == bot_id,
                    self._Grant.env == get_current_env(),
                )
                # id breaks the tie: gmt_create is second-granularity on
                # MySQL, so grants issued in the same second would otherwise
                # come back in an arbitrary order.
                .order_by(self._Grant.gmt_create.desc(), self._Grant.id.desc())
                .all()
            )
            return [row.to_record() for row in rows]

    def list_for_app(self, app_id: int, user_id: str) -> List[BotAppGrantRecord]:
        """The app's view — which bots may this app reach as this user."""
        with self._db.orm_session() as db:
            rows = (
                db.query(self._Grant)
                .filter(
                    self._Grant.app_id == app_id,
                    self._Grant.user_id == user_id,
                    self._Grant.env == get_current_env(),
                )
                # id breaks the tie: gmt_create is second-granularity on
                # MySQL, so grants issued in the same second would otherwise
                # come back in an arbitrary order.
                .order_by(self._Grant.gmt_create.desc(), self._Grant.id.desc())
                .all()
            )
            return [row.to_record() for row in rows]

    def find(
        self, bot_id: str, user_id: str, app_id: int
    ) -> Optional[BotAppGrantRecord]:
        """One live authorization, or ``None`` when the app may not act as this user."""
        with self._db.orm_session() as db:
            row = self._live_row(db, bot_id, user_id, app_id, get_current_env())
            return row.to_record() if row else None

    # ========================================================================
    # Internals
    # ========================================================================

    def _live_row(
        self,
        db: Any,
        bot_id: str,
        user_id: str,
        app_id: int,
        env: str,
        *,
        lock: bool = False,
    ) -> Optional[BotAppGrantModel]:
        """The live row for one delegation, inside an open session.

        Keyed on ``user_id`` rather than ``owner_id`` — this is the unique key,
        so the lookup is a point probe, and scoping it to the delegating user is
        what keeps two collaborators' grants on one bot from being mistaken for
        each other.

        Takes the session rather than opening its own, so a caller that has
        already begun a transaction reads and writes within it — which is what
        makes the idempotent grant and the revoke atomic.

        ``lock`` adds ``FOR UPDATE`` and belongs only to ``revoke``, which holds
        an existing row across its check-then-delete so a concurrent withdrawal
        waits rather than double-logging. Reads leave it off — taking write
        locks on a listing would serialize callers who are only looking — and so
        does the insert path, where there is no row to lock and the clause would
        take a deadlock-prone gap lock instead.

        **SQLite compiles ``FOR UPDATE`` away entirely**, and every session a
        test can reach is SQLite. So a green suite says nothing about the
        locking below; only MySQL/OceanBase exercises it. Stated here because
        the next reader will otherwise assume the tests cover it.
        """
        query = db.query(self._Grant).filter(
            self._Grant.app_id == app_id,
            self._Grant.bot_id == bot_id,
            self._Grant.user_id == user_id,
            self._Grant.env == env,
        )
        if lock:
            query = query.with_for_update()
        return query.first()
