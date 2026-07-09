"""ORM-based device repository for baas_device table."""

import json
from datetime import datetime
from typing import Any

from secbaas.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.logger import get_logger

from ._orm_model import DeviceModel
from ._protocol import DeviceRepository
from ._record import DeviceRecord

log = get_logger("orm-repository")


class OrmDeviceRepository(OrmConnectionMixin, DeviceRepository):
    # MySQL TEXT type limit is 65535 bytes. Using 65000 for safety margin.
    _MAX_ERR_MSG_TOTAL_BYTES = 65000
    _MAX_NEW_ERR_MSG_BYTES = 10000
    _ERR_MSG_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

    def __init__(self, database) -> None:
        self._database = database

    @staticmethod
    def _build_err_msg_prefix(new_err_msg: str) -> str:
        timestamp = datetime.now().strftime(
            OrmDeviceRepository._ERR_MSG_TIMESTAMP_FORMAT
        )
        prefix = f"[device:{timestamp}]\n"
        prefix_bytes = len(prefix.encode("utf-8"))
        available_for_msg = max(
            0, OrmDeviceRepository._MAX_NEW_ERR_MSG_BYTES - prefix_bytes
        )

        msg_bytes = new_err_msg.encode("utf-8")
        if len(msg_bytes) <= available_for_msg:
            return prefix + new_err_msg

        truncated = msg_bytes[:available_for_msg]
        while truncated:
            try:
                return prefix + truncated.decode("utf-8")
            except UnicodeDecodeError:
                truncated = truncated[:-1]
        return prefix

    @with_orm_session
    def insert_device(
        self,
        *,
        device_uuid: str,
        tenant: str,
        env: str,
        domain: str,
        creator: str,
        modifier: str,
        status: str = "PENDING",
        provider_type: str | None,
        provider_device_id: str | None = None,
        provider_device_props: dict[str, Any] | None = None,
        extra_config: dict[str, Any] | None = None,
    ) -> int:
        log.info(
            "insert_device: device_uuid=%s, tenant=%s, env=%s, status=%s",
            device_uuid,
            tenant,
            env,
            status,
        )
        row = DeviceModel(
            device_uuid=device_uuid,
            tenant=tenant,
            env=env,
            domain=domain,
            creator=creator,
            modifier=modifier,
            status=status,
            provider_type=provider_type,
            provider_device_id=provider_device_id,
            provider_device_props=json.dumps(provider_device_props, ensure_ascii=False)
            if provider_device_props
            else None,
            extra_config=json.dumps(extra_config, ensure_ascii=False)
            if extra_config
            else None,
        )
        self._session.add(row)
        self._session.flush()
        result = int(row.id)
        log.info("[device:insert_device] result: id=%s", result)
        return result

    @with_orm_session
    def get_by_id(self, device_id: int, tenant: str, env: str) -> DeviceRecord | None:
        log.info("get_by_id: device_id=%s, tenant=%s, env=%s", device_id, tenant, env)
        row = (
            self._session.query(DeviceModel)
            .filter(
                DeviceModel.id == device_id,
                DeviceModel.tenant == tenant,
                DeviceModel.env == env,
                DeviceModel.is_deleted == 0,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info("[device:get_by_id] result: %s", record.id if record else "None")
        return record

    @with_orm_session
    def get_by_ids(
        self, device_ids: list[int], tenant: str, env: str
    ) -> dict[int, DeviceRecord]:
        log.info(
            "get_by_ids: device_ids_count=%s, tenant=%s, env=%s",
            len(device_ids) if device_ids else 0,
            tenant,
            env,
        )
        if not device_ids:
            result: dict[int, DeviceRecord] = {}
            log.info("[device:get_by_ids] result: 0 rows")
            return result
        rows = (
            self._session.query(DeviceModel)
            .filter(
                DeviceModel.id.in_(device_ids),
                DeviceModel.tenant == tenant,
                DeviceModel.env == env,
                DeviceModel.is_deleted == 0,
            )
            .all()
        )
        result = {r.id: r.to_record() for r in rows}
        log.info("[device:get_by_ids] result: %s rows", len(result))
        return result

    @with_orm_session
    def get_by_device_uuid(
        self, device_uuid: str, tenant: str, env: str, status: str | None
    ) -> DeviceRecord | None:
        log.info(
            "get_by_device_uuid: device_uuid=%s, tenant=%s, env=%s, status=%s",
            device_uuid,
            tenant,
            env,
            status,
        )
        query = self._session.query(DeviceModel).filter(
            DeviceModel.device_uuid == device_uuid,
            DeviceModel.tenant == tenant,
            DeviceModel.env == env,
            DeviceModel.is_deleted == 0,
        )
        if status is not None:
            query = query.filter(DeviceModel.status == status)
        row = query.order_by(DeviceModel.id.desc()).first()
        record = row.to_record() if row else None
        log.info(
            "[device:get_by_device_uuid] result: %s", record.id if record else "None"
        )
        return record

    @with_orm_session
    def get_by_device_uuid_only(self, device_uuid: str) -> DeviceRecord | None:
        log.info("get_by_device_uuid_only: device_uuid=%s", device_uuid)
        row = (
            self._session.query(DeviceModel)
            .filter(
                DeviceModel.device_uuid == device_uuid,
                DeviceModel.is_deleted == 0,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[device:get_by_device_uuid_only] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def list_by_device_uuid(
        self, device_uuid: str, tenant: str, env: str
    ) -> list[DeviceRecord]:
        log.info(
            "list_by_device_uuid: device_uuid=%s, tenant=%s, env=%s",
            device_uuid,
            tenant,
            env,
        )
        rows = (
            self._session.query(DeviceModel)
            .filter(
                DeviceModel.device_uuid == device_uuid,
                DeviceModel.tenant == tenant,
                DeviceModel.env == env,
                DeviceModel.is_deleted == 0,
            )
            .order_by(DeviceModel.id.desc())
            .all()
        )
        items = [r.to_record() for r in rows]
        log.info("[device:list_by_device_uuid] result: %s rows", len(items))
        return items

    @with_orm_session
    def get_active_by_device_uuid(
        self, device_uuid: str, tenant: str, env: str
    ) -> DeviceRecord | None:
        log.info(
            "get_active_by_device_uuid: device_uuid=%s, tenant=%s, env=%s",
            device_uuid,
            tenant,
            env,
        )
        row = (
            self._session.query(DeviceModel)
            .filter(
                DeviceModel.device_uuid == device_uuid,
                DeviceModel.tenant == tenant,
                DeviceModel.env == env,
                DeviceModel.status == "ACTIVE",
                DeviceModel.is_deleted == 0,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[device:get_active_by_device_uuid] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def get_active_or_updating_by_device_uuid(
        self, device_uuid: str, tenant: str, env: str
    ) -> DeviceRecord | None:
        log.info(
            "get_active_or_updating_by_device_uuid: device_uuid=%s, tenant=%s, env=%s",
            device_uuid,
            tenant,
            env,
        )
        row = (
            self._session.query(DeviceModel)
            .filter(
                DeviceModel.device_uuid == device_uuid,
                DeviceModel.tenant == tenant,
                DeviceModel.env == env,
                DeviceModel.status.in_(["ACTIVE", "UPDATING"]),
                DeviceModel.is_deleted == 0,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[device:get_active_or_updating_by_device_uuid] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def get_by_provider_device_id_like(
        self, provider_device_id_prefix: str
    ) -> DeviceRecord | None:
        log.info(
            "get_by_provider_device_id_like: provider_device_id_prefix=%s",
            provider_device_id_prefix,
        )
        row = (
            self._session.query(DeviceModel)
            .filter(
                DeviceModel.provider_device_id.like(f"%{provider_device_id_prefix}%"),
            )
            .order_by(DeviceModel.id.desc())
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[device:get_by_provider_device_id_like] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def get_by_provider_device_id_prefix(
        self, prefix: str, env: str
    ) -> DeviceRecord | None:
        log.info("get_by_provider_device_id_prefix: prefix=%s, env=%s", prefix, env)
        row = (
            self._session.query(DeviceModel)
            .filter(
                DeviceModel.provider_device_id.like(f"{prefix}%"),
                DeviceModel.env == env,
                DeviceModel.is_deleted == 0,
            )
            .order_by(DeviceModel.id.desc())
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[device:get_by_provider_device_id_prefix] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def update_device(
        self,
        *,
        device_id: int,
        tenant: str,
        env: str,
        modifier: str | None = None,
        provider_type: str | None = None,
        provider_device_id: str | None = None,
        provider_device_props: dict[str, Any] | None = None,
        extra_config: dict[str, Any] | None = None,
        status: str | None = None,
        err_msg: str | None = None,
    ) -> int:
        log.info(
            "update_device: device_id=%s, tenant=%s, env=%s, status=%s",
            device_id,
            tenant,
            env,
            status,
        )
        from sqlalchemy import func as sa_func

        values: dict[str, Any] = {"gmt_modified": sa_func.now()}
        if modifier is not None:
            values["modifier"] = modifier
        if provider_type is not None:
            values["provider_type"] = provider_type
        if provider_device_id is not None:
            values["provider_device_id"] = provider_device_id
        if provider_device_props is not None:
            values["provider_device_props"] = json.dumps(
                provider_device_props, ensure_ascii=False
            )
        if extra_config is not None:
            values["extra_config"] = json.dumps(extra_config, ensure_ascii=False)
        if status is not None:
            values["status"] = status
        if err_msg is not None:
            formatted_prefix = self._build_err_msg_prefix(err_msg)
            separator = "\n\n"
            # SQLite uses substr(), MySQL uses left().
            trunc_fn = (
                sa_func.substr
                if self._session.bind.dialect.name == "sqlite"
                else sa_func.left
            )
            values["err_msg"] = trunc_fn(
                sa_func.concat(
                    formatted_prefix + separator,
                    sa_func.coalesce(DeviceModel.err_msg, ""),
                ),
                self._MAX_ERR_MSG_TOTAL_BYTES,
            )

        result = (
            self._session.query(DeviceModel)
            .filter(
                DeviceModel.id == device_id,
                DeviceModel.tenant == tenant,
                DeviceModel.env == env,
                DeviceModel.is_deleted == 0,
            )
            .update(values, synchronize_session=False)
        )
        result = int(result)
        log.info("[device:update_device] result: %s rows", result)
        return result

    @with_orm_session
    def update_status(
        self, *, device_id: int, tenant: str, env: str, status: str
    ) -> None:
        log.info(
            "update_status: device_id=%s, tenant=%s, env=%s, status=%s",
            device_id,
            tenant,
            env,
            status,
        )
        from sqlalchemy import func as sa_func

        self._session.query(DeviceModel).filter(
            DeviceModel.id == device_id,
            DeviceModel.tenant == tenant,
            DeviceModel.env == env,
            DeviceModel.is_deleted == 0,
        ).update(
            {
                "status": status,
                "gmt_modified": sa_func.now(),
            },
            synchronize_session=False,
        )
        log.info("[device:update_status] result: done")

    @with_orm_session
    def soft_delete(
        self, *, device_id: int, tenant: str, env: str, modifier: str
    ) -> None:
        log.info("soft_delete: device_id=%s, tenant=%s, env=%s", device_id, tenant, env)
        from sqlalchemy import func as sa_func

        self._session.query(DeviceModel).filter(
            DeviceModel.id == device_id,
            DeviceModel.tenant == tenant,
            DeviceModel.env == env,
            DeviceModel.is_deleted == 0,
        ).update(
            {
                "is_deleted": device_id,
                "modifier": modifier,
                "gmt_modified": sa_func.now(),
            },
            synchronize_session=False,
        )
        log.info("[device:soft_delete] result: done")

    @with_orm_session
    def soft_delete_by_device_uuid(
        self, device_uuid: str, tenant: str, env: str, modifier: str
    ) -> int:
        log.info(
            "soft_delete_by_device_uuid: device_uuid=%s, tenant=%s, env=%s",
            device_uuid,
            tenant,
            env,
        )
        from sqlalchemy import func as sa_func

        result = (
            self._session.query(DeviceModel)
            .filter(
                DeviceModel.device_uuid == device_uuid,
                DeviceModel.tenant == tenant,
                DeviceModel.env == env,
                DeviceModel.is_deleted == 0,
            )
            .update(
                {
                    "is_deleted": DeviceModel.id,
                    "modifier": modifier,
                    "gmt_modified": sa_func.now(),
                },
                synchronize_session=False,
            )
        )
        result = int(result)
        log.info("[device:soft_delete_by_device_uuid] result: %s rows", result)
        return result

    @with_orm_session
    def update_status_by_device_uuid(
        self, device_uuid: str, tenant: str, env: str, status: str
    ) -> int:
        log.info(
            "update_status_by_device_uuid: device_uuid=%s, tenant=%s, env=%s, status=%s",
            device_uuid,
            tenant,
            env,
            status,
        )
        from sqlalchemy import func as sa_func

        result = (
            self._session.query(DeviceModel)
            .filter(
                DeviceModel.device_uuid == device_uuid,
                DeviceModel.tenant == tenant,
                DeviceModel.env == env,
                DeviceModel.is_deleted == 0,
            )
            .update(
                {
                    "status": status,
                    "gmt_modified": sa_func.now(),
                },
                synchronize_session=False,
            )
        )
        result = int(result)
        log.info("[device:update_status_by_device_uuid] result: %s rows", result)
        return result

    @with_orm_session
    def list_devices(
        self,
        *,
        tenant: str,
        env: str,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[DeviceRecord]]:
        log.info(
            "list_devices: tenant=%s, env=%s, status=%s, page=%s",
            tenant,
            env,
            status,
            page,
        )
        from sqlalchemy import func as sa_func

        query = self._session.query(DeviceModel).filter(
            DeviceModel.tenant == tenant,
            DeviceModel.env == env,
            DeviceModel.is_deleted == 0,
        )
        if status is not None:
            query = query.filter(DeviceModel.status == status)

        total = query.with_entities(sa_func.count(DeviceModel.id)).scalar()
        offset = (page - 1) * page_size
        rows = (
            query.order_by(DeviceModel.id.desc()).offset(offset).limit(page_size).all()
        )
        items = [r.to_record() for r in rows]
        log.info("[device:list_devices] result: %s rows", len(items))
        return total, items

    @with_orm_session
    def list_by_bot_id(
        self, *, bot_id: int, tenant: str, env: str
    ) -> list[DeviceRecord]:
        log.info("list_by_bot_id: bot_id=%s, tenant=%s, env=%s", bot_id, tenant, env)
        from secbaas.core.repository.bot_device_rel import BotDeviceRelModel

        rows = (
            self._session.query(DeviceModel)
            .join(
                BotDeviceRelModel,
                DeviceModel.device_uuid == BotDeviceRelModel.device_uuid,
            )
            .filter(
                BotDeviceRelModel.bot_id == bot_id,
                DeviceModel.tenant == tenant,
                DeviceModel.env == env,
                DeviceModel.is_deleted == 0,
                BotDeviceRelModel.is_deleted == 0,
            )
            .order_by(DeviceModel.id.desc())
            .all()
        )
        items = [r.to_record() for r in rows]
        log.info("[device:list_by_bot_id] result: %s rows", len(items))
        return items

    @with_orm_session
    def list_active_devices_by_bot_id(self, *, bot_id: int) -> list[DeviceRecord]:
        """Get ACTIVE devices associated with a bot via baas_bot_device_rel (no tenant/env filter).

        Matches the 0525 SQL:
            INNER JOIN baas_bot_device_rel r ON r.bot_id = b.id AND r.is_deleted = 0
            INNER JOIN baas_device d ON d.device_uuid = r.device_uuid
                AND d.is_deleted = 0 AND d.status = 'ACTIVE'
        """
        log.info("list_active_devices_by_bot_id: bot_id=%s", bot_id)
        from secbaas.core.repository.bot_device_rel import BotDeviceRelModel

        rows = (
            self._session.query(DeviceModel)
            .join(
                BotDeviceRelModel,
                DeviceModel.device_uuid == BotDeviceRelModel.device_uuid,
            )
            .filter(
                BotDeviceRelModel.bot_id == bot_id,
                DeviceModel.is_deleted == 0,
                DeviceModel.status == "ACTIVE",
                BotDeviceRelModel.is_deleted == 0,
            )
            .order_by(DeviceModel.id.desc())
            .all()
        )
        items = [r.to_record() for r in rows]
        log.info("[device:list_active_devices_by_bot_id] result: %s rows", len(items))
        return items

    @with_orm_session
    def list_devices_by_bot_ids(
        self, *, bot_ids: list[int], tenant: str, env: str
    ) -> dict[int, list[DeviceRecord]]:
        log.info(
            "list_devices_by_bot_ids: bot_ids_count=%s, tenant=%s, env=%s",
            len(bot_ids) if bot_ids else 0,
            tenant,
            env,
        )
        if not bot_ids:
            result: dict[int, list[DeviceRecord]] = {}
            log.info("[device:list_devices_by_bot_ids] result: 0 rows")
            return result

        from secbaas.core.repository.bot_device_rel import BotDeviceRelModel

        results = (
            self._session.query(DeviceModel, BotDeviceRelModel.bot_id)
            .join(
                BotDeviceRelModel,
                DeviceModel.device_uuid == BotDeviceRelModel.device_uuid,
            )
            .filter(
                BotDeviceRelModel.bot_id.in_(bot_ids),
                DeviceModel.tenant == tenant,
                DeviceModel.env == env,
                DeviceModel.is_deleted == 0,
                BotDeviceRelModel.is_deleted == 0,
            )
            .order_by(DeviceModel.id.desc())
            .all()
        )

        result: dict[int, list[DeviceRecord]] = {bid: [] for bid in bot_ids}
        for device, bot_id in results:
            result.setdefault(bot_id, []).append(device.to_record())
        total_rows = sum(len(v) for v in result.values())
        log.info(
            "[device:list_devices_by_bot_ids] result: %s rows across %s bots",
            total_rows,
            len(bot_ids),
        )
        return result

    @with_orm_session
    def list_active_local_devices_by_machine_user(
        self, machine_id: str, user_id: str, env: str
    ) -> list[DeviceRecord]:
        log.info(
            "list_active_local_devices_by_machine_user: machine_id=%s, user_id=%s, env=%s",
            machine_id,
            user_id,
            env,
        )
        pattern = f"%--{machine_id}--{user_id}@%"
        rows = (
            self._session.query(DeviceModel)
            .filter(
                DeviceModel.provider_type == "local",
                DeviceModel.env == env,
                DeviceModel.status == "ACTIVE",
                DeviceModel.provider_device_id.like(pattern),
                DeviceModel.is_deleted == 0,
            )
            .order_by(DeviceModel.id.desc())
            .all()
        )
        items = [r.to_record() for r in rows]
        log.info(
            "[device:list_active_local_devices_by_machine_user] result: %s rows",
            len(items),
        )
        return items

    @with_orm_session
    def batch_update_status_to_offline(self, device_ids: list[int], env: str) -> int:
        log.info(
            "batch_update_status_to_offline: device_ids_count=%s, env=%s",
            len(device_ids) if device_ids else 0,
            env,
        )
        if not device_ids:
            result = 0
            log.info("[device:batch_update_status_to_offline] result: %s rows", result)
            return result
        from sqlalchemy import func as sa_func

        result = (
            self._session.query(DeviceModel)
            .filter(
                DeviceModel.id.in_(device_ids),
                DeviceModel.env == env,
                DeviceModel.is_deleted == 0,
            )
            .update(
                {
                    "status": "OFFLINE",
                    "gmt_modified": sa_func.now(),
                },
                synchronize_session=False,
            )
        )
        result = int(result)
        log.info("[device:batch_update_status_to_offline] result: %s rows", result)
        return result
