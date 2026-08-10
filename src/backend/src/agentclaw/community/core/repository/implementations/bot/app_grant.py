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
- The check-then-write in each mutation takes ``with_for_update()`` on the live
  row, so two concurrent callers serialize rather than racing the unique key.
  The insert path cannot lock a row that does not exist yet, so it also handles
  the loser of that race — see :meth:`BotAppGrantRepository.grant`.
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
        pass the existence check before either inserts — the row they are
        looking for does not exist, so there is nothing to lock — and the loser
        hits the unique key. That is caught and re-read rather than raised: the
        state the caller asked for now holds, which is precisely the outcome
        idempotency promises. Letting it escape would 500 a partner retrying a
        request that had in fact succeeded.
        """
        app_id = data["app_id"]
        bot_id = data["bot_id"]
        owner_id = data["owner_id"]
        app_name = data["app_name"]
        env = get_current_env()

        try:
            return self._insert_grant(app_id, app_name, bot_id, owner_id, env)
        except IntegrityError:
            # The unique key fired, so a concurrent caller won. Re-read outside
            # the failed transaction and return what they wrote.
            logger.info(
                "[bot_app_grant] lost the insert race, returning the winner's "
                "row: app_id=%s bot_id=%s owner_id=%s",
                app_id,
                bot_id,
                owner_id,
            )
            with self._db.orm_session() as db:
                row = self._live_row(db, bot_id, owner_id, app_id, env)
                if row is None:
                    # The winner's row is gone already — revoked between the
                    # conflict and this read. Nothing is live, so re-raising is
                    # the honest answer rather than inventing a record.
                    raise
                return row.to_record()

    def _insert_grant(
        self, app_id: int, app_name: str, bot_id: str, owner_id: str, env: str
    ) -> BotAppGrantRecord:
        """The write half of :meth:`grant`, in one transaction."""
        with self._db.transactional_orm_session() as db:
            existing = self._live_row(db, bot_id, owner_id, app_id, env, lock=True)
            if existing is not None:
                # Idempotent, and deliberately silent in the log: a repeated
                # grant is the same authorization, not a new period. Returning
                # the row untouched is what keeps gmt_create meaning "since
                # when", rather than "when someone last called this".
                logger.info(
                    "[bot_app_grant] grant is already live, returning it "
                    "unchanged: app_id=%s bot_id=%s owner_id=%s",
                    app_id,
                    bot_id,
                    owner_id,
                )
                return existing.to_record()

            row = self._Grant(
                app_id=app_id,
                app_name=app_name,
                bot_id=bot_id,
                owner_id=owner_id,
                env=env,
            )
            db.add(row)
            db.add(
                self._Log(
                    app_id=app_id,
                    app_name=app_name,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    action=GrantAction.GRANTED,
                    env=env,
                )
            )
            db.flush()
            db.refresh(row)
            logger.info(
                "[bot_app_grant] granted app_id=%s to bot_id=%s owner_id=%s",
                app_id,
                bot_id,
                owner_id,
            )
            return row.to_record()

    def revoke(self, bot_id: str, owner_id: str, app_id: int) -> bool:
        """Withdraw an authorization, appending a ``revoked`` event."""
        env = get_current_env()
        with self._db.transactional_orm_session() as db:
            row = self._live_row(db, bot_id, owner_id, app_id, env, lock=True)
            if row is None:
                logger.info(
                    "[bot_app_grant] nothing to revoke: app_id=%s bot_id=%s "
                    "owner_id=%s",
                    app_id,
                    bot_id,
                    owner_id,
                )
                return False

            # The log row is built from the live row rather than from the
            # caller's arguments, so the history records what was actually in
            # force — including the app name as it stood when consent was
            # given, which the caller never supplies on this path.
            db.add(
                self._Log(
                    app_id=row.app_id,
                    app_name=row.app_name,
                    bot_id=row.bot_id,
                    owner_id=row.owner_id,
                    action=GrantAction.REVOKED,
                    env=row.env,
                )
            )
            db.delete(row)
            logger.info(
                "[bot_app_grant] revoked app_id=%s from bot_id=%s owner_id=%s",
                app_id,
                bot_id,
                owner_id,
            )
            return True

    # ========================================================================
    # Reads
    # ========================================================================

    def list_for_bot(self, bot_id: str, owner_id: str) -> List[BotAppGrantRecord]:
        """The owner's view — which apps may reach this bot."""
        with self._db.orm_session() as db:
            rows = (
                db.query(self._Grant)
                .filter(
                    self._Grant.bot_id == bot_id,
                    self._Grant.owner_id == owner_id,
                    self._Grant.env == get_current_env(),
                )
                # id breaks the tie: gmt_create is second-granularity on
                # MySQL, so grants issued in the same second would otherwise
                # come back in an arbitrary order.
                .order_by(self._Grant.gmt_create.desc(), self._Grant.id.desc())
                .all()
            )
            return [row.to_record() for row in rows]

    def list_for_app(self, app_id: int, owner_id: str) -> List[BotAppGrantRecord]:
        """The app's view — which of this owner's bots may this app reach."""
        with self._db.orm_session() as db:
            rows = (
                db.query(self._Grant)
                .filter(
                    self._Grant.app_id == app_id,
                    self._Grant.owner_id == owner_id,
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
        self, bot_id: str, owner_id: str, app_id: int
    ) -> Optional[BotAppGrantRecord]:
        """One live authorization, or ``None`` when the app may not reach the bot."""
        with self._db.orm_session() as db:
            row = self._live_row(db, bot_id, owner_id, app_id, get_current_env())
            return row.to_record() if row else None

    # ========================================================================
    # Internals
    # ========================================================================

    def _live_row(
        self,
        db: Any,
        bot_id: str,
        owner_id: str,
        app_id: int,
        env: str,
        *,
        lock: bool = False,
    ) -> Optional[BotAppGrantModel]:
        """The live row for one scope, inside an open session.

        Takes the session rather than opening its own, so a caller that has
        already begun a transaction reads and writes within it — which is what
        makes the idempotent grant and the revoke atomic.

        ``lock`` adds ``FOR UPDATE`` and belongs only to the two mutations: it
        holds the row across their check-then-write so a concurrent caller
        waits rather than interleaving. Reads leave it off — taking write locks
        on a listing would serialize callers who are only looking.
        """
        query = db.query(self._Grant).filter(
            self._Grant.app_id == app_id,
            self._Grant.bot_id == bot_id,
            self._Grant.owner_id == owner_id,
            self._Grant.env == env,
        )
        if lock:
            query = query.with_for_update()
        return query.first()
