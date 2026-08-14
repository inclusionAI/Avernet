"""Unified system-config repository (prod OceanBase + local SQLite).

One ORM implementation behind ``ConfigRepositoryProtocol`` (two
tables: ``ac_config_category`` + ``ac_config_item``). The only
per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so
this single body runs unchanged on OceanBase (prod) and SQLite
(local), collapsing the previous raw-SQL/ORM twins.

Prod-twin parity (the raw-SQL ``ZdasConfigRepository`` is the
reference):

- ``upsert_category`` / ``upsert_config`` are the prod twin's atomic
  ``INSERT ... ON DUPLICATE KEY UPDATE`` on the unique keys
  ``uk_category_env`` / ``uk_parent_key``. Kept as a single atomic
  statement on both backends via the same dialect-aware upsert used by
  ``expert_chat_repository.add_chat_bot`` (``on_conflict_do_update``
  on SQLite + follow-up PK SELECT; ``on_duplicate_key_update`` on
  MySQL/OceanBase + ``LAST_INSERT_ID(id)``). The branch is purely
  dialect-correctness — runtime mode never enters the picture. The
  prod ``description = COALESCE(VALUES(description), description)``
  semantic is preserved exactly: an incoming ``None`` description
  keeps the existing value; ``creator`` is written only on insert
  (untouched on the update path), matching prod.
- ``delete_category`` / ``delete_config`` are **single blind
  DELETEs** returning ``rowcount > 0`` (prod parity; mysqlconnector
  sets CLIENT_FOUND_ROWS so rowcount = rows matched, same as SQLite).
  ``delete_category`` relies on the database FK ``ON DELETE CASCADE``
  to remove child ``ac_config_item`` rows — exactly as prod's single
  ``DELETE FROM ac_config_category WHERE id=%s`` does. SQLite enforces
  this only with ``PRAGMA foreign_keys=ON``, which the local
  ``SqliteDB`` engine now sets via a connect listener (see
  ``plugins/local/database.py``). The old SQLite twin's manual
  child-then-parent two-statement cascade is dropped.
- Reads return the same ``ConfigCategoryRecord`` / ``ConfigItemRecord``
  / dict shapes as the twins.
"""
from __future__ import annotations

import json
from typing import Any

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.system_config.models import (
    ConfigCategoryRecord,
    ConfigItemRecord,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.system_config.orm import AcConfigCategory, AcConfigItem
from agentclaw.community.core.repository.protocols.config import ConfigRepositoryProtocol

logger = get_logger()


class ConfigRepository(
    ConfigRepositoryProtocol,
):
    """Unified ORM ``ConfigRepositoryProtocol`` implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    # ── row → record ────────────────────────────────────────────

    @staticmethod
    def _to_category_record(
        m: AcConfigCategory | None,
    ) -> ConfigCategoryRecord | None:
        if m is None:
            return None
        return ConfigCategoryRecord(
            id=m.id,
            category=m.category,
            category_name=m.category_name,
            description=m.description,
            env=m.env,
            creator=m.creator,
            modifier=m.modifier,
            gmt_create=m.gmt_create,
            gmt_modified=m.gmt_modified,
        )

    @staticmethod
    def _to_config_record(
        m: AcConfigItem | None,
    ) -> ConfigItemRecord | None:
        if m is None:
            return None
        return ConfigItemRecord(
            id=m.id,
            parent_id=m.parent_id,
            config_key=m.config_key,
            config_value=m.config_value,
            description=m.description,
            creator=m.creator,
            modifier=m.modifier,
            gmt_create=m.gmt_create,
            gmt_modified=m.gmt_modified,
        )

    @staticmethod
    def _parse_value(value: str | None) -> Any:
        if value is None:
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value

    # ── category ops ────────────────────────────────────────────

    def get_category_by_id(
        self, *, category_id: int
    ) -> ConfigCategoryRecord | None:
        with self._db.orm_session() as db:
            m = (
                db.query(AcConfigCategory)
                .filter(AcConfigCategory.id == category_id)
                .first()
            )
            return self._to_category_record(m)

    def get_category(
        self, *, category: str, env: str
    ) -> ConfigCategoryRecord | None:
        with self._db.orm_session() as db:
            m = (
                db.query(AcConfigCategory)
                .filter(
                    AcConfigCategory.category == category,
                    AcConfigCategory.env == env,
                )
                .first()
            )
            return self._to_category_record(m)

    def list_categories(
        self, *, env: str
    ) -> list[ConfigCategoryRecord]:
        with self._db.orm_session() as db:
            models = (
                db.query(AcConfigCategory)
                .filter(AcConfigCategory.env == env)
                .order_by(AcConfigCategory.id)
                .all()
            )
            return [self._to_category_record(m) for m in models]

    def upsert_category(
        self,
        *,
        category: str,
        category_name: str,
        env: str,
        description: str | None = None,
        operator: str | None = None,
    ) -> int:
        """Atomic upsert on uk_category_env (prod parity)."""
        with self._db.orm_session() as db:
            # get_bind() is the documented session→engine accessor in
            # both SA 1.4 and 2.0 (Session.bind has no 2.0 guarantee).
            # Dialect branch (not mode branch): both arms perform the
            # *same* logical operation — an atomic INSERT-or-update on
            # the ``uk_category_env`` unique key — but SQLAlchemy
            # exposes ON CONFLICT / ON DUPLICATE KEY through two
            # dialect-specific ``insert()`` constructs. There is no
            # core/portable equivalent preserving single-statement
            # atomicity, so we dispatch on ``dialect.name`` here, not
            # on runtime mode. This is the only sanctioned shape of
            # dialect branch under the unified-repo rule.
            #
            # TODO(repo-unify): collapse this branch once a shared
            # ``atomic_upsert`` helper exists in
            # ``plugins/_sql_helpers.py``.
            dialect = db.get_bind().dialect.name
            table = AcConfigCategory.__table__
            values = {
                "category": category,
                "category_name": category_name,
                "description": description,
                "env": env,
                "creator": operator,
                "modifier": operator,
            }
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as _insert

                stmt = _insert(table).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["category", "env"],
                    set_={
                        "category_name": category_name,
                        # COALESCE(VALUES(description), description):
                        # incoming NULL keeps the existing value.
                        # ``table.c.description`` is the unambiguous
                        # Core column ref for the existing row.
                        "description": func.coalesce(
                            stmt.excluded.description,
                            table.c.description,
                        ),
                        "modifier": operator,
                    },
                )
                db.execute(stmt)
                db.flush()
                category_id = (
                    db.query(AcConfigCategory.id)
                    .filter(
                        AcConfigCategory.category == category,
                        AcConfigCategory.env == env,
                    )
                    .scalar()
                )
            else:
                from sqlalchemy.dialects.mysql import insert as _insert

                stmt = _insert(table).values(**values)
                stmt = stmt.on_duplicate_key_update(
                    id=func.LAST_INSERT_ID(AcConfigCategory.id),
                    category_name=category_name,
                    description=func.coalesce(
                        stmt.inserted.description,
                        table.c.description,
                    ),
                    modifier=operator,
                )
                category_id = db.execute(stmt).lastrowid
        logger.info(
            "[upsert_category] category=%s env=%s id=%s",
            category, env, category_id,
        )
        return category_id

    def delete_category(self, *, category_id: int) -> bool:
        """Single blind DELETE; child config items removed by the DB
        FK ON DELETE CASCADE (prod parity — SQLite needs
        PRAGMA foreign_keys=ON, set by the local engine)."""
        with self._db.orm_session() as db:
            rowcount = (
                db.query(AcConfigCategory)
                .filter(AcConfigCategory.id == category_id)
                .delete(synchronize_session=False)
            )
            return rowcount > 0

    # ── config-item ops ─────────────────────────────────────────

    def get_config(
        self, *, config_id: int
    ) -> ConfigItemRecord | None:
        with self._db.orm_session() as db:
            m = (
                db.query(AcConfigItem)
                .filter(AcConfigItem.id == config_id)
                .first()
            )
            return self._to_config_record(m)

    def get_config_by_key(
        self, *, parent_id: int, config_key: str
    ) -> ConfigItemRecord | None:
        with self._db.orm_session() as db:
            m = (
                db.query(AcConfigItem)
                .filter(
                    AcConfigItem.parent_id == parent_id,
                    AcConfigItem.config_key == config_key,
                )
                .first()
            )
            return self._to_config_record(m)

    def upsert_config(
        self,
        *,
        parent_id: int,
        config_key: str,
        config_value: str,
        description: str | None = None,
        operator: str | None = None,
    ) -> int:
        """Atomic upsert on uk_parent_key (prod parity)."""
        with self._db.orm_session() as db:
            # Dialect branch (not mode branch) — see upsert_category.
            # TODO(repo-unify): shared atomic_upsert helper.
            dialect = db.get_bind().dialect.name
            table = AcConfigItem.__table__
            values = {
                "parent_id": parent_id,
                "config_key": config_key,
                "config_value": config_value,
                "description": description,
                "creator": operator,
                "modifier": operator,
            }
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as _insert

                stmt = _insert(table).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["parent_id", "config_key"],
                    set_={
                        "config_value": config_value,
                        "description": func.coalesce(
                            stmt.excluded.description,
                            table.c.description,
                        ),
                        "modifier": operator,
                    },
                )
                db.execute(stmt)
                db.flush()
                config_id = (
                    db.query(AcConfigItem.id)
                    .filter(
                        AcConfigItem.parent_id == parent_id,
                        AcConfigItem.config_key == config_key,
                    )
                    .scalar()
                )
            else:
                from sqlalchemy.dialects.mysql import insert as _insert

                stmt = _insert(table).values(**values)
                stmt = stmt.on_duplicate_key_update(
                    id=func.LAST_INSERT_ID(AcConfigItem.id),
                    config_value=config_value,
                    description=func.coalesce(
                        stmt.inserted.description,
                        table.c.description,
                    ),
                    modifier=operator,
                )
                config_id = db.execute(stmt).lastrowid
        logger.info(
            "[upsert_config] parent_id=%s key=%s id=%s",
            parent_id, config_key, config_id,
        )
        return config_id

    def delete_config(self, *, config_id: int) -> bool:
        """Single blind DELETE returning rowcount > 0 (prod parity)."""
        with self._db.orm_session() as db:
            rowcount = (
                db.query(AcConfigItem)
                .filter(AcConfigItem.id == config_id)
                .delete(synchronize_session=False)
            )
            return rowcount > 0

    def list_configs(
        self, *, parent_id: int
    ) -> list[ConfigItemRecord]:
        with self._db.orm_session() as db:
            models = (
                db.query(AcConfigItem)
                .filter(AcConfigItem.parent_id == parent_id)
                .order_by(AcConfigItem.config_key)
                .all()
            )
            return [self._to_config_record(m) for m in models]

    def list_all_configs(self, *, env: str) -> list[dict]:
        with self._db.orm_session() as db:
            results = (
                db.query(
                    AcConfigItem.id,
                    AcConfigItem.parent_id,
                    AcConfigItem.config_key,
                    AcConfigItem.config_value,
                    AcConfigItem.description,
                    AcConfigItem.creator,
                    AcConfigItem.modifier,
                    AcConfigItem.gmt_create,
                    AcConfigItem.gmt_modified,
                    AcConfigCategory.category,
                    AcConfigCategory.category_name,
                )
                .join(
                    AcConfigCategory,
                    AcConfigItem.parent_id == AcConfigCategory.id,
                )
                .filter(AcConfigCategory.env == env)
                .order_by(AcConfigCategory.id, AcConfigItem.config_key)
                .all()
            )
            return [
                {
                    "id": row[0],
                    "parent_id": row[1],
                    "config_key": row[2],
                    "config_value": self._parse_value(row[3]),
                    "description": row[4],
                    "creator": row[5],
                    "modifier": row[6],
                    "gmt_create": (
                        row[7].isoformat() if row[7] else None
                    ),
                    "gmt_modified": (
                        row[8].isoformat() if row[8] else None
                    ),
                    "category": row[9],
                    "category_name": row[10],
                }
                for row in results
            ]
