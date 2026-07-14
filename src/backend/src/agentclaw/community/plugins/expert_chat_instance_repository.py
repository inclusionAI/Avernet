"""Unified ExpertChatInstance repository (prod ZDAS + local SQLite).

One ORM implementation behind the ``ExpertChatInstanceRepository``
Protocol (defined in
``core/expert_chat/repository/expert_chat_instance_repository.py``).

Mirrors the ``ExpertChatRepository`` patterns in
``plugins/expert_chat_repository.py``: a single body running unchanged
on OceanBase (prod) and SQLite (local) via the injected
:class:`DatabasePlugin`. The two implementations live in separate files
to keep the session/​bot-list ledger and the caller-instance ledger
decoupled — neither references the other.

Prod-twin parity (same rationale as the session repo):

- ``upsert_instance`` is an atomic ``INSERT ... ON CONFLICT/ON DUPLICATE
  KEY UPDATE`` on the unique key ``uk_bi_oi_ui_e``. Dialect-correctness
  branch only (SQLite ``on_conflict_do_update`` vs MySQL/OceanBase
  ``on_duplicate_key_update``); the MySQL arm reuses the
  ``LAST_INSERT_ID(id)`` trick so the upserted id reads off the execute
  result in one round-trip, the SQLite arm pays one follow-up PK SELECT
  (``RETURNING`` on ``ON CONFLICT DO UPDATE`` needs SQLite >= 3.35).
- ``update_instance`` is a single blind UPDATE returning ``rowcount >
  0`` (no-op if absent).
- ``ext`` round-trips as JSON: serialized at write, deserialized at read
  via the model's ``to_dict()``.
- ``gmt_modified`` is bumped DB-side (``func.now()``) on every write,
  matching the prod twins' ``NOW()``.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from injector import inject
from sqlalchemy import func

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()


class ExpertChatInstanceRepository:
    """Unified ``ExpertChatInstanceRepository`` Protocol implementation.

    One ORM body (``AcExpertChatInstance``) behind the Protocol, runs
    unchanged on OceanBase (prod) and SQLite (local) via the injected
    :class:`DatabasePlugin`. Mirrors the ``ExpertChatRepository``
    patterns: ``get_current_env()`` scoping, dialect-aware atomic upsert
    on the ``(bot_id, owner_id, user_id, env)`` unique key, blind
    partial UPDATE for ``update_instance``.

    ``ext`` is JSON-encoded at write time and decoded at read time; the
    protocol layer always sees plain dicts.
    """

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        from agentclaw.community.core.expert_chat.sqlite_models import (
            AcExpertChatInstance,
        )

        self._db = db
        self.Model = AcExpertChatInstance

    def get_instance(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
    ) -> Optional[Dict[str, Any]]:
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
            return row.to_dict() if row else None

    def upsert_instance(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        status: str,
        ext: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Atomic insert-or-replace on ``uk_bi_oi_ui_e`` (prod parity).

        ``ext`` is stored as full-overwrite JSON; the existing row, if
        any, gets its ``status`` / ``ext`` replaced wholesale (caller is
        the single owner of the instance ext and always writes the full
        picture). On update we do NOT touch ``gmt_create``.
        """
        env = get_current_env()
        ext_json = json.dumps(ext) if ext is not None else None
        with self._db.orm_session() as db:
            dialect = db.get_bind().dialect.name
            table = self.Model.__table__
            values = {
                "user_id": user_id,
                "bot_id": bot_id,
                "owner_id": owner_id,
                "status": status,
                "ext": ext_json,
                "env": env,
            }
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as _insert

                stmt = _insert(table).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["bot_id", "owner_id", "user_id", "env"],
                    set_={
                        "status": status,
                        "ext": ext_json,
                        "gmt_modified": func.now(),
                    },
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

                stmt = _insert(table).values(**values)
                stmt = stmt.on_duplicate_key_update(
                    id=func.LAST_INSERT_ID(self.Model.id),
                    status=status,
                    ext=ext_json,
                    gmt_modified=func.now(),
                )
                row_id = db.execute(stmt).lastrowid

            row = db.query(self.Model).filter(self.Model.id == row_id).first()
            logger.info(
                "[ExpertChatInstanceRepo] upsert_instance user=%s bot=%s "
                "owner=%s status=%s",
                user_id,
                bot_id,
                owner_id,
                status,
            )
            return row.to_dict() if row else {
                "id": row_id,
                "user_id": user_id,
                "bot_id": bot_id,
                "owner_id": owner_id,
                "status": status,
                "ext": ext,
                "env": env,
            }

    def update_instance(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        status: Optional[str] = None,
        ext: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Blind partial UPDATE — no-op if the row is absent (prod parity).

        Only non-None fields are written (caller opts into per-field
        updates). ``ext`` is whole-overwrite, not a merge: read-modify-
        write is the caller's responsibility.
        """
        env = get_current_env()
        updates: Dict[str, Any] = {self.Model.gmt_modified: func.now()}
        if status is not None:
            updates[self.Model.status] = status
        if ext is not None:
            updates[self.Model.ext] = json.dumps(ext)
        with self._db.orm_session() as db:
            rowcount = (
                db.query(self.Model)
                .filter(
                    self.Model.user_id == user_id,
                    self.Model.bot_id == bot_id,
                    self.Model.owner_id == owner_id,
                    self.Model.env == env,
                )
                .update(updates, synchronize_session=False)
            )
            return rowcount > 0