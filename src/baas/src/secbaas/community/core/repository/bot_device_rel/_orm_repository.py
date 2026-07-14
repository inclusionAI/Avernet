"""ORM-based bot-device relationship repository for baas_bot_device_rel table."""

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.logger import get_logger

from ._orm_model import BotDeviceRelModel
from ._protocol import BotDeviceRelRepository
from ._record import BotDeviceRelRecord

log = get_logger("orm-repository")


class OrmBotDeviceRelRepository(OrmConnectionMixin, BotDeviceRelRepository):
    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def insert_rel(
        self,
        *,
        bot_id: int,
        device_uuid: str,
        tenant: str,
        env: str,
        domain: str,
        creator: str,
        modifier: str,
    ) -> int:
        log.info(
            "insert_rel: bot_id=%s, device_uuid=%s, tenant=%s, env=%s",
            bot_id,
            device_uuid,
            tenant,
            env,
        )
        row = BotDeviceRelModel(
            bot_id=bot_id,
            device_uuid=device_uuid,
            tenant=tenant,
            env=env,
            domain=domain,
            creator=creator,
            modifier=modifier,
        )
        self._session.add(row)
        self._session.flush()
        result = int(row.id)
        log.info("[bot-device-rel:insert_rel] result: id=%s", result)
        return result

    @with_orm_session
    def get_by_id(
        self, rel_id: int, tenant: str, env: str
    ) -> BotDeviceRelRecord | None:
        log.info("get_by_id: rel_id=%s, tenant=%s, env=%s", rel_id, tenant, env)
        row = (
            self._session.query(BotDeviceRelModel)
            .filter(
                BotDeviceRelModel.id == rel_id,
                BotDeviceRelModel.tenant == tenant,
                BotDeviceRelModel.env == env,
                BotDeviceRelModel.is_deleted == 0,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[bot-device-rel:get_by_id] result: %s", record.id if record else "None"
        )
        return record

    @with_orm_session
    def list_by_bot_id(
        self, bot_id: int, tenant: str, env: str
    ) -> list[BotDeviceRelRecord]:
        log.info("list_by_bot_id: bot_id=%s, tenant=%s, env=%s", bot_id, tenant, env)
        rows = (
            self._session.query(BotDeviceRelModel)
            .filter(
                BotDeviceRelModel.bot_id == bot_id,
                BotDeviceRelModel.tenant == tenant,
                BotDeviceRelModel.env == env,
                BotDeviceRelModel.is_deleted == 0,
            )
            .order_by(BotDeviceRelModel.id.desc())
            .all()
        )
        items = [r.to_record() for r in rows]
        log.info("[bot-device-rel:list_by_bot_id] result: %s rows", len(items))
        return items

    @with_orm_session
    def get_by_device_uuid(
        self, device_uuid: str, tenant: str, env: str
    ) -> BotDeviceRelRecord | None:
        log.info(
            "get_by_device_uuid: device_uuid=%s, tenant=%s, env=%s",
            device_uuid,
            tenant,
            env,
        )
        row = (
            self._session.query(BotDeviceRelModel)
            .filter(
                BotDeviceRelModel.device_uuid == device_uuid,
                BotDeviceRelModel.tenant == tenant,
                BotDeviceRelModel.env == env,
                BotDeviceRelModel.is_deleted == 0,
            )
            .order_by(BotDeviceRelModel.id.desc())
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[bot-device-rel:get_by_device_uuid] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def soft_delete(self, *, rel_id: int, tenant: str, env: str, modifier: str) -> None:
        log.info("soft_delete: rel_id=%s, tenant=%s, env=%s", rel_id, tenant, env)
        from sqlalchemy import func as sa_func

        self._session.query(BotDeviceRelModel).filter(
            BotDeviceRelModel.id == rel_id,
            BotDeviceRelModel.tenant == tenant,
            BotDeviceRelModel.env == env,
            BotDeviceRelModel.is_deleted == 0,
        ).update(
            {
                "is_deleted": rel_id,
                "modifier": modifier,
                "gmt_modified": sa_func.now(),
            },
            synchronize_session=False,
        )
        log.info("[bot-device-rel:soft_delete] result: done")

    @with_orm_session
    def exists(self, *, bot_id: int, device_uuid: str, tenant: str, env: str) -> bool:
        log.info(
            "exists: bot_id=%s, device_uuid=%s, tenant=%s", bot_id, device_uuid, tenant
        )
        from sqlalchemy import func as sa_func

        count = (
            self._session.query(sa_func.count(BotDeviceRelModel.id))
            .filter(
                BotDeviceRelModel.bot_id == bot_id,
                BotDeviceRelModel.device_uuid == device_uuid,
                BotDeviceRelModel.tenant == tenant,
                BotDeviceRelModel.env == env,
                BotDeviceRelModel.is_deleted == 0,
            )
            .scalar()
        )
        result = bool(count)
        log.info("[bot-device-rel:exists] result: %s", result)
        return result

    @with_orm_session
    def count_by_bot_id(self, bot_id: int, tenant: str, env: str) -> int:
        log.info("count_by_bot_id: bot_id=%s, tenant=%s, env=%s", bot_id, tenant, env)
        from sqlalchemy import func as sa_func

        count = (
            self._session.query(sa_func.count(BotDeviceRelModel.id))
            .filter(
                BotDeviceRelModel.bot_id == bot_id,
                BotDeviceRelModel.tenant == tenant,
                BotDeviceRelModel.env == env,
                BotDeviceRelModel.is_deleted == 0,
            )
            .scalar()
        )
        result = int(count)
        log.info("[bot-device-rel:count_by_bot_id] result: %s", result)
        return result

    @with_orm_session
    def soft_delete_by_bot_id(
        self, *, bot_id: int, tenant: str, env: str, modifier: str
    ) -> int:
        log.info(
            "soft_delete_by_bot_id: bot_id=%s, tenant=%s, env=%s", bot_id, tenant, env
        )
        from sqlalchemy import func as sa_func

        result = (
            self._session.query(BotDeviceRelModel)
            .filter(
                BotDeviceRelModel.bot_id == bot_id,
                BotDeviceRelModel.tenant == tenant,
                BotDeviceRelModel.env == env,
                BotDeviceRelModel.is_deleted == 0,
            )
            .update(
                {
                    "is_deleted": BotDeviceRelModel.id,
                    "modifier": modifier,
                    "gmt_modified": sa_func.now(),
                },
                synchronize_session=False,
            )
        )
        result = int(result)
        log.info("[bot-device-rel:soft_delete_by_bot_id] result: %s rows", result)
        return result

    @with_orm_session
    def batch_insert_rels(
        self,
        *,
        bot_id: int,
        device_uuids: list[str],
        tenant: str,
        env: str,
        domain: str,
        creator: str,
        modifier: str,
    ) -> list[int]:
        log.info(
            "batch_insert_rels: bot_id=%s, device_uuids_count=%s, tenant=%s",
            bot_id,
            len(device_uuids),
            tenant,
        )
        ids: list[int] = []
        for device_uuid in device_uuids:
            row = BotDeviceRelModel(
                bot_id=bot_id,
                device_uuid=device_uuid,
                tenant=tenant,
                env=env,
                domain=domain,
                creator=creator,
                modifier=modifier,
            )
            self._session.add(row)
            self._session.flush()
            ids.append(int(row.id))
        log.info("[bot-device-rel:batch_insert_rels] result: %s ids", len(ids))
        return ids
