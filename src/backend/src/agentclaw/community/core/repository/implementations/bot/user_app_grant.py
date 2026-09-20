"""User→app delegation repository (prod OceanBase + local SQLite).

One ORM implementation behind ``UserAppGrantRepositoryProtocol``, with the
same session discipline as ``app_grant.py``: each mutation writes its live row
and its log event inside one ``transactional_orm_session()``, reads stay on
``orm_session()``, the tenant is never passed in (the guard stamps and filters
it), and ``env`` is always this process's own.

Simpler than the bot grant in one respect that is worth naming: the unique key
carries every identity the row has, so there is no "same slot, different bot"
collision to refuse. A second grant of a live pair is the same delegation.
"""
from __future__ import annotations

from typing import Any, List, Optional

from injector import inject
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.bot_app_grant.models import (
    GrantAction,
    UserAppGrantLogModel,
    UserAppGrantModel,
    UserAppGrantRecord,
)
from agentclaw.community.core.repository.protocols.bot import (
    UserAppGrantRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()


class UserAppGrantRepository(
    UserAppGrantRepositoryProtocol,
):
    """Unified ``UserAppGrantRepositoryProtocol`` implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._Grant = UserAppGrantModel
        self._Log = UserAppGrantLogModel

    # ========================================================================
    # Mutations
    # ========================================================================

    def grant(
        self, *, app_id: int, app_name: str, user_id: str
    ) -> UserAppGrantRecord:
        """Record a delegation, appending a ``granted`` event.

        Idempotent under concurrency the way the bot grant is: the loser of an
        insert race hits the unique key, re-reads the winner's row and returns
        it — the state the caller asked for now holds. If the winner revoked
        in between, the insert is retried exactly once.
        """
        env = get_current_env()
        try:
            return self._insert(app_id, app_name, user_id, env)
        except IntegrityError:
            with self._db.orm_session() as db:
                row = self._live_row(db, user_id, app_id, env)
                if row is not None:
                    logger.info(
                        "[user_app_grant] lost the insert race, returning the "
                        "winner's row: app_id=%s user_id=%s",
                        app_id,
                        user_id,
                    )
                    return row.to_record()
            return self._insert(app_id, app_name, user_id, env)

    def _insert(
        self, app_id: int, app_name: str, user_id: str, env: str
    ) -> UserAppGrantRecord:
        with self._db.transactional_orm_session() as db:
            existing = self._live_row(db, user_id, app_id, env)
            if existing is not None:
                # Same delegation, not a new period: ``gmt_create`` keeps
                # meaning "since when".
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
                    action=GrantAction.GRANTED,
                    env=env,
                )
            )
            db.flush()
            db.refresh(row)
            logger.info(
                "[user_app_grant] granted app_id=%s as user_id=%s", app_id, user_id
            )
            return row.to_record()

    def revoke(self, user_id: str, app_id: int) -> bool:
        """Withdraw one delegation, appending a ``revoked`` event."""
        env = get_current_env()
        with self._db.transactional_orm_session() as db:
            row = self._live_row(db, user_id, app_id, env, lock=True)
            if row is None:
                return False
            db.add(
                self._Log(
                    app_id=row.app_id,
                    app_name=row.app_name,
                    user_id=row.user_id,
                    action=GrantAction.REVOKED,
                    env=row.env,
                )
            )
            db.delete(row)
            logger.info(
                "[user_app_grant] revoked app_id=%s as user_id=%s", app_id, user_id
            )
            return True

    # ========================================================================
    # Reads
    # ========================================================================

    def find(self, user_id: str, app_id: int) -> Optional[UserAppGrantRecord]:
        """The live delegation for this pair, or ``None``."""
        with self._db.orm_session() as db:
            row = self._live_row(db, user_id, app_id, get_current_env())
            return row.to_record() if row else None

    def list_for_user(self, user_id: str) -> List[UserAppGrantRecord]:
        """Every application that may act as ``user_id``, newest first."""
        with self._db.orm_session() as db:
            rows = (
                db.query(self._Grant)
                .filter(
                    self._Grant.user_id == user_id,
                    self._Grant.env == get_current_env(),
                )
                .order_by(self._Grant.gmt_create.desc(), self._Grant.id.desc())
                .all()
            )
            return [row.to_record() for row in rows]

    # ========================================================================
    # Internals
    # ========================================================================

    def _live_row(
        self, db: Any, user_id: str, app_id: int, env: str, *, lock: bool = False
    ) -> Optional[UserAppGrantModel]:
        query = db.query(self._Grant).filter(
            self._Grant.app_id == app_id,
            self._Grant.user_id == user_id,
            self._Grant.env == env,
        )
        if lock:
            query = query.with_for_update()
        return query.first()
