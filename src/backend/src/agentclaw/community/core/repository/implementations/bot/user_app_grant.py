"""User→app account-level authorization repository (prod OceanBase + local SQLite).

One ORM implementation behind ``UserAppGrantRepositoryProtocol``, in the shape
of ``app_grant.py`` and for the same reasons:

- Each mutation writes its live row and its log event inside **one**
  ``transactional_orm_session()``, so the history cannot disagree with the
  live state. Reads stay on ``orm_session()``.
- ``revoke`` takes ``with_for_update()`` on the row it is about to delete, so
  a concurrent withdrawal waits rather than double-logging. ``grant`` does
  not: a ``FOR UPDATE`` matching nothing takes a deadlock-prone gap lock on
  InnoDB, and the unique key already serializes the insert; the loser is
  handled below. (SQLite compiles the clause away, so only MySQL/OceanBase
  exercises the locking.)
- The tenant is never passed in or filtered on here:
  ``register_avernet_tenant_guard`` stamps it on insert and appends the
  tenant predicate to every read.
- ``env`` is always this process's ``get_current_env()``, never a caller's.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from injector import inject
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.repository.protocols.bot import (
    UserAppGrantRepositoryProtocol,
)
from agentclaw.community.core.user_app_grant.models import (
    UserAppGrantLogModel,
    UserAppGrantModel,
    UserAppGrantRecord,
    UserGrantAction,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()


class UserAppGrantRepository(UserAppGrantRepositoryProtocol):
    """Unified ``UserAppGrantRepositoryProtocol`` implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._Grant = UserAppGrantModel
        self._Log = UserAppGrantLogModel

    # ========================================================================
    # Mutations — each writes its live row and its log event in one session
    # ========================================================================

    def grant(self, data: Dict[str, Any]) -> UserAppGrantRecord:
        """Authorize an app to act as a user, appending a ``granted`` event.

        Idempotent under concurrency, not just in sequence. Two callers can
        both pass the existence check before either inserts; the loser hits
        the unique key, which is caught and resolved rather than raised. If the
        winner's row was revoked before it could be re-read, the insert is
        retried exactly once; a second conflict propagates rather than looping.
        """
        app_id = data["app_id"]
        user_id = data["user_id"]
        app_name = data["app_name"]
        env = get_current_env()

        try:
            return self._insert_grant(app_id, app_name, user_id, env)
        except IntegrityError:
            existing = self._reread_after_conflict(user_id, app_id, env)
            if existing is not None:
                return existing
            logger.info(
                "[user_app_grant] insert conflict resolved to nothing live, "
                "retrying once: app_id=%s user_id=%s",
                app_id,
                user_id,
            )
            return self._insert_grant(app_id, app_name, user_id, env)

    def _reread_after_conflict(
        self, user_id: str, app_id: int, env: str
    ) -> Optional[UserAppGrantRecord]:
        """The live row for this scope after an ``IntegrityError``, if any.

        A fresh session, deliberately: the failed transaction has already
        rolled back, so this snapshot is taken after any winner committed.
        """
        with self._db.orm_session() as db:
            row = self._live_row(db, user_id, app_id, env)
            if row is None:
                return None
            logger.info(
                "[user_app_grant] lost the insert race, returning the winner's "
                "row: app_id=%s user_id=%s",
                app_id,
                user_id,
            )
            return row.to_record()

    def _insert_grant(
        self, app_id: int, app_name: str, user_id: str, env: str
    ) -> UserAppGrantRecord:
        """The write half of :meth:`grant`, in one transaction."""
        with self._db.transactional_orm_session() as db:
            existing = self._live_row(db, user_id, app_id, env)
            if existing is not None:
                # Idempotent, and silent in the log: a repeated grant is the
                # same authorization, not a new period, which is what keeps
                # gmt_create meaning "since when".
                logger.info(
                    "[user_app_grant] grant is already live, returning it "
                    "unchanged: app_id=%s user_id=%s",
                    app_id,
                    user_id,
                )
                return existing.to_record()

            row = self._Grant(
                app_id=app_id, app_name=app_name, user_id=user_id, env=env
            )
            db.add(row)
            db.add(
                self._Log(
                    app_id=app_id,
                    app_name=app_name,
                    user_id=user_id,
                    action=UserGrantAction.GRANTED,
                    env=env,
                )
            )
            db.flush()
            db.refresh(row)
            logger.info(
                "[user_app_grant] granted app_id=%s as user_id=%s at the "
                "account level",
                app_id,
                user_id,
            )
            return row.to_record()

    def revoke(self, user_id: str, app_id: int) -> bool:
        """Withdraw one user's authorization, appending a ``revoked`` event."""
        env = get_current_env()
        with self._db.transactional_orm_session() as db:
            row = self._live_row(db, user_id, app_id, env, lock=True)
            if row is None:
                logger.info(
                    "[user_app_grant] nothing to revoke: app_id=%s user_id=%s",
                    app_id,
                    user_id,
                )
                return False
            # Built from the row rather than the caller's arguments, so the
            # history records the app name as it stood when consent was given.
            db.add(
                self._Log(
                    app_id=row.app_id,
                    app_name=row.app_name,
                    user_id=row.user_id,
                    action=UserGrantAction.REVOKED,
                    env=row.env,
                )
            )
            db.delete(row)
            logger.info(
                "[user_app_grant] revoked app_id=%s as user_id=%s at the "
                "account level",
                app_id,
                user_id,
            )
            return True

    # ========================================================================
    # Reads
    # ========================================================================

    def list_for_user(self, user_id: str) -> List[UserAppGrantRecord]:
        """The user's view — every app that may act as them."""
        with self._db.orm_session() as db:
            rows = (
                db.query(self._Grant)
                .filter(
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

    def find(self, user_id: str, app_id: int) -> Optional[UserAppGrantRecord]:
        """One live authorization, or ``None`` when the app may not act as this user."""
        with self._db.orm_session() as db:
            row = self._live_row(db, user_id, app_id, get_current_env())
            return row.to_record() if row else None

    # ========================================================================
    # Internals
    # ========================================================================

    def _live_row(
        self,
        db: Any,
        user_id: str,
        app_id: int,
        env: str,
        *,
        lock: bool = False,
    ) -> Optional[UserAppGrantModel]:
        """The live row for one authorization, inside an open session.

        Takes the session rather than opening its own, so a caller that has
        already begun a transaction reads and writes within it — which is what
        makes the idempotent grant and the revoke atomic. ``lock`` adds
        ``FOR UPDATE`` and belongs only to ``revoke``.
        """
        query = db.query(self._Grant).filter(
            self._Grant.app_id == app_id,
            self._Grant.user_id == user_id,
            self._Grant.env == env,
        )
        if lock:
            query = query.with_for_update()
        return query.first()
