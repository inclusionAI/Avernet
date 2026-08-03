"""Unified DeviceBindingRepository (prod ZDAS + local SQLite).

The **last** DB-repository twin pair (S5 — closes the unification
program). One ORM implementation behind the
``DeviceBindingRepository`` Protocol. The only per-environment
difference is the injected :class:`DatabasePlugin`: ``orm_session()``
yields a SQLAlchemy ``Session`` in both runtimes, so this single body
runs unchanged on OceanBase (prod) and SQLite (local), collapsing the
previous raw-cursor/ORM twins so CI exercises the prod path too.

Prod-twin parity (the raw-SQL ``ProdDeviceRepository`` is the
reference):

- ``insert_binding`` is a **plain INSERT** (``db.add`` +
  ``db.flush``); returns ``model.id``. **Never** an upsert despite
  the ``uk_device_id`` unique key — prod itself does not upsert, and
  the codebase relies on ``exists_device_id`` +
  ``get_released_binding`` + ``reuse_binding`` at the service layer
  to handle ``device_id`` conflicts. Treating it as an upsert would
  silently break the reuse flow (the S2 anti-pattern is explicitly
  avoided here).
- ``release_binding`` is a single bulk UPDATE that flips
  ``status='RELEASED'`` and writes ``release_reason``/``released_by``/
  ``released_at=func.now()`` — **soft-delete via status flip, never
  a DELETE**.
- ``update_status`` / ``update_status_and_alive_at`` /
  ``reuse_binding`` / ``batch_update_env`` are single bulk UPDATEs;
  ``gmt_modified=func.now()`` is set DB-side on every values dict
  (S4 pattern; matches prod's ``ON UPDATE CURRENT_TIMESTAMP`` on
  OceanBase, fills the gap on SQLite where ON UPDATE doesn't fire).
- ``update_bot_start_status`` keeps prod's multi-statement shape:
  one SELECT for ``BotModel.ext`` + JSON mutation in Python + one
  bulk UPDATE — **all within one ``orm_session``** to match prod's
  single connection.

Three adopt-prod behavior changes vs the old local twin (flagged for
PR + DDL-parity doc):

1. ``gmt_modified=func.now()`` set DB-side on every UPDATE — in
   this body's values dicts for both ``EntityDeviceBinding`` AND
   the three legacy cross-table ``BotModel`` writes (``update_bot_*``).
   The old local twin stamped a Python ``datetime.utcnow()`` value
   only on ``EntityDeviceBinding``; the old prod twin omitted the
   column from every UPDATE SQL relying on the MySQL
   ``ON UPDATE CURRENT_TIMESTAMP`` clause on both tables. Explicit
   ``func.now()`` makes the column advance on both backends. On
   prod, setting the column explicitly to ``NOW()`` produces the
   same value MySQL's ON UPDATE would set (same instant), so the
   observable post-UPDATE row state is unchanged from the prod
   twin. On SQLite there is no ON UPDATE, so this explicit set is
   required for parity there.
2. ``get_active_engine_by_device_id`` fallback uses
   ``DEFAULT_ENGINE_TYPE`` (the project constant prod imports), not
   the local twin's hardcoded ``"openclaw"`` literal.
3. Legacy cross-table writes to ``ac_bots``
   (``update_bot_start_status`` / ``update_bot_status_on_device_active``
   / ``update_bot_status_on_device_failed``) **propagate exceptions**
   to the caller, matching prod. The old local twin's
   ``"Failed (local mode):"`` warn-and-swallow is dropped.

The Teclaw terminal transition additionally upgrades its fresh ORM Session out
of prod ``AUTOCOMMIT`` before the first SELECT, then explicitly commits or
rolls back the guarded bot + binding transaction. SQLite uses
``BEGIN IMMEDIATE`` because it ignores ``FOR UPDATE``. This path is
intentionally separate from the three legacy adopt-prod helpers above.

All queries that filter by ``bot_id`` / ``owner_id`` / ``device_id`` are env-scoped via
``self._binding_env()`` (EntityDeviceBinding.env) or ``self._bot_env()`` (BotModel.env),
matching the convention used in :class:`agentclaw.community.plugins.bot_repository.BotRepository`.
PK-based lookups (binding_id) intentionally skip env-scope as the auto-increment ID
doesn't collide across envs.

Zero genuine upserts → zero dialect branches in S5.
"""
from __future__ import annotations

import json
from typing import Any

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.devices.repository.protocol import (
    DeviceBindingRepository as _DeviceBindingRepositoryProtocol,
)
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugins.local.sqlite_models import EntityDeviceBinding
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()


class _DeviceBindingStatus:
    """Device binding status enum values (copied from prod twin)."""

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    RELEASED = "RELEASED"


def _normalize_isolation_level(value: str) -> str:
    """Normalize dialect spelling before comparing isolation levels."""
    return " ".join(
        value.replace("_", " ").replace("-", " ").upper().split()
    )


def _to_record(m: EntityDeviceBinding | None) -> DeviceBindingRecord | None:
    """Convert an EntityDeviceBinding ORM row to DeviceBindingRecord
    (matches prod ``_row_to_record`` shape exactly)."""
    if m is None:
        return None
    try:
        props_raw = m.device_props
        device_props = (
            json.loads(props_raw)
            if isinstance(props_raw, str)
            else (props_raw or {})
        )
    except (json.JSONDecodeError, TypeError):
        device_props = {}
    return DeviceBindingRecord(
        id=m.id,
        entity_id=m.entity_id,
        entity_type=m.entity_type,
        device_id=m.device_id,
        device_provider=m.device_provider,
        env=m.env,
        device_props=device_props,
        status=m.status,
        apply_reason=m.apply_reason,
        applied_by=m.applied_by,
        release_reason=m.release_reason,
        released_by=m.released_by,
        released_at=m.released_at,
        last_alive_at=m.last_alive_at,
        gmt_create=m.gmt_create,
        gmt_modified=m.gmt_modified,
    )


class DeviceRepository(_DeviceBindingRepositoryProtocol):
    """Unified ORM ``DeviceBindingRepository`` implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    # ── env-scope helpers (parity with bot_repository._env()) ──

    def _binding_env(self):
        return EntityDeviceBinding.env == get_current_env()

    def _bot_env(self):
        return BotModel.env == get_current_env()

    # ── insert (plain INSERT — never an upsert) ─────────────────

    def insert_binding(
        self,
        *,
        entity_id: str,
        entity_type: str,
        device_id: str,
        device_provider: str,
        env: str,
        device_props: dict[str, Any],
        status: str,
        apply_reason: str | None,
        applied_by: str,
    ) -> int:
        with self._db.orm_session() as db:
            row = EntityDeviceBinding(
                entity_id=entity_id,
                entity_type=entity_type,
                device_id=device_id,
                device_provider=device_provider,
                env=env,
                device_props=json.dumps(
                    device_props, ensure_ascii=False
                ),
                status=status,
                apply_reason=apply_reason,
                applied_by=applied_by,
            )
            db.add(row)
            db.flush()
            return row.id

    # ── reads ───────────────────────────────────────────────────

    def get_by_id(
        self, binding_id: int
    ) -> DeviceBindingRecord | None:
        with self._db.orm_session() as db:
            m = (
                db.query(EntityDeviceBinding)
                .filter(EntityDeviceBinding.id == binding_id)
                .first()
            )
            record = _to_record(m)
            if record:
                logger.info(
                    "[get_by_id] id=%s device_id=%s "
                    "device_provider=%s status=%s",
                    record.id,
                    record.device_id,
                    record.device_provider,
                    record.status,
                )
            else:
                logger.info("[get_by_id] result: None")
            return record

    def get_active_by_bot_and_owner(
        self, bot_id: str, owner_id: str
    ) -> DeviceBindingRecord | None:
        """DeviceContextResolver 入口：按 bot_id + owner_id 拿 bot 当前 binding。

        ``ac_bots`` JOIN ``ac_entity_device_binding`` ON
        ``ac_bots.binding_id = ac_entity_device_binding.id``，过滤
        ``owner_id`` 与 ``is_delete=0``。bot 不存在 / 无 binding /
        owner 不匹配 → 返 None。一次查询拿到 binding 完整对象，
        取代旧链路（``get_device_provider_by_bot_id_and_owner`` 返
        dict 缺 ``binding_id`` → 再二次 ``get_by_id`` 取 record）。
        """
        with self._db.orm_session() as db:
            m = (
                db.query(EntityDeviceBinding)
                .select_from(BotModel)
                .join(
                    EntityDeviceBinding,
                    BotModel.binding_id == EntityDeviceBinding.id,
                )
                .filter(
                    BotModel.bot_id == bot_id,
                    BotModel.owner_id == owner_id,
                    BotModel.is_delete == 0,
                    self._bot_env(),
                )
                .first()
            )
            return _to_record(m)

    def get_by_device_id(
        self, device_id: str
    ) -> DeviceBindingRecord | None:
        with self._db.orm_session() as db:
            m = (
                db.query(EntityDeviceBinding)
                .filter(
                    EntityDeviceBinding.device_id == device_id,
                    self._binding_env(),
                )
                .order_by(EntityDeviceBinding.id.desc())
                .first()
            )
            return _to_record(m)

    def get_by_ids(
        self, binding_ids: list[int]
    ) -> list[DeviceBindingRecord]:
        if not binding_ids:
            return []
        with self._db.orm_session() as db:
            rows = (
                db.query(EntityDeviceBinding)
                .filter(EntityDeviceBinding.id.in_(binding_ids))
                .order_by(EntityDeviceBinding.id.desc())
                .all()
            )
            return [r for r in (_to_record(m) for m in rows) if r]

    def exists_device_id(self, *, device_id: str) -> bool:
        with self._db.orm_session() as db:
            return (
                db.query(EntityDeviceBinding)
                .filter(
                    EntityDeviceBinding.device_id == device_id,
                    self._binding_env(),
                )
                .count()
                > 0
            )

    def get_released_binding(
        self, *, device_id: str
    ) -> DeviceBindingRecord | None:
        with self._db.orm_session() as db:
            m = (
                db.query(EntityDeviceBinding)
                .filter(
                    EntityDeviceBinding.device_id == device_id,
                    EntityDeviceBinding.status
                    == _DeviceBindingStatus.RELEASED,
                    self._binding_env(),
                )
                .order_by(EntityDeviceBinding.id.desc())
                .first()
            )
            return _to_record(m)

    def list_bindings(
        self,
        *,
        env: str,
        entity_id: str | None = None,
        entity_type: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[DeviceBindingRecord]]:
        with self._db.orm_session() as db:
            query = db.query(EntityDeviceBinding).filter(
                EntityDeviceBinding.env == env
            )
            if entity_id is not None:
                query = query.filter(
                    EntityDeviceBinding.entity_id == entity_id
                )
            if entity_type is not None:
                query = query.filter(
                    EntityDeviceBinding.entity_type == entity_type
                )
            if status is not None:
                query = query.filter(
                    EntityDeviceBinding.status == status
                )
            total = query.count()
            rows = (
                query.order_by(EntityDeviceBinding.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            items = [
                r for r in (_to_record(m) for m in rows) if r
            ]
            return total, items

    def list_active_caller_instance_bindings(
        self,
        *,
        bot_id: str,
        owner_id: str,
        env: str,
    ) -> list[DeviceBindingRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(EntityDeviceBinding)
                .filter(
                    EntityDeviceBinding.env == env,
                    EntityDeviceBinding.entity_id == owner_id,
                    EntityDeviceBinding.entity_type == "staff",
                    EntityDeviceBinding.status == _DeviceBindingStatus.ACTIVE,
                    EntityDeviceBinding.device_provider == "baas",
                    EntityDeviceBinding.apply_reason
                    == f"caller_instance:{bot_id}",
                )
                .order_by(EntityDeviceBinding.id.desc())
                .all()
            )

            # ``device_id`` is unique in prod, but keep this defensive de-dupe
            # for legacy/local data and retain the latest binding deterministically.
            seen_device_ids: set[str] = set()
            bindings: list[DeviceBindingRecord] = []
            for row in rows:
                record = _to_record(row)
                if record is None or record.device_id in seen_device_ids:
                    continue
                seen_device_ids.add(record.device_id)
                bindings.append(record)
            return bindings

    def count_non_released_bindings(
        self,
        *,
        entity_id: str,
        entity_type: str,
        env: str,
    ) -> int:
        with self._db.orm_session() as db:
            return (
                db.query(EntityDeviceBinding)
                .filter(
                    EntityDeviceBinding.entity_id == entity_id,
                    EntityDeviceBinding.entity_type == entity_type,
                    EntityDeviceBinding.env == env,
                    EntityDeviceBinding.status
                    != _DeviceBindingStatus.RELEASED,
                )
                .count()
            )

    # ── updates (single bulk statements; gmt_modified DB-side) ──

    def release_binding(
        self,
        *,
        binding_id: int,
        release_reason: str | None,
        released_by: str,
    ) -> None:
        # Soft-delete via status flip — NEVER a DELETE. Single
        # conditional bulk UPDATE matches prod's raw UPDATE SQL.
        with self._db.orm_session() as db:
            db.query(EntityDeviceBinding).filter(
                EntityDeviceBinding.id == binding_id
            ).update(
                {
                    EntityDeviceBinding.status: _DeviceBindingStatus.RELEASED,
                    EntityDeviceBinding.release_reason: release_reason,
                    EntityDeviceBinding.released_by: released_by,
                    EntityDeviceBinding.released_at: func.now(),
                    EntityDeviceBinding.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )

    def update_status(
        self, *, binding_id: int, status: str
    ) -> None:
        with self._db.orm_session() as db:
            db.query(EntityDeviceBinding).filter(
                EntityDeviceBinding.id == binding_id
            ).update(
                {
                    EntityDeviceBinding.status: status,
                    EntityDeviceBinding.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )

    def update_status_and_alive_at(
        self, *, binding_id: int, status: str
    ) -> None:
        with self._db.orm_session() as db:
            db.query(EntityDeviceBinding).filter(
                EntityDeviceBinding.id == binding_id
            ).update(
                {
                    EntityDeviceBinding.status: status,
                    EntityDeviceBinding.last_alive_at: func.now(),
                    EntityDeviceBinding.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )

    def update_device_props(
        self, *, binding_id: int, props: dict[str, Any]
    ) -> None:
        # Read-merge-write so other device_props keys (callback_token,
        # sandbox_id, adapter_port, ...) survive. Binding gone → silent no-op.
        with self._db.orm_session() as db:
            row = (
                db.query(EntityDeviceBinding.device_props)
                .filter(EntityDeviceBinding.id == binding_id)
                .first()
            )
            if row is None:
                return
            try:
                current = json.loads(row[0]) if row[0] else {}
                if not isinstance(current, dict):
                    current = {}
            except (TypeError, ValueError):
                current = {}
            current.update(props)
            db.query(EntityDeviceBinding).filter(
                EntityDeviceBinding.id == binding_id
            ).update(
                {
                    EntityDeviceBinding.device_props: json.dumps(
                        current, ensure_ascii=False
                    ),
                    EntityDeviceBinding.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )

    def transition_teclaw_publish_terminal(
        self,
        *,
        binding_id: int,
        bot_id: str,
        owner_id: str,
        publish_id: int,
        status: str,
    ) -> bool:
        """Guard and persist a Teclaw terminal result in one transaction."""
        with self._db.orm_session() as db:
            try:
                if db.in_transaction():
                    raise RuntimeError(
                        "Teclaw terminal transition requires a fresh ORM "
                        "Session before selecting transaction isolation"
                    )
                dialect_name = db.get_bind().dialect.name
                isolation_level = (
                    "SERIALIZABLE"
                    if dialect_name == "sqlite"
                    else "READ COMMITTED"
                )
                connection = db.connection(
                    execution_options={"isolation_level": isolation_level}
                )
                actual_isolation = _normalize_isolation_level(
                    connection.get_isolation_level()
                )
                expected_isolation = _normalize_isolation_level(
                    isolation_level
                )
                if actual_isolation != expected_isolation:
                    db.rollback()
                    raise RuntimeError(
                        "Teclaw terminal transaction isolation mismatch: "
                        f"expected {expected_isolation}, got "
                        f"{actual_isolation}"
                    )
                if dialect_name == "sqlite":
                    connection.exec_driver_sql("BEGIN IMMEDIATE")

                binding = (
                    db.query(EntityDeviceBinding)
                    .filter(EntityDeviceBinding.id == binding_id)
                    .with_for_update()
                    .one_or_none()
                )
                if binding is None:
                    db.rollback()
                    return False
                try:
                    props = (
                        json.loads(binding.device_props)
                        if isinstance(binding.device_props, str)
                        else (binding.device_props or {})
                    )
                except (json.JSONDecodeError, TypeError):
                    props = {}
                current_publish_id = (
                    props.get("publish_id")
                    if isinstance(props, dict)
                    else None
                )
                if (
                    binding.status != _DeviceBindingStatus.PENDING
                    or binding.device_provider != "teclaw"
                    or current_publish_id is None
                    or str(current_publish_id) != str(publish_id)
                ):
                    db.rollback()
                    return False

                bot_rowcount = (
                    db.query(BotModel)
                    .filter(
                        BotModel.bot_id == bot_id,
                        BotModel.owner_id == owner_id,
                        BotModel.binding_id == binding_id,
                        BotModel.is_delete == 0,
                        self._bot_env(),
                    )
                    .update(
                        {
                            BotModel.status: status,
                            BotModel.gmt_modified: func.now(),
                        },
                        synchronize_session=False,
                    )
                )
                if bot_rowcount != 1:
                    raise RuntimeError(
                        "Teclaw terminal bot status update expected exactly "
                        f"one record, matched {bot_rowcount}"
                    )

                binding.status = status
                binding.gmt_modified = func.now()
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise

    def reuse_binding(
        self,
        *,
        binding_id: int,
        device_props: dict[str, Any],
        apply_reason: str | None,
        applied_by: str,
        status: str = "PENDING",
    ) -> None:
        # Service-layer reuse path for a previously-released
        # device_id. Single bulk UPDATE by PK; NULLs out release-
        # metadata + last_alive_at. Prod parity:
        # prod/zdas_device_repo.py:355-387.
        with self._db.orm_session() as db:
            db.query(EntityDeviceBinding).filter(
                EntityDeviceBinding.id == binding_id
            ).update(
                {
                    EntityDeviceBinding.status: status,
                    EntityDeviceBinding.device_props: json.dumps(
                        device_props, ensure_ascii=False
                    ),
                    EntityDeviceBinding.apply_reason: apply_reason,
                    EntityDeviceBinding.applied_by: applied_by,
                    EntityDeviceBinding.release_reason: None,
                    EntityDeviceBinding.released_by: None,
                    EntityDeviceBinding.released_at: None,
                    EntityDeviceBinding.last_alive_at: None,
                    EntityDeviceBinding.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )

    def batch_update_env(
        self, *, binding_ids: list[int], env: str
    ) -> int:
        if not binding_ids:
            return 0
        with self._db.orm_session() as db:
            return (
                db.query(EntityDeviceBinding)
                .filter(EntityDeviceBinding.id.in_(binding_ids))
                .update(
                    {
                        EntityDeviceBinding.env: env,
                        EntityDeviceBinding.gmt_modified: func.now(),
                    },
                    synchronize_session=False,
                )
            )

    # ── cross-table read against ac_bots ────────────────────────

    def get_active_engine_by_device_id(self, *, device_id: str) -> str:
        """Look up ``ac_bots.active_engine`` by device_id.

        Falls back to ``DEFAULT_ENGINE_TYPE`` on missing row or any
        exception — adopt prod (the old local twin hardcoded
        ``"openclaw"``; the constant is the source of truth).
        """
        try:
            with self._db.orm_session() as db:
                value = (
                    db.query(BotModel.active_engine)
                    .filter(
                        BotModel.device_id == device_id,
                        self._bot_env(),
                    )
                    .limit(1)
                    .scalar()
                )
                return value if value else DEFAULT_ENGINE_TYPE
        except Exception as e:  # noqa: BLE001 - prod parity
            logger.error(
                "[get_active_engine_by_device_id] device_id=%s "
                "error=%s using DEFAULT_ENGINE_TYPE=%s",
                device_id,
                e,
                DEFAULT_ENGINE_TYPE,
            )
            return DEFAULT_ENGINE_TYPE

    # ── cross-table writes against ac_bots (propagate errors) ──

    def update_bot_start_status(
        self,
        *,
        binding_id: int,
        status: str,
        message: str | None,
    ) -> None:
        """SELECT BotModel ext → mutate JSON in Python → UPDATE,
        within one orm_session. **Exceptions propagate** — adopt
        prod (drop the local twin's warn-and-swallow)."""
        with self._db.orm_session() as db:
            row = (
                db.query(BotModel.id, BotModel.ext)
                .filter(
                    BotModel.binding_id == binding_id,
                    self._bot_env(),
                )
                .limit(1)
                .first()
            )
            if row is None:
                logger.info(
                    "[update_bot_start_status] No bot found for "
                    "binding_id=%s, skipping",
                    binding_id,
                )
                return
            bot_id, ext_raw = row
            try:
                ext = json.loads(ext_raw) if ext_raw else {}
            except (json.JSONDecodeError, TypeError):
                ext = {}
            ext["start_status"] = status
            if message is not None:
                ext["start_message"] = message
            db.query(BotModel).filter(BotModel.id == bot_id).update(
                {
                    BotModel.ext: json.dumps(
                        ext, ensure_ascii=False
                    ),
                    BotModel.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )

    def update_bot_status_on_device_active(
        self, *, binding_id: int
    ) -> None:
        """Flip BotModel.status PENDING → ACTIVE conditionally.
        Single bulk UPDATE; exceptions propagate (prod parity)."""
        with self._db.orm_session() as db:
            db.query(BotModel).filter(
                BotModel.binding_id == binding_id,
                BotModel.status == "PENDING",
                self._bot_env(),
            ).update(
                {
                    BotModel.status: "ACTIVE",
                    BotModel.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )

    def update_bot_status_on_device_failed(
        self, *, binding_id: int
    ) -> None:
        """Set BotModel.status='FAILED' unconditionally for the
        binding (matches prod — no status guard). Single bulk
        UPDATE; exceptions propagate."""
        with self._db.orm_session() as db:
            db.query(BotModel).filter(
                BotModel.binding_id == binding_id,
                self._bot_env(),
            ).update(
                {
                    BotModel.status: "FAILED",
                    BotModel.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )
