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
from ._queries import DeviceBindingQueriesMixin
from ._record import DeviceBindingRecord, DeviceBindingStatus

log = get_logger("orm")


class OrmDeviceBindingRepository(
    DeviceBindingQueriesMixin, OrmConnectionMixin, DeviceBindingRepository
):
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
    def list_bindings_by_id_asc(
        self,
        *,
        due_before: str,
        last_id: int = 0,
        limit: int = 100,
    ) -> list[DeviceBindingRecord]:
        """查询 ac_entity_device_binding 中 ACTIVE 且有 sandbox_id 的记录，
        按 id ASC 排序，取前 limit 条。

        用于 DeviceTtlTimer 定时任务：仅处理 TTL 不晚于调用方指定的 due_before（已到期或即将
        到期）的个人 bot 设备；窗口外的设备会被 Arca 续期逻辑跳过，
        无需入选。

        支持 keyset 游标分页：仅以 id 排序，``id > last_id`` 保证游标逐页推进，
        不会因 TTL 续期失败/相同而困在队列头部。
        """
        from sqlalchemy import func

        rows = (
            self._session.query(DeviceBindingModel)
            .filter(
                DeviceBindingModel.status == "ACTIVE",
                DeviceBindingModel.device_props.op("->>")("$.sandbox_id").isnot(None),
                func.coalesce(
                    DeviceBindingModel.device_props.op("->>")("$.ttl_expiration_time"),
                    "",
                ).op("<=")(due_before),
                DeviceBindingModel.id > last_id,
            )
            .order_by(DeviceBindingModel.id.asc())
            .limit(limit)
            .all()
        )
        items = [self._model_to_record(r) for r in rows if r is not None]
        log.info(
            "[device-binding:list_bindings_by_id_asc] result: %s rows",
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
    def list_baas_devices_by_id_asc(
        self,
        *,
        due_before: str,
        last_id: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """查询 baas_device 中 ACTIVE 且有 sandbox_id 的 ARCA 记录，
        按 id ASC 排序，取前 limit 条。

        用于 DeviceTtlTimer 定时任务：仅处理 TTL 不晚于调用方指定的 due_before（已到期或即将
        到期）的服务 bot 设备；窗口外的设备会被 Arca 续期逻辑跳过。
        限 provider_type='ARCA'（对齐 list_baas_devices_active_paginated）——
        teclaw 的 update_device_ttl 是 NotImplementedError，k8s/docker 等沙箱
        生命周期不由本任务续期，混入会触发无效续期 + 探活噪声。

        支持 keyset 游标分页：仅以 id 排序，``id > last_id`` 保证游标逐页推进。
        """
        from sqlalchemy import func

        rows = (
            self._session.query(DeviceModel)
            .filter(
                DeviceModel.status == "ACTIVE",
                DeviceModel.provider_type == "ARCA",
                DeviceModel.provider_device_props.op("->>")("$.sandbox_id").isnot(None),
                func.coalesce(
                    DeviceModel.provider_device_props.op("->>")(
                        "$.ttl_expiration_time"
                    ),
                    "",
                ).op("<=")(due_before),
                DeviceModel.id > last_id,
            )
            .order_by(DeviceModel.id.asc())
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
            "[device-binding:list_baas_devices_by_id_asc] result: %s rows",
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

    @with_orm_session
    def list_export_rows(
        self,
        *,
        env: str | None,
        status: str,
        device_provider: str,
        offset: int,
        limit: int,
    ) -> list[tuple[str, str | dict[str, Any] | None]]:
        query = self._session.query(
            DeviceBindingModel.entity_id,
            DeviceBindingModel.device_props,
        ).filter(
            DeviceBindingModel.status == status,
            DeviceBindingModel.device_provider == device_provider,
        )
        if env is not None:
            query = query.filter(DeviceBindingModel.env == env)
        rows = (
            query.order_by(
                DeviceBindingModel.gmt_create.desc(),
                DeviceBindingModel.id.desc(),
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [(row[0], row[1]) for row in rows]
