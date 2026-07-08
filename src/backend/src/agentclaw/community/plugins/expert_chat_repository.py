"""Unified ExpertChat repository (prod ZDAS + local SQLite).

One ORM implementation behind the ``ExpertChatRepository`` Protocol.
The only per-environment difference is the injected
:class:`DatabasePlugin`: ``orm_session()`` yields a SQLAlchemy
``Session`` in both runtimes, so this single body runs unchanged on
OceanBase (prod) and SQLite (local), collapsing the previous
raw-SQL/ORM twins so CI exercises the prod path too.

Prod-twin parity:

- ``add_chat_bot`` is the prod twin's atomic
  ``INSERT ... ON DUPLICATE KEY UPDATE`` on the unique key
  ``uk_user_bot_owner_env``. Kept as a single atomic statement on both
  backends via a dialect-aware upsert (``on_conflict_do_update`` on
  SQLite, ``on_duplicate_key_update`` on MySQL/OceanBase) — the branch
  is purely dialect-correctness. On
  MySQL/OceanBase the inserted/updated id is read straight off the
  execute result via the ``LAST_INSERT_ID(id)`` trick so prod is one
  round-trip. The SQLite arm pays one follow-up PK SELECT because
  ``RETURNING`` on ``ON CONFLICT DO UPDATE`` requires SQLite >= 3.35
  and not every CI image ships it. The prior local twin's
  SELECT-then-write (2 statements, non-atomic under prod AUTOCOMMIT)
  is intentionally dropped.
- ``remove_chat_bot`` / ``delete_session`` are single blind UPDATEs
  returning ``rowcount > 0`` (prod parity; SQLAlchemy mysqlconnector
  sets CLIENT_FOUND_ROWS so rowcount = rows matched, same as SQLite).
- ``save_session`` is a single blind UPDATE (no-op if absent).
- ``gmt_create``/``gmt_modified`` are model server defaults; bulk
  UPDATEs set ``gmt_modified=func.now()`` (DB-side), matching the prod
  twins' literal ``gmt_modified = NOW()``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from injector import inject
from sqlalchemy import func

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()


class ExpertChatRepository:
    """Unified ``ExpertChatRepository`` Protocol implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        from agentclaw.community.core.expert_chat.sqlite_models import (
            AcExpertChatBotSession,
        )

        self._db = db
        self.Model = AcExpertChatBotSession

    def add_chat_bot(
        self, user_id: str, bot_id: str, owner_id: str
    ) -> Dict[str, Any]:
        """Atomic upsert on uk_user_bot_owner_env (prod parity)."""
        env = get_current_env()
        with self._db.orm_session() as db:
            # get_bind() is the documented session→engine accessor in
            # both SA 1.4 and 2.0 (Session.bind has no 2.0 guarantee).
            # Dialect branch (not mode branch): both arms perform the
            # *same* logical operation — an atomic INSERT-or-update on
            # the ``uk_user_bot_owner_env`` unique key — but SQLAlchemy
            # exposes the ON CONFLICT / ON DUPLICATE KEY syntax through
            # two dialect-specific ``insert()`` constructs. There is no
            # core/portable equivalent that preserves the single-
            # statement atomicity we need for prod parity, so we
            # dispatch on ``dialect.name`` here rather than on runtime
            # mode. This is the *only* allowed shape of dialect branch
            # under the unified-repo rule (Rule 14 / Round-3 guarantees).
            #
            # TODO(repo-unify): collapse this branch once we adopt a
            # shared upsert helper (e.g. a small ``atomic_upsert(table,
            # values, conflict_cols, update_cols)`` utility in
            # ``plugins/_sql_helpers.py``) so call sites stop
            # importing dialect-specific ``insert``. Track alongside
            # the remaining Round-3 sessions.
            dialect = db.get_bind().dialect.name
            table = self.Model.__table__
            values = {
                "user_id": user_id,
                "bot_id": bot_id,
                "owner_id": owner_id,
                "status": "ACTIVE",
                "env": env,
            }
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as _insert

                # SQLite-only follow-up SELECT after the atomic upsert:
                # ``RETURNING`` on ``ON CONFLICT DO UPDATE`` needs
                # SQLite >= 3.35, which not every CI image ships. The
                # one-statement prod-parity goal applies on MySQL/OB
                # (handled in the else arm); SQLite is the local/test
                # backend where a primary-key lookup on the row we just
                # touched is effectively free.
                stmt = _insert(table).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[
                        "user_id",
                        "bot_id",
                        "owner_id",
                        "env",
                    ],
                    set_={"status": "ACTIVE", "gmt_modified": func.now()},
                )
                db.execute(stmt)
                db.flush()
                row_id = (
                    db.query(self.Model.id)
                    .filter(
                        self.Model.user_id == user_id,
                        self.Model.bot_id == bot_id,
                        self.Model.owner_id == owner_id,
                        self.Model.env == env,
                    )
                    .scalar()
                )
            else:
                from sqlalchemy.dialects.mysql import insert as _insert

                # ``id = LAST_INSERT_ID(id)`` is the MySQL idiom that
                # makes ``LAST_INSERT_ID()`` return the existing row's
                # PK on the update path (it already returns the new id
                # on the insert path). Lets ``result.lastrowid`` work
                # uniformly for both arms of the upsert, so no follow-
                # up SELECT is needed.
                stmt = _insert(table).values(**values)
                stmt = stmt.on_duplicate_key_update(
                    id=func.LAST_INSERT_ID(self.Model.id),
                    status="ACTIVE",
                    gmt_modified=func.now(),
                )
                row_id = db.execute(stmt).lastrowid
            logger.info(
                "[ExpertChatRepo] add_chat_bot user=%s bot=%s owner=%s",
                user_id,
                bot_id,
                owner_id,
            )
            return {
                "id": row_id,
                "user_id": user_id,
                "bot_id": bot_id,
                "owner_id": owner_id,
                "status": "ACTIVE",
            }

    def remove_chat_bot(
        self, user_id: str, bot_id: str, owner_id: str
    ) -> bool:
        env = get_current_env()
        with self._db.orm_session() as db:
            rowcount = (
                db.query(self.Model)
                .filter(
                    self.Model.user_id == user_id,
                    self.Model.bot_id == bot_id,
                    self.Model.owner_id == owner_id,
                    self.Model.env == env,
                )
                .update(
                    {
                        self.Model.status: "REMOVED",
                        self.Model.gmt_modified: func.now(),
                    },
                    synchronize_session=False,
                )
            )
            return rowcount > 0

    def list_chat_bots(self, user_id: str) -> List[Dict[str, str]]:
        env = get_current_env()
        with self._db.orm_session() as db:
            rows = (
                db.query(self.Model)
                .filter(
                    self.Model.user_id == user_id,
                    self.Model.status == "ACTIVE",
                    self.Model.env == env,
                )
                .order_by(self.Model.gmt_create.desc())
                .all()
            )
            return [
                {"bot_id": r.bot_id, "owner_id": r.owner_id} for r in rows
            ]

    def get_session(
        self, user_id: str, bot_id: str, owner_id: str
    ) -> Optional[str]:
        env = get_current_env()
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(
                    self.Model.user_id == user_id,
                    self.Model.bot_id == bot_id,
                    self.Model.owner_id == owner_id,
                    self.Model.env == env,
                )
                .first()
            )
            return row.session_key if row else None

    def save_session(
        self, user_id: str, bot_id: str, owner_id: str, session_key: str
    ) -> None:
        # Single blind UPDATE — no-op if the row is absent (prod parity).
        env = get_current_env()
        with self._db.orm_session() as db:
            db.query(self.Model).filter(
                self.Model.user_id == user_id,
                self.Model.bot_id == bot_id,
                self.Model.owner_id == owner_id,
                self.Model.env == env,
            ).update(
                {
                    self.Model.session_key: session_key,
                    self.Model.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )

    def delete_session(
        self, user_id: str, bot_id: str, owner_id: str
    ) -> bool:
        env = get_current_env()
        with self._db.orm_session() as db:
            rowcount = (
                db.query(self.Model)
                .filter(
                    self.Model.user_id == user_id,
                    self.Model.bot_id == bot_id,
                    self.Model.owner_id == owner_id,
                    self.Model.env == env,
                )
                .update(
                    {
                        self.Model.session_key: None,
                        self.Model.gmt_modified: func.now(),
                    },
                    synchronize_session=False,
                )
            )
            return rowcount > 0
