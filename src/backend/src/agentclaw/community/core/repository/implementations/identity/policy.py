"""Unified access-control PolicyRepository (prod the relational store + local SQLite).

One ORM implementation behind the ``PolicyRepository`` Protocol
(tables ``ac_access_control_policy`` + ``ac_user_info``, plus reads of
``ac_config_item``/``ac_config_category`` and
``ac_entity_device_binding``). The only per-environment difference is
the injected :class:`DatabasePlugin`: ``orm_session()`` yields a
SQLAlchemy ``Session`` in both runtimes, so this single body runs
unchanged on OceanBase (prod) and SQLite (local).

Prod-twin parity (the raw-SQL ``ZdasPolicyRepository`` is the
reference):

- ``upsert_policy`` / ``upsert_user_info`` are the prod twin's atomic
  ``INSERT ... ON DUPLICATE KEY UPDATE`` on the unique keys
  ``uk_entity_type_id`` / ``uk_user_id_type``. Kept as a **single
  atomic statement with no follow-up re-SELECT** (the Protocol returns
  ``None``) via the same dialect-aware upsert used by
  ``expert_chat_repository.add_chat_bot`` — ``on_conflict_do_update``
  on SQLite, ``on_duplicate_key_update`` on MySQL/OceanBase. The
  branch is purely dialect-correctness, never runtime-mode-driven.
  ``gmt_modified=func.now()`` is set on the update arm
  to match prod's ``ON UPDATE CURRENT_TIMESTAMP`` (a Core upsert does
  not fire the ORM ``onupdate``). The old SQLite twin's
  SELECT-then-write (2 statements, non-atomic under prod AUTOCOMMIT)
  is dropped.
- ``get_config_by_key`` reads the config tables with **no broad
  try/except** — errors propagate, exactly like prod. The old SQLite
  twin swallowed every exception and returned ``None``; that masking
  is dropped.
- All other methods are read-only and return the same
  ``AccessControlPolicyRecord`` / ``UserInfoRecord`` /
  ``ConfigItemRecord`` shapes as the twins.
"""
from __future__ import annotations

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.access.models import (
    AccessControlPolicyRecord,
    ConfigItemRecord,
    UserInfoRecord,
)
from agentclaw.community.core.access.sqlite_models import (
    AccessControlPolicy,
    UserInfo,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.repository.protocols.identity import PolicyRepository

logger = get_logger()


def _policy_to_record(row: AccessControlPolicy) -> AccessControlPolicyRecord:
    return AccessControlPolicyRecord(
        id=row.id,
        entity_id=row.entity_id,
        entity_type=row.entity_type,
        policy=row.policy,
        gmt_create=str(row.gmt_create) if row.gmt_create else None,
    )


def _user_to_record(row: UserInfo) -> UserInfoRecord:
    return UserInfoRecord(
        id=row.id,
        user_id=row.user_id,
        user_type=row.user_type,
        status=row.status,
        gmt_create=str(row.gmt_create) if row.gmt_create else None,
        gmt_modified=str(row.gmt_modified) if row.gmt_modified else None,
    )


class PolicyRepository(
    PolicyRepository,
):
    """Unified ORM ``PolicyRepository`` implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    # ── access-control policy ───────────────────────────────────

    def get_by_entity(
        self, *, entity_id: str, entity_type: str
    ) -> AccessControlPolicyRecord | None:
        with self._db.orm_session() as db:
            row = (
                db.query(AccessControlPolicy)
                .filter(
                    AccessControlPolicy.entity_type == entity_type,
                    AccessControlPolicy.entity_id == entity_id,
                )
                .first()
            )
            return _policy_to_record(row) if row else None

    def upsert_policy(
        self, *, entity_id: str, entity_type: str, policy: str
    ) -> None:
        """Atomic upsert on uk_entity_type_id (prod parity, no
        re-SELECT — returns None)."""
        with self._db.orm_session() as db:
            # Dialect branch (not mode branch): both arms perform the
            # same atomic INSERT-or-update on the ``uk_entity_type_id``
            # unique key; SQLAlchemy exposes ON CONFLICT / ON DUPLICATE
            # KEY through two dialect-specific ``insert()`` constructs
            # with no portable single-statement equivalent. Dispatch on
            # ``dialect.name``, not runtime mode — the only sanctioned
            # dialect branch under the unified-repo rule.
            #
            # TODO(repo-unify): collapse via a shared atomic_upsert
            # helper in ``plugins/_sql_helpers.py``.
            dialect = db.get_bind().dialect.name
            table = AccessControlPolicy.__table__
            values = {
                "entity_id": entity_id,
                "entity_type": entity_type,
                "policy": policy,
            }
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as _insert

                stmt = _insert(table).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["entity_type", "entity_id"],
                    set_={
                        "policy": policy,
                        "gmt_modified": func.now(),
                    },
                )
            else:
                from sqlalchemy.dialects.mysql import insert as _insert

                stmt = _insert(table).values(**values)
                stmt = stmt.on_duplicate_key_update(
                    policy=policy,
                    gmt_modified=func.now(),
                )
            db.execute(stmt)

    # ── config read (errors propagate — prod parity) ────────────

    def get_config_by_key(
        self, *, config_key: str, category: str, env: str
    ) -> ConfigItemRecord | None:
        from agentclaw.community.core.system_config.orm import AcConfigCategory, AcConfigItem

        logger.info(
            "[get_config_by_key] config_key=%s category=%s env=%s",
            config_key, category, env,
        )
        with self._db.orm_session() as db:
            row = (
                db.query(
                    AcConfigItem.config_key, AcConfigItem.config_value
                )
                .filter(
                    AcConfigItem.parent_id.in_(
                        db.query(AcConfigCategory.id).filter(
                            AcConfigCategory.env == env,
                            AcConfigCategory.category == category,
                        )
                    ),
                    AcConfigItem.config_key == config_key,
                )
                .first()
            )
            if row is None:
                return None
            return ConfigItemRecord(
                config_key=row[0], config_value=row[1]
            )

    def count_active_devices(self, *, env: str) -> int:
        from agentclaw.community.core.devices.repository.models import EntityDeviceBinding

        with self._db.orm_session() as db:
            return (
                db.query(EntityDeviceBinding)
                .filter(
                    EntityDeviceBinding.env == env,
                    EntityDeviceBinding.status.in_(
                        ["ACTIVE", "PENDING"]
                    ),
                    EntityDeviceBinding.device_provider == "arca",
                )
                .count()
            )

    # ── user info ───────────────────────────────────────────────

    def get_user_info(
        self, *, user_id: str, user_type: str
    ) -> UserInfoRecord | None:
        with self._db.orm_session() as db:
            row = (
                db.query(UserInfo)
                .filter(
                    UserInfo.user_id == user_id,
                    UserInfo.user_type == user_type,
                )
                .first()
            )
            return _user_to_record(row) if row else None

    def list_users(
        self, *, user_type: str | None = None
    ) -> list[UserInfoRecord]:
        with self._db.orm_session() as db:
            query = db.query(UserInfo)
            if user_type:
                query = query.filter(UserInfo.user_type == user_type)
            rows = query.order_by(UserInfo.gmt_modified.desc()).all()
            return [_user_to_record(r) for r in rows]

    def upsert_user_info(
        self, *, user_id: str, user_type: str, status: str
    ) -> None:
        """Atomic upsert on uk_user_id_type (prod parity, no
        re-SELECT — returns None)."""
        with self._db.orm_session() as db:
            # Dialect branch (not mode branch) — see upsert_policy.
            # TODO(repo-unify): shared atomic_upsert helper.
            dialect = db.get_bind().dialect.name
            table = UserInfo.__table__
            values = {
                "user_id": user_id,
                "user_type": user_type,
                "status": status,
            }
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as _insert

                stmt = _insert(table).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["user_id", "user_type"],
                    set_={
                        "status": status,
                        "gmt_modified": func.now(),
                    },
                )
            else:
                from sqlalchemy.dialects.mysql import insert as _insert

                stmt = _insert(table).values(**values)
                stmt = stmt.on_duplicate_key_update(
                    status=status,
                    gmt_modified=func.now(),
                )
            db.execute(stmt)

    def count_compete_users_after_time(
        self, *, start_time: str
    ) -> int:
        with self._db.orm_session() as db:
            return (
                db.query(UserInfo)
                .filter(
                    UserInfo.user_type == "COMPETE",
                    UserInfo.status == "ACCESS",
                    UserInfo.gmt_modified >= start_time,
                )
                .count()
            )
