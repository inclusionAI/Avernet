"""ORM-backed device binding repository implementation.

Uses @with_orm_session decorator for SQLAlchemy ORM Session lifecycle.
Cross-table JOIN / JSON-heavy methods use session.execute(text(...))
to preserve readability while staying on the ORM session connection.
"""

import json
from typing import Any

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.core.repository.device import DeviceModel
from secbaas.community.logger import get_logger

from ._orm_model import DeviceBindingModel
from ._protocol import DeviceBindingRepository
from ._record import DeviceBindingRecord, DeviceBindingStatus

log = get_logger("orm-repository")


class OrmDeviceBindingRepository(OrmConnectionMixin, DeviceBindingRepository):
    """ORM-based device binding repository for ac_entity_device_binding table.

    Cross-table / JSON-heavy methods use session.execute(text(...)) on the
    ORM-managed connection — same session lifecycle, raw SQL for readability.
    """

    def __init__(self, database) -> None:
        self._database = database

    @staticmethod
    def _model_to_record(row: DeviceBindingModel | None) -> DeviceBindingRecord | None:
        if row is None:
            return None
        try:
            dps = (
                json.loads(row.device_props)
                if isinstance(row.device_props, str)
                else row.device_props
            )
        except (json.JSONDecodeError, TypeError):
            dps = {}
        return DeviceBindingRecord(
            id=row.id,
            entity_id=row.entity_id,
            entity_type=row.entity_type,
            device_id=row.device_id,
            device_provider=row.device_provider,
            env=row.env,
            device_props=dps or {},
            status=row.status,
            apply_reason=row.apply_reason,
            applied_by=row.applied_by,
            release_reason=row.release_reason,
            released_by=row.released_by,
            released_at=row.released_at,
            last_alive_at=row.last_alive_at,
            gmt_create=row.gmt_create,
            gmt_modified=row.gmt_modified,
        )

    @with_orm_session
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
        log.info(
            "insert_binding: entity_id=%s, entity_type=%s, device_id=%s, env=%s",
            entity_id,
            entity_type,
            device_id,
            env,
        )
        row = DeviceBindingModel(
            entity_id=entity_id,
            entity_type=entity_type,
            device_id=device_id,
            device_provider=device_provider,
            env=env,
            device_props=json.dumps(device_props, ensure_ascii=False),
            status=status,
            apply_reason=apply_reason,
            applied_by=applied_by,
        )
        self._session.add(row)
        self._session.flush()
        result = int(row.id)
        log.info("[device-binding:insert_binding] result: id=%s", result)
        return result

    @with_orm_session
    def get_by_id(self, binding_id: int) -> DeviceBindingRecord | None:
        log.info("get_by_id: binding_id=%s", binding_id)
        row = (
            self._session.query(DeviceBindingModel)
            .filter(DeviceBindingModel.id == binding_id)
            .first()
        )
        result = self._model_to_record(row)
        log.info(
            "[device-binding:get_by_id] result: %s", result.id if result else "None"
        )
        return result

    @with_orm_session
    def get_by_device_id(self, device_id: str) -> DeviceBindingRecord | None:
        log.info("get_by_device_id: device_id=%s", device_id)
        row = (
            self._session.query(DeviceBindingModel)
            .filter(DeviceBindingModel.device_id == device_id)
            .order_by(DeviceBindingModel.id.desc())
            .first()
        )
        result = self._model_to_record(row)
        log.info(
            "[device-binding:get_by_device_id] result: %s",
            result.id if result else "None",
        )
        return result

    @with_orm_session
    def release_binding(
        self,
        *,
        binding_id: int,
        release_reason: str | None,
        released_by: str,
    ) -> None:
        log.info("release_binding: binding_id=%s", binding_id)
        from sqlalchemy import func

        self._session.query(DeviceBindingModel).filter(
            DeviceBindingModel.id == binding_id
        ).update(
            {
                "status": DeviceBindingStatus.RELEASED.value,
                "release_reason": release_reason,
                "released_by": released_by,
                "released_at": func.now(),
            },
            synchronize_session=False,
        )
        log.info("[device-binding:release_binding] result: done")

    @with_orm_session
    def update_status(self, *, binding_id: int, status: str) -> None:
        log.info("update_status: binding_id=%s, status=%s", binding_id, status)
        self._session.query(DeviceBindingModel).filter(
            DeviceBindingModel.id == binding_id
        ).update({"status": status}, synchronize_session=False)
        log.info("[device-binding:update_status] result: done")

    @with_orm_session
    def update_status_and_alive_at(self, *, binding_id: int, status: str) -> None:
        log.info(
            "update_status_and_alive_at: binding_id=%s, status=%s", binding_id, status
        )
        from sqlalchemy import func

        self._session.query(DeviceBindingModel).filter(
            DeviceBindingModel.id == binding_id
        ).update(
            {"status": status, "last_alive_at": func.now()},
            synchronize_session=False,
        )
        log.info("[device-binding:update_status_and_alive_at] result: done")

    @with_orm_session
    def list_bindings(
        self,
        *,
        entity_id: str | None = None,
        entity_type: str | None = None,
        device_provider: str | None = None,
        env: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[DeviceBindingRecord]]:
        log.info(
            "list_bindings: entity_id=%s, entity_type=%s, env=%s, status=%s, page=%s",
            entity_id,
            entity_type,
            env,
            status,
            page,
        )
        from sqlalchemy import func

        q = self._session.query(DeviceBindingModel)
        if entity_id is not None:
            q = q.filter(DeviceBindingModel.entity_id == entity_id)
        if entity_type is not None:
            q = q.filter(DeviceBindingModel.entity_type == entity_type)
        if device_provider is not None:
            q = q.filter(DeviceBindingModel.device_provider == device_provider)
        if env is not None:
            q = q.filter(DeviceBindingModel.env == env)
        if status is not None:
            q = q.filter(DeviceBindingModel.status == status)

        total = q.with_entities(func.count(DeviceBindingModel.id)).scalar() or 0
        offset = (page - 1) * page_size
        rows = (
            q.order_by(DeviceBindingModel.id.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )
        items = [self._model_to_record(r) for r in rows]
        log.info("[device-binding:list_bindings] result: %s rows", len(items))
        return total, items

    @with_orm_session
    def list_bindings_by_providers(
        self,
        *,
        providers: list[str],
        env: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[DeviceBindingRecord]]:
        """按多个设备提供商查询绑定记录。

        Args:
            providers: 设备提供商列表，如 ["arca", "baas"]
            env: 环境筛选
            status: 状态筛选
            page: 页码
            page_size: 每页数量
        """
        log.info(
            "list_bindings_by_providers: providers=%s, env=%s, status=%s, page=%s",
            providers,
            env,
            status,
            page,
        )
        if not providers:
            return 0, []
        from sqlalchemy import func

        q = self._session.query(DeviceBindingModel)
        q = q.filter(DeviceBindingModel.device_provider.in_(providers))
        if env is not None:
            q = q.filter(DeviceBindingModel.env == env)
        if status is not None:
            q = q.filter(DeviceBindingModel.status == status)

        total = q.with_entities(func.count(DeviceBindingModel.id)).scalar() or 0
        offset = (page - 1) * page_size
        rows = (
            q.order_by(DeviceBindingModel.id.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )
        items = [self._model_to_record(r) for r in rows]
        log.info(
            "[device-binding:list_bindings_by_providers] result: %s rows", len(items)
        )
        return total, items

    @with_orm_session
    def count_non_released_bindings(
        self,
        *,
        entity_id: str,
        entity_type: str,
        env: str,
    ) -> int:
        log.info(
            "count_non_released_bindings: entity_id=%s, entity_type=%s, env=%s",
            entity_id,
            entity_type,
            env,
        )
        from sqlalchemy import func

        result = (
            self._session.query(func.count(DeviceBindingModel.id))
            .filter(
                DeviceBindingModel.entity_id == entity_id,
                DeviceBindingModel.entity_type == entity_type,
                DeviceBindingModel.env == env,
                DeviceBindingModel.status != DeviceBindingStatus.RELEASED.value,
            )
            .scalar()
        ) or 0
        log.info("[device-binding:count_non_released_bindings] result: %s", result)
        return result

    @with_orm_session
    def exists_device_id(self, *, device_id: str) -> bool:
        log.info("exists_device_id: device_id=%s", device_id)
        from sqlalchemy import func

        count = (
            self._session.query(func.count(DeviceBindingModel.id))
            .filter(DeviceBindingModel.device_id == device_id)
            .scalar()
        )
        result = (count or 0) > 0
        log.info("[device-binding:exists_device_id] result: %s", result)
        return result

    @with_orm_session
    def get_released_binding(self, *, device_id: str) -> DeviceBindingRecord | None:
        log.info("get_released_binding: device_id=%s", device_id)
        row = (
            self._session.query(DeviceBindingModel)
            .filter(
                DeviceBindingModel.device_id == device_id,
                DeviceBindingModel.status == DeviceBindingStatus.RELEASED.value,
            )
            .order_by(DeviceBindingModel.id.desc())
            .first()
        )
        result = self._model_to_record(row)
        log.info(
            "[device-binding:get_released_binding] result: %s",
            result.id if result else "None",
        )
        return result

    @with_orm_session
    def reuse_binding(
        self,
        *,
        binding_id: int,
        device_props: dict[str, Any],
        apply_reason: str | None,
        applied_by: str,
        status: str = "PENDING",
    ) -> None:
        log.info("reuse_binding: binding_id=%s, status=%s", binding_id, status)
        self._session.query(DeviceBindingModel).filter(
            DeviceBindingModel.id == binding_id
        ).update(
            {
                "status": status,
                "device_props": json.dumps(device_props, ensure_ascii=False),
                "apply_reason": apply_reason,
                "applied_by": applied_by,
                "release_reason": None,
                "released_by": None,
                "released_at": None,
                "last_alive_at": None,
            },
            synchronize_session=False,
        )
        log.info("[device-binding:reuse_binding] result: done")

    @with_orm_session
    def delete_binding(self, binding_id: int) -> bool:
        log.info("delete_binding: binding_id=%s", binding_id)
        result = (
            self._session.query(DeviceBindingModel)
            .filter(DeviceBindingModel.id == binding_id)
            .delete(synchronize_session=False)
        )
        result = int(result) > 0
        log.info("[device-binding:delete_binding] result: %s", result)
        return result

    @with_orm_session
    def exists(self, binding_id: int) -> bool:
        log.info("exists: binding_id=%s", binding_id)
        from sqlalchemy import func

        count = (
            self._session.query(func.count(DeviceBindingModel.id))
            .filter(DeviceBindingModel.id == binding_id)
            .scalar()
        )
        result = (count or 0) > 0
        log.info("[device-binding:exists] result: %s", result)
        return result

    @with_orm_session
    def list_bindings_by_ttl_asc(
        self,
        *,
        limit: int = 100,
    ) -> list[DeviceBindingRecord]:
        """查询 ac_entity_device_binding 中 ACTIVE 且有 sandbox_id 的记录，
        按 TTL 过期时间 ASC 排序，取前 limit 条。

        用于 DeviceTtlTimer 定时任务：优先处理即将过期的个人 bot 设备。
        """

        rows = (
            self._session.query(DeviceBindingModel)
            .filter(
                DeviceBindingModel.status == "ACTIVE",
                DeviceBindingModel.device_props.op("->>")("$.sandbox_id").isnot(None),
            )
            .order_by(
                DeviceBindingModel.device_props.op("->>")("$.ttl_expiration_time").asc()
            )
            .limit(limit)
            .all()
        )
        items = [self._model_to_record(r) for r in rows if r is not None]
        log.info(
            "[device-binding:list_bindings_by_ttl_asc] result: %s rows",
            len(items),
        )
        return items

    @with_orm_session
    def update_device_props_ttl(
        self,
        *,
        binding_id: int,
        ttl_expiration_timestamp: int,
        ttl_expiration_time: str,
        refresh_fail_count: int = 0,
    ) -> None:
        log.info(
            "update_device_props_ttl: binding_id=%s, ttl_expiration_timestamp=%s",
            binding_id,
            ttl_expiration_timestamp,
        )
        from sqlalchemy import func, text

        self._session.query(DeviceBindingModel).filter(
            DeviceBindingModel.id == binding_id
        ).update(
            {
                "device_props": func.json_set(
                    DeviceBindingModel.device_props,
                    text("'$.ttl_expiration_timestamp'"),
                    ttl_expiration_timestamp,
                    text("'$.ttl_expiration_time'"),
                    ttl_expiration_time,
                    text("'$.refresh_fail_count'"),
                    refresh_fail_count,
                ),
            },
            synchronize_session=False,
        )
        log.info("[device-binding:update_device_props_ttl] result: done")

    @with_orm_session
    def get_binding_by_sandbox_id(
        self, *, sandbox_id: str
    ) -> DeviceBindingRecord | None:
        log.info("get_binding_by_sandbox_id: sandbox_id=%s", sandbox_id)
        from sqlalchemy import func, text

        row = (
            self._session.query(DeviceBindingModel)
            .filter(
                DeviceBindingModel.status == "ACTIVE",
                DeviceBindingModel.device_props.isnot(None),
                func.json_extract(
                    DeviceBindingModel.device_props, text("'$.sandbox_id'")
                )
                == sandbox_id,
            )
            .first()
        )
        result = self._model_to_record(row)
        log.info(
            "[device-binding:get_binding_by_sandbox_id] result: %s",
            result.id if result else "None",
        )
        return result

    @with_orm_session
    def get_binding_by_sandbox_id_like(
        self, *, sandbox_id_prefix: str
    ) -> DeviceBindingRecord | None:
        log.info(
            "get_binding_by_sandbox_id_like: sandbox_id_prefix=%s", sandbox_id_prefix
        )
        from sqlalchemy import String, func, text

        row = (
            self._session.query(DeviceBindingModel)
            .filter(
                DeviceBindingModel.device_props.isnot(None),
                func.json_extract(
                    DeviceBindingModel.device_props, text("'$.sandbox_id'")
                )
                .cast(String)
                .like(f"%{sandbox_id_prefix}%"),
            )
            .order_by(DeviceBindingModel.id.desc())
            .first()
        )
        result = self._model_to_record(row)
        log.info(
            "[device-binding:get_binding_by_sandbox_id_like] result: %s",
            result.id if result else "None",
        )
        return result

    @with_orm_session
    def list_by_device_id(
        self,
        *,
        device_id: str,
        status: str = "ACTIVE",
        env: str | None = None,
    ) -> list[DeviceBindingRecord]:
        log.info(
            "list_by_device_id: device_id=%s, status=%s, env=%s", device_id, status, env
        )
        q = self._session.query(DeviceBindingModel).filter(
            DeviceBindingModel.device_id == device_id,
            DeviceBindingModel.status == status,
        )
        if env is not None:
            q = q.filter(DeviceBindingModel.env == env)
        rows = q.order_by(DeviceBindingModel.id.desc()).all()
        items = [self._model_to_record(r) for r in rows]
        log.info("[device-binding:list_by_device_id] result: %s rows", len(items))
        return items

    @with_orm_session
    def export_device_all(self) -> list[tuple[str, str, str]]:
        log.info("export_device_all")
        rows = (
            self._session.query(
                DeviceBindingModel.entity_id,
                DeviceBindingModel.device_props,
            )
            .filter(
                DeviceBindingModel.status == "ACTIVE",
                DeviceBindingModel.device_provider == "arca",
            )
            .order_by(DeviceBindingModel.gmt_create.desc())
            .all()
        )
        result: list[tuple[str, str, str]] = []
        for entity_id, device_props in rows:
            try:
                dps = (
                    json.loads(device_props)
                    if isinstance(device_props, str)
                    else (device_props or {})
                )
            except (json.JSONDecodeError, TypeError):
                dps = {}
            bolt_id = dps.get("bolt_id", "")
            sandbox_id = dps.get("sandbox_id", "")
            result.append((entity_id, bolt_id, sandbox_id))
        log.info("[device-binding:export_device_all] result: %s rows", len(result))
        return result

    @with_orm_session
    def export_device_list(self, *, env: str = "pre") -> list[tuple[str, str, str]]:
        log.info("export_device_list: env=%s", env)
        rows = (
            self._session.query(
                DeviceBindingModel.entity_id,
                DeviceBindingModel.device_props,
            )
            .filter(
                DeviceBindingModel.status == "ACTIVE",
                DeviceBindingModel.device_provider == "arca",
                DeviceBindingModel.env == env,
            )
            .order_by(DeviceBindingModel.gmt_create.desc())
            .all()
        )
        result: list[tuple[str, str, str]] = []
        for entity_id, device_props in rows:
            try:
                dps = (
                    json.loads(device_props)
                    if isinstance(device_props, str)
                    else (device_props or {})
                )
            except (json.JSONDecodeError, TypeError):
                dps = {}
            bolt_id = dps.get("bolt_id", "")
            sandbox_id = dps.get("sandbox_id", "")
            result.append((entity_id, bolt_id, sandbox_id))
        log.info("[device-binding:export_device_list] result: %s rows", len(result))
        return result

    @with_orm_session
    def update_device_props_ttl_by_paas_device_id(
        self,
        *,
        paas_device_id: str,
        ttl_expiration_timestamp: int,
        ttl_expiration_time: str,
    ) -> None:
        log.info(
            "update_device_props_ttl_by_paas_device_id: paas_device_id=%s",
            paas_device_id,
        )
        from sqlalchemy import func, text

        self._session.query(DeviceBindingModel).filter(
            func.json_extract(DeviceBindingModel.device_props, text("'$.sandbox_id'"))
            == paas_device_id,
        ).update(
            {
                "device_props": func.json_set(
                    DeviceBindingModel.device_props,
                    text("'$.ttl_expiration_timestamp'"),
                    ttl_expiration_timestamp,
                    text("'$.ttl_expiration_time'"),
                    ttl_expiration_time,
                ),
            },
            synchronize_session=False,
        )
        log.info(
            "[device-binding:update_device_props_ttl_by_paas_device_id] result: done"
        )

    @with_orm_session
    def update_device_props_refresh_fail_count(
        self,
        *,
        binding_id: int,
        refresh_fail_count: int,
    ) -> None:
        log.info(
            "update_device_props_refresh_fail_count: binding_id=%s, refresh_fail_count=%s",
            binding_id,
            refresh_fail_count,
        )
        from sqlalchemy import func, text

        self._session.query(DeviceBindingModel).filter(
            DeviceBindingModel.id == binding_id
        ).update(
            {
                "device_props": func.json_set(
                    DeviceBindingModel.device_props,
                    text("'$.refresh_fail_count'"),
                    refresh_fail_count,
                ),
            },
            synchronize_session=False,
        )
        log.info("[device-binding:update_device_props_refresh_fail_count] result: done")

    # ── Cross-table / JSON-heavy methods (text SQL on ORM session) ──

    @with_orm_session
    def list_active_sandboxes_with_bot(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        env: str | None = None,
        device_provider: str | None = None,
        sort_by: str = "id",
        sort_order: str = "desc",
    ) -> tuple[int, list[tuple[DeviceBindingRecord, dict[str, Any]]]]:
        valid_sort_fields = {"id", "gmt_create"}
        if sort_by not in valid_sort_fields:
            sort_by = "id"
        sort_order_sql = "DESC" if sort_order.lower() == "desc" else "ASC"

        where_clauses = [
            "d.status = 'ACTIVE'",
            "b.is_delete = 0",
            "d.device_props IS NOT NULL",
        ]
        params: list[Any] = []
        if env:
            where_clauses.append("d.env = :env")
            params.append({"name": "env", "value": env})
        if device_provider:
            where_clauses.append("d.device_provider = :device_provider")
            params.append({"name": "device_provider", "value": device_provider})

        where_sql = " AND ".join(where_clauses)

        count_sql = f"""
        SELECT COUNT(*)
        FROM ac_entity_device_binding d
        INNER JOIN ac_bots b ON d.device_id = b.device_id
        WHERE {where_sql}
        """
        param_dict = {p["name"]: p["value"] for p in params}

        from sqlalchemy import text

        total = self._session.execute(text(count_sql), param_dict).scalar() or 0

        offset = (page - 1) * page_size
        data_sql = f"""
        SELECT
            d.id, d.entity_id, d.entity_type, d.device_id, d.device_provider, d.env, d.device_props,
            d.status, d.apply_reason, d.applied_by,
            d.release_reason, d.released_by, d.released_at, d.last_alive_at,
            d.gmt_create, d.gmt_modified,
            b.bot_id, b.bot_name, b.status AS bot_status, b.active_engine,
            b.gmt_create AS bot_gmt_create, b.gmt_modified AS bot_gmt_modified
        FROM ac_entity_device_binding d
        INNER JOIN ac_bots b ON d.device_id = b.device_id
        WHERE {where_sql}
        ORDER BY d.{sort_by} {sort_order_sql}
        LIMIT :limit OFFSET :offset
        """
        param_dict["limit"] = page_size
        param_dict["offset"] = offset

        rows = self._session.execute(text(data_sql), param_dict).fetchall()

        items: list[tuple[DeviceBindingRecord, dict[str, Any]]] = []
        for row in rows:
            device_props_json = row[6]
            try:
                device_props = (
                    json.loads(device_props_json)
                    if isinstance(device_props_json, str)
                    else device_props_json
                )
            except (json.JSONDecodeError, TypeError):
                device_props = {}

            record = DeviceBindingRecord(
                id=row[0],
                entity_id=row[1],
                entity_type=row[2],
                device_id=row[3],
                device_provider=row[4],
                env=row[5],
                device_props=device_props,
                status=row[7],
                apply_reason=row[8],
                applied_by=row[9],
                release_reason=row[10],
                released_by=row[11],
                released_at=row[12],
                last_alive_at=row[13],
                gmt_create=row[14],
                gmt_modified=row[15],
            )
            bot_info = {
                "bot_id": row[16],
                "bot_name": row[17],
                "bot_status": row[18],
                "active_engine": row[19],
                "bot_created_at": row[20],
                "bot_modified_at": row[21],
            }
            items.append((record, bot_info))

        log.info(
            "[device-binding:list_active_sandboxes_with_bot] returned %s items, total=%s",
            len(items),
            total,
        )
        return total, items

    @with_orm_session
    def list_sandboxes_by_bot(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str | None = None,
    ) -> tuple[dict[str, Any] | None, list[DeviceBindingRecord]]:
        from sqlalchemy import text

        bot_sql = """
        SELECT DISTINCT bot_id, bot_name, status, active_engine,
               entity_id, entity_type, device_id,
               gmt_create, gmt_modified
        FROM ac_bots
        WHERE bot_id = :bot_id AND entity_id = :entity_id AND is_delete = 0
        LIMIT 1
        """
        bot_row = self._session.execute(
            text(bot_sql), {"bot_id": bot_id, "entity_id": entity_id}
        ).fetchone()

        if not bot_row:
            log.info(
                "[device-binding:list_sandboxes_by_bot] Bot not found: bot_id=%s, entity_id=%s",
                bot_id,
                entity_id,
            )
            return None, []

        bot_info = {
            "bot_id": bot_row[0],
            "bot_name": bot_row[1],
            "bot_status": bot_row[2],
            "active_engine": bot_row[3],
            "entity_id": bot_row[4],
            "entity_type": bot_row[5],
            "device_id": bot_row[6],
            "created_at": bot_row[7],
            "modified_at": bot_row[8],
        }

        device_id = bot_row[6]
        if not device_id:
            log.info(
                "[device-binding:list_sandboxes_by_bot] Bot has no device_id: bot_id=%s",
                bot_id,
            )
            return bot_info, []

        binding_params: dict[str, Any] = {"device_id": device_id}
        env_clause = ""
        if env:
            env_clause = "AND env = :env"
            binding_params["env"] = env

        binding_sql = f"""
        SELECT id, entity_id, entity_type, device_id, device_provider, env, device_props,
               status, apply_reason, applied_by,
               release_reason, released_by, released_at, last_alive_at,
               gmt_create, gmt_modified
        FROM ac_entity_device_binding
        WHERE device_id = :device_id AND status = 'ACTIVE' {env_clause}
        ORDER BY id DESC
        """
        binding_rows = self._session.execute(
            text(binding_sql), binding_params
        ).fetchall()

        sandboxes = []
        for row in binding_rows:
            device_props_json = row[6]
            try:
                device_props = (
                    json.loads(device_props_json)
                    if isinstance(device_props_json, str)
                    else device_props_json
                )
            except (json.JSONDecodeError, TypeError):
                device_props = {}
            sandboxes.append(
                DeviceBindingRecord(
                    id=row[0],
                    entity_id=row[1],
                    entity_type=row[2],
                    device_id=row[3],
                    device_provider=row[4],
                    env=row[5],
                    device_props=device_props,
                    status=row[7],
                    apply_reason=row[8],
                    applied_by=row[9],
                    release_reason=row[10],
                    released_by=row[11],
                    released_at=row[12],
                    last_alive_at=row[13],
                    gmt_create=row[14],
                    gmt_modified=row[15],
                )
            )

        log.info(
            "[device-binding:list_sandboxes_by_bot] found %s sandboxes for bot_id=%s",
            len(sandboxes),
            bot_id,
        )
        return bot_info, sandboxes

    @with_orm_session
    def list_all_active_bot_device(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        env: str = "prod",
        bot_type: str | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        from sqlalchemy import text

        where_clauses = ["is_delete = 0", "env = :env", "status = 'ACTIVE'"]
        params: dict[str, Any] = {"env": env}
        if bot_type is not None:
            where_clauses.append("bot_type = :bot_type")
            params["bot_type"] = bot_type

        where_sql = " AND ".join(where_clauses)

        count_sql = f"SELECT COUNT(*) FROM ac_bots WHERE {where_sql}"
        total = self._session.execute(text(count_sql), params).scalar() or 0

        offset = (page - 1) * page_size
        data_sql = f"""
        SELECT bot_id, entity_id, binding_id, bot_type, active_engine, status
        FROM ac_bots
        WHERE {where_sql}
        ORDER BY id DESC
        LIMIT :limit OFFSET :offset
        """
        params["limit"] = page_size
        params["offset"] = offset

        rows = self._session.execute(text(data_sql), params).fetchall()
        items = [
            {
                "bot_id": row[0],
                "entity_id": row[1],
                "binding_id": row[2],
                "bot_type": row[3],
                "active_engine": row[4],
                "status": row[5],
            }
            for row in rows
        ]

        log.info(
            "[device-binding:list_all_active_bot_device] returned %s items, total=%s",
            len(items),
            total,
        )
        return total, items

    @with_orm_session
    def get_bot_binding(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str = "prod",
    ) -> dict[str, Any] | None:
        from sqlalchemy import text

        sql = """
        SELECT b.bot_id, b.entity_id, b.binding_id, b.bot_type, b.active_engine, b.status,
               eb.device_provider
        FROM ac_bots b
        LEFT JOIN ac_entity_device_binding eb ON b.binding_id = eb.id
        WHERE b.bot_id = :bot_id AND b.entity_id = :entity_id
          AND b.is_delete = 0 AND b.status = 'ACTIVE' AND b.env = :env
        LIMIT 1
        """
        row = self._session.execute(
            text(sql), {"bot_id": bot_id, "entity_id": entity_id, "env": env}
        ).fetchone()

        if not row:
            log.info(
                "[device-binding:get_bot_binding] Bot not found: bot_id=%s, entity_id=%s",
                bot_id,
                entity_id,
            )
            return None

        result = {
            "bot_id": row[0],
            "entity_id": row[1],
            "binding_id": row[2],
            "bot_type": row[3],
            "active_engine": row[4],
            "status": row[5],
            "device_provider": row[6],
        }
        log.info(
            "[device-binding:get_bot_binding] result: bot_type=%s, binding_id=%s",
            result["bot_type"],
            result["binding_id"],
        )
        return result

    @with_orm_session
    def get_publish_binding(
        self,
        *,
        source_bot_id: str,
        status: str,
    ) -> int | None:
        from sqlalchemy import text

        if status == "validating":
            json_path = "$.binding.verify"
        else:
            json_path = "$.binding.online"

        sql = f"""
        SELECT JSON_UNQUOTE(JSON_EXTRACT(ext, '{json_path}')) AS binding_id
        FROM ac_bot_publish
        WHERE source_bot_id = :source_bot_id AND status = :status
        ORDER BY id DESC
        LIMIT 1
        """
        row = self._session.execute(
            text(sql), {"source_bot_id": source_bot_id, "status": status}
        ).fetchone()

        if not row or not row[0]:
            log.info(
                "[device-binding:get_publish_binding] No binding found for source_bot_id=%s, status=%s",
                source_bot_id,
                status,
            )
            return None

        binding_id = int(row[0])
        log.info(
            "[device-binding:get_publish_binding] result: binding_id=%s", binding_id
        )
        return binding_id

    @with_orm_session
    def list_paas_device_by_bot_personal(
        self,
        *,
        bot_id: str,
        binding_id: int,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text

        # 先查询 binding 以确定 device_provider
        binding_sql = """
        SELECT device_provider
        FROM ac_entity_device_binding
        WHERE id = :binding_id AND status = 'ACTIVE'
        """
        binding_row = self._session.execute(
            text(binding_sql), {"binding_id": binding_id}
        ).fetchone()

        if binding_row is None:
            log.info(
                "[device-binding:list_paas_device_by_bot_personal] No ACTIVE binding: binding_id=%s",
                binding_id,
            )
            return []

        device_provider = (binding_row[0] or "").lower()

        # baas provider: 走 baas 链路 JOIN 查询
        if device_provider == "baas":
            return self._query_personal_devices_baas(
                binding_id=binding_id, bot_id=bot_id
            )

        # arca provider (默认): 从 device_props 解析
        sql = """
        SELECT
            JSON_UNQUOTE(JSON_EXTRACT(device_props, '$.sandbox_id')) AS paas_device_id,
            device_provider AS provider_type,
            status,
            JSON_UNQUOTE(JSON_EXTRACT(device_props, '$.ttl_expiration_time')) AS ttl_expiration_time,
            JSON_EXTRACT(device_props, '$.ttl_expiration_timestamp') AS ttl_expiration_timestamp,
            IFNULL(JSON_EXTRACT(device_props, '$.refresh_fail_count'), 0) AS refresh_fail_count
        FROM ac_entity_device_binding
        WHERE id = :binding_id AND status = 'ACTIVE'
        """
        rows = self._session.execute(text(sql), {"binding_id": binding_id}).fetchall()

        items = []
        for row in rows:
            ttl_timestamp = row[4]
            if ttl_timestamp is not None:
                ttl_timestamp = int(float(ttl_timestamp))
            items.append(
                {
                    "paas_device_id": row[0] or "",
                    "provider_type": row[1],
                    "status": row[2],
                    "ttl_expiration_time": row[3],
                    "ttl_expiration_timestamp": ttl_timestamp,
                    "device_uuid": None,
                    "source_table_id": binding_id,
                    "source_table": "ac_binding",
                    "refresh_fail_count": int(row[5] or 0),
                }
            )

        log.info(
            "[device-binding:list_paas_device_by_bot_personal] arca: returned %s items for bot_id=%s",
            len(items),
            bot_id,
        )
        return items

    def _query_personal_devices_baas(
        self, *, binding_id: int, bot_id: str
    ) -> list[dict[str, Any]]:
        """personal bot 的 baas provider 分支：JOIN 查询 baas_device。

        链路：ac_entity_device_binding → baas_bot → baas_bot_device_rel → baas_device
        对齐 _resolve_devices_from_binding 的 Python 编排逻辑。
        """
        from sqlalchemy import text

        sql = """
        SELECT
            d.device_uuid,
            d.provider_device_id AS paas_device_id,
            d.provider_type,
            d.status,
            JSON_UNQUOTE(JSON_EXTRACT(d.provider_device_props, '$.ttl_expiration_time')) AS ttl_expiration_time,
            JSON_EXTRACT(d.provider_device_props, '$.ttl_expiration_timestamp') AS ttl_expiration_timestamp,
            IFNULL(JSON_EXTRACT(d.provider_device_props, '$.refresh_fail_count'), 0) AS refresh_fail_count,
            d.id AS source_table_id
        FROM ac_entity_device_binding eb
        INNER JOIN baas_bot b ON b.bot_uuid = eb.device_id AND b.status = 'ACTIVE' AND b.is_deleted = 0
        INNER JOIN baas_bot_device_rel r ON r.bot_id = b.id AND r.is_deleted = 0
        INNER JOIN baas_device d ON d.device_uuid = r.device_uuid AND d.is_deleted = 0 AND d.status = 'ACTIVE'
        WHERE eb.id = :binding_id AND eb.status = 'ACTIVE'
        """
        rows = self._session.execute(text(sql), {"binding_id": binding_id}).fetchall()

        items = []
        for row in rows:
            ttl_timestamp = row[5]
            if ttl_timestamp is not None:
                ttl_timestamp = int(float(ttl_timestamp))
            items.append(
                {
                    "device_uuid": row[0],
                    "paas_device_id": row[1] or "",
                    "provider_type": row[2],
                    "status": row[3],
                    "query_status": "personal",
                    "ttl_expiration_time": row[4],
                    "ttl_expiration_timestamp": ttl_timestamp,
                    "source_table": "baas_device",
                    "source_table_id": str(row[7]) if row[7] is not None else None,
                    "refresh_fail_count": int(row[6] or 0),
                }
            )

        log.info(
            "[device-binding:_query_personal_devices_baas] returned %s items for bot_id=%s, binding_id=%s",
            len(items),
            bot_id,
            binding_id,
        )
        return items

    def _query_service_devices_draft(
        self, bot_id: str, entity_id: str
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text

        sql = """
        SELECT
            NULL AS device_uuid,
            JSON_UNQUOTE(JSON_EXTRACT(eb.device_props, '$.sandbox_id')) AS paas_device_id,
            eb.device_provider AS provider_type,
            eb.status,
            JSON_UNQUOTE(JSON_EXTRACT(eb.device_props, '$.ttl_expiration_time')) AS ttl_expiration_time,
            JSON_EXTRACT(eb.device_props, '$.ttl_expiration_timestamp') AS ttl_expiration_timestamp,
            'ac_binding' AS source_table,
            eb.id AS source_table_id,
            IFNULL(JSON_EXTRACT(eb.device_props, '$.refresh_fail_count'), 0) AS refresh_fail_count
        FROM ac_bots b
        INNER JOIN ac_entity_device_binding eb ON b.binding_id = eb.id
        WHERE b.bot_id = :bot_id AND b.entity_id = :entity_id AND b.is_delete = 0 AND b.status = 'ACTIVE'
        """
        return self._session.execute(
            text(sql), {"bot_id": bot_id, "entity_id": entity_id}
        ).fetchall()

    def _query_service_devices_validating(
        self, bot_id: str, entity_id: str
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text

        sql = """
        SELECT
            d.device_uuid,
            d.provider_device_id AS paas_device_id,
            d.provider_type,
            d.status,
            JSON_UNQUOTE(JSON_EXTRACT(d.provider_device_props, '$.ttl_expiration_time')) AS ttl_expiration_time,
            JSON_EXTRACT(d.provider_device_props, '$.ttl_expiration_timestamp') AS ttl_expiration_timestamp,
            'baas_device' AS source_table,
            d.id AS source_table_id,
            IFNULL(JSON_EXTRACT(d.provider_device_props, '$.refresh_fail_count'), 0) AS refresh_fail_count
        FROM ac_bot_publish p
        INNER JOIN ac_entity_device_binding eb ON JSON_UNQUOTE(JSON_EXTRACT(p.ext, '$.binding.verify')) = eb.id
        INNER JOIN baas_bot b ON b.bot_uuid = eb.device_id AND b.status = 'ACTIVE'
        INNER JOIN baas_bot_device_rel r ON r.bot_id = b.id AND r.is_deleted = 0
        INNER JOIN baas_device d ON d.device_uuid = r.device_uuid AND d.is_deleted = 0 AND d.status = 'ACTIVE'
        WHERE p.source_bot_id = :bot_id AND p.owner_id = :entity_id AND p.status = 'validating'
        ORDER BY d.id DESC
        """
        return self._session.execute(
            text(sql), {"bot_id": bot_id, "entity_id": entity_id}
        ).fetchall()

    def _query_service_devices_online(
        self, bot_id: str, entity_id: str
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text

        sql = """
        SELECT
            d.device_uuid,
            d.provider_device_id AS paas_device_id,
            d.provider_type,
            d.status,
            JSON_UNQUOTE(JSON_EXTRACT(d.provider_device_props, '$.ttl_expiration_time')) AS ttl_expiration_time,
            JSON_EXTRACT(d.provider_device_props, '$.ttl_expiration_timestamp') AS ttl_expiration_timestamp,
            'baas_device' AS source_table,
            d.id AS source_table_id,
            IFNULL(JSON_EXTRACT(d.provider_device_props, '$.refresh_fail_count'), 0) AS refresh_fail_count
        FROM ac_bot_publish p
        INNER JOIN ac_entity_device_binding eb ON JSON_UNQUOTE(JSON_EXTRACT(p.ext, '$.binding.online')) = eb.id
        INNER JOIN baas_bot b ON b.bot_uuid = eb.device_id AND b.status = 'ACTIVE'
        INNER JOIN baas_bot_device_rel r ON r.bot_id = b.id AND r.is_deleted = 0
        INNER JOIN baas_device d ON d.device_uuid = r.device_uuid AND d.is_deleted = 0 AND d.status = 'ACTIVE'
        WHERE p.source_bot_id = :bot_id AND p.owner_id = :entity_id AND p.status = 'success'
        ORDER BY d.id DESC
        """
        return self._session.execute(
            text(sql), {"bot_id": bot_id, "entity_id": entity_id}
        ).fetchall()

    @with_orm_session
    def list_paas_device_by_bot_service(
        self,
        *,
        bot_id: str,
        entity_id: str,
        statuses: list[str],
    ) -> list[dict[str, Any]]:
        status_query_map = {
            "draft": self._query_service_devices_draft,
            "validating": self._query_service_devices_validating,
            "online": self._query_service_devices_online,
        }

        items: list[dict[str, Any]] = []
        for s in statuses:
            query_fn = status_query_map.get(s)
            if query_fn is None:
                log.warning(
                    "[device-binding:list_paas_device_by_bot_service] Unknown status: %s, skipping",
                    s,
                )
                continue

            rows = query_fn(bot_id, entity_id)
            for row in rows:
                paas_device_id = row[1] or ""
                ttl_timestamp = row[5]
                if ttl_timestamp is not None:
                    ttl_timestamp = int(float(ttl_timestamp))
                items.append(
                    {
                        "device_uuid": row[0],
                        "paas_device_id": paas_device_id,
                        "provider_type": row[2],
                        "status": row[3],
                        "query_status": s,
                        "ttl_expiration_time": row[4],
                        "ttl_expiration_timestamp": ttl_timestamp,
                        "source_table": row[6],
                        "source_table_id": str(row[7]) if row[7] is not None else None,
                        "refresh_fail_count": int(row[8] or 0),
                    }
                )

        log.info(
            "[device-binding:list_paas_device_by_bot_service] returned %s items for bot_id=%s, entity_id=%s, statuses=%s",
            len(items),
            bot_id,
            entity_id,
            statuses,
        )
        return items

    @with_orm_session
    def update_baas_device_ttl(
        self,
        *,
        device_uuid: str,
        ttl_expiration_time: str,
        ttl_expiration_timestamp: int,
    ) -> None:
        from sqlalchemy import text

        sql = """
        UPDATE baas_device
        SET provider_device_props = JSON_SET(
            IFNULL(provider_device_props, '{}'),
            '$.ttl_expiration_time', :ttl_expiration_time,
            '$.ttl_expiration_timestamp', :ttl_expiration_timestamp
        )
        WHERE device_uuid = :device_uuid
        """
        self._session.execute(
            text(sql),
            {
                "ttl_expiration_time": ttl_expiration_time,
                "ttl_expiration_timestamp": ttl_expiration_timestamp,
                "device_uuid": device_uuid,
            },
        )
        log.info(
            "[device-binding:update_baas_device_ttl] device %s updated successfully",
            device_uuid,
        )

    @with_orm_session
    def update_baas_device_ttl_by_id(
        self,
        *,
        baas_device_id: int,
        ttl_expiration_time: str,
        ttl_expiration_timestamp: int,
        refresh_fail_count: int = 0,
    ) -> None:
        from sqlalchemy import text

        sql = """
        UPDATE baas_device
        SET provider_device_props = JSON_SET(
            IFNULL(provider_device_props, '{}'),
            '$.ttl_expiration_time', :ttl_expiration_time,
            '$.ttl_expiration_timestamp', :ttl_expiration_timestamp,
            '$.refresh_fail_count', :refresh_fail_count
        )
        WHERE id = :baas_device_id
        """
        self._session.execute(
            text(sql),
            {
                "ttl_expiration_time": ttl_expiration_time,
                "ttl_expiration_timestamp": ttl_expiration_timestamp,
                "refresh_fail_count": refresh_fail_count,
                "baas_device_id": baas_device_id,
            },
        )
        log.info(
            "[device-binding:update_baas_device_ttl_by_id] baas_device %s updated successfully",
            baas_device_id,
        )

    @with_orm_session
    def update_baas_device_refresh_fail_count_by_id(
        self,
        *,
        baas_device_id: int,
        refresh_fail_count: int,
    ) -> None:
        from sqlalchemy import text

        sql = """
        UPDATE baas_device
        SET provider_device_props = JSON_SET(
            IFNULL(provider_device_props, '{}'),
            '$.refresh_fail_count', :refresh_fail_count
        )
        WHERE id = :baas_device_id
        """
        self._session.execute(
            text(sql),
            {
                "refresh_fail_count": refresh_fail_count,
                "baas_device_id": baas_device_id,
            },
        )
        log.info(
            "[device-binding:update_baas_device_refresh_fail_count_by_id] baas_device %s updated successfully",
            baas_device_id,
        )

    @with_orm_session
    def get_baas_device_by_id(self, *, baas_device_id: int) -> dict[str, Any] | None:
        row = (
            self._session.query(DeviceModel)
            .filter(DeviceModel.id == baas_device_id)
            .first()
        )
        if row is None:
            return None
        record = row.to_record()
        return {
            "id": record.id,
            "tenant": record.tenant,
            "env": record.env,
            "status": record.status,
            "provider_type": record.provider_type,
            "provider_device_id": record.provider_device_id,
            "provider_device_props": record.provider_device_props,
            "is_deleted": record.is_deleted,
        }

    @with_orm_session
    def list_baas_devices_active_paginated(
        self,
        *,
        env: str,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[dict[str, Any]]]:
        offset = (page - 1) * page_size

        # 查询总数
        total = (
            self._session.query(DeviceModel)
            .filter(
                DeviceModel.status == "ACTIVE",
                DeviceModel.provider_type == "ARCA",
                DeviceModel.env == env,
                DeviceModel.is_deleted == 0,
            )
            .count()
        )

        # 查询数据
        rows = (
            self._session.query(DeviceModel)
            .filter(
                DeviceModel.status == "ACTIVE",
                DeviceModel.provider_type == "ARCA",
                DeviceModel.env == env,
                DeviceModel.is_deleted == 0,
            )
            .order_by(DeviceModel.id.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )
        result = []
        for r in rows:
            record = r.to_record()
            result.append(
                {
                    "id": record.id,
                    "tenant": record.tenant,
                    "env": record.env,
                    "status": record.status,
                    "provider_type": record.provider_type,
                    "provider_device_id": record.provider_device_id,
                    "provider_device_props": record.provider_device_props,
                    "is_deleted": record.is_deleted,
                }
            )
        return total, result

    @with_orm_session
    def list_baas_devices_by_ttl_asc(
        self,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """查询 baas_device 中 ACTIVE 且有 sandbox_id 的 ARCA 记录，
        按 TTL 过期时间 ASC 排序，取前 limit 条。

        用于 DeviceTtlTimer 定时任务：优先处理即将过期的服务 bot 设备。
        限 provider_type='ARCA'（对齐 list_baas_devices_active_paginated）——
        teclaw 的 update_device_ttl 是 NotImplementedError，k8s/docker 等沙箱
        生命周期不由本任务续期，混入会触发无效续期 + 探活噪声。
        """
        rows = (
            self._session.query(DeviceModel)
            .filter(
                DeviceModel.status == "ACTIVE",
                DeviceModel.provider_type == "ARCA",
                DeviceModel.provider_device_props.op("->>")("$.sandbox_id").isnot(None),
            )
            .order_by(
                DeviceModel.provider_device_props.op("->>")(
                    "$.ttl_expiration_time"
                ).asc()
            )
            .limit(limit)
            .all()
        )
        result = []
        for r in rows:
            record = r.to_record()
            result.append(
                {
                    "id": record.id,
                    "tenant": record.tenant,
                    "env": record.env,
                    "status": record.status,
                    "provider_type": record.provider_type,
                    "provider_device_id": record.provider_device_id,
                    "provider_device_props": record.provider_device_props,
                    "is_deleted": record.is_deleted,
                }
            )
        log.info(
            "[device-binding:list_baas_devices_by_ttl_asc] result: %s rows",
            len(result),
        )
        return result

    @with_orm_session
    def update_baas_device_status_by_id(
        self, *, baas_device_id: int, status: str, modifier: str = "system"
    ) -> None:
        self._session.query(DeviceModel).filter(
            DeviceModel.id == baas_device_id
        ).update({"status": status, "modifier": modifier}, synchronize_session=False)
        log.info(
            "[device-binding:update_baas_device_status_by_id] baas_device %s status updated to %s by %s",
            baas_device_id,
            status,
            modifier,
        )
