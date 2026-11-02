"""Publish repository ORM implementation."""

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.logger import get_logger

from ._orm_model import PublishModel
from ._protocol import PublishRepository
from ._record import PublishRecord

log = get_logger("orm")


class OrmPublishRepository(OrmConnectionMixin, PublishRepository):
    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def insert_publish(
        self,
        *,
        tenant: str,
        env: str,
        domain: str,
        bot_id: int,
        publish_type: str,
        status: str,
        creator: str,
        modifier: str,
        name: str,
        description: str | None = None,
        publisher: str,
        replica_desired: int,
        batch_capacity: int,
        batch_number: int,
        cooldown_seconds: int,
        config_version: str | None = None,
        last_publish_id: int | None = None,
        changelog: str | None = None,
        extra_config: dict | None = None,
    ) -> int:
        log.info(
            "insert_publish: bot_id=%s, tenant=%s, env=%s, publish_type=%s",
            bot_id,
            tenant,
            env,
            publish_type,
        )
        import json

        row = PublishModel(
            tenant=tenant,
            env=env,
            domain=domain,
            bot_id=bot_id,
            publish_type=publish_type,
            name=name,
            description=description,
            publisher=publisher,
            replica_desired=replica_desired,
            batch_capacity=batch_capacity,
            batch_number=batch_number,
            cooldown_seconds=cooldown_seconds,
            config_version=config_version,
            status=status,
            last_publish_id=last_publish_id,
            changelog=changelog,
            extra_config=json.dumps(extra_config, ensure_ascii=False)
            if extra_config
            else None,
            creator=creator,
            modifier=modifier,
            is_deleted=0,
        )
        self._session.add(row)
        self._session.flush()
        result = int(row.id)
        log.info("[publish:insert_publish] result: id=%s", result)
        return result

    @with_orm_session
    def get_by_id(self, publish_id: int, tenant: str, env: str) -> PublishRecord | None:
        log.info("get_by_id: publish_id=%s, tenant=%s, env=%s", publish_id, tenant, env)
        row = (
            self._session.query(PublishModel)
            .filter(
                PublishModel.id == publish_id,
                PublishModel.tenant == tenant,
                PublishModel.env == env,
                PublishModel.is_deleted == 0,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info("[publish:get_by_id] result: %s", record.id if record else "None")
        return record

    @with_orm_session
    def update_status(
        self,
        *,
        publish_id: int,
        tenant: str,
        env: str,
        status: str,
        modifier: str | None = None,
    ) -> None:
        log.info(
            "update_status: publish_id=%s, tenant=%s, env=%s, status=%s",
            publish_id,
            tenant,
            env,
            status,
        )
        from sqlalchemy import func

        values = {"status": status, "gmt_modified": func.now()}
        if modifier is not None:
            values["modifier"] = modifier
        self._session.query(PublishModel).filter(
            PublishModel.id == publish_id,
            PublishModel.tenant == tenant,
            PublishModel.env == env,
            PublishModel.is_deleted == 0,
        ).update(values, synchronize_session=False)
        log.info("[publish:update_status] result: done")

    @with_orm_session
    def update_publish(
        self,
        *,
        publish_id: int,
        tenant: str,
        env: str,
        extra_config: dict | None = None,
        modifier: str | None = None,
    ) -> int:
        log.info(
            "update_publish: publish_id=%s, tenant=%s, env=%s", publish_id, tenant, env
        )
        from sqlalchemy import func

        values: dict = {"gmt_modified": func.now()}
        if extra_config is not None:
            import json

            values["extra_config"] = json.dumps(extra_config, ensure_ascii=False)
        if modifier is not None:
            values["modifier"] = modifier
        if len(values) <= 1:
            return 0
        result = (
            self._session.query(PublishModel)
            .filter(
                PublishModel.id == publish_id,
                PublishModel.tenant == tenant,
                PublishModel.env == env,
                PublishModel.is_deleted == 0,
            )
            .update(values, synchronize_session=False)
        )
        result = int(result)
        log.info("[publish:update_publish] result: %s rows", result)
        return result

    @with_orm_session
    def list_by_bot_id(self, bot_id: int, tenant: str, env: str) -> list[PublishRecord]:
        log.info("list_by_bot_id: bot_id=%s, tenant=%s, env=%s", bot_id, tenant, env)
        rows = (
            self._session.query(PublishModel)
            .filter(
                PublishModel.bot_id == bot_id,
                PublishModel.tenant == tenant,
                PublishModel.env == env,
                PublishModel.is_deleted == 0,
            )
            .order_by(PublishModel.id.desc())
            .all()
        )
        items = [r.to_record() for r in rows]
        log.info("[publish:list_by_bot_id] result: %s rows", len(items))
        return items

    @with_orm_session
    def get_active_by_bot_id(
        self, bot_id: int, tenant: str, env: str
    ) -> PublishRecord | None:
        log.info(
            "get_active_by_bot_id: bot_id=%s, tenant=%s, env=%s", bot_id, tenant, env
        )
        row = (
            self._session.query(PublishModel)
            .filter(
                PublishModel.bot_id == bot_id,
                PublishModel.tenant == tenant,
                PublishModel.env == env,
                PublishModel.status.notin_(
                    ["SUCCESS", "FAILED", "REJECTED", "REVOKED"]
                ),
                PublishModel.is_deleted == 0,
            )
            .order_by(PublishModel.id.desc())
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[publish:get_active_by_bot_id] result: %s", record.id if record else "None"
        )
        return record

    @with_orm_session
    def list_active_by_bot_ids(
        self, bot_ids: list[int], tenant: str, env: str
    ) -> list[PublishRecord]:
        log.info(
            "list_active_by_bot_ids: bot_ids=%s, tenant=%s, env=%s",
            bot_ids,
            tenant,
            env,
        )
        if not bot_ids:
            log.info("[publish:list_active_by_bot_ids] result: 0 rows")
            return []
        rows = (
            self._session.query(PublishModel)
            .filter(
                PublishModel.bot_id.in_(bot_ids),
                PublishModel.tenant == tenant,
                PublishModel.env == env,
                PublishModel.status.notin_(
                    ["SUCCESS", "FAILED", "REJECTED", "REVOKED"]
                ),
                PublishModel.is_deleted == 0,
            )
            .order_by(PublishModel.id.desc())
            .all()
        )
        items = [r.to_record() for r in rows]
        log.info("[publish:list_active_by_bot_ids] result: %s rows", len(items))
        return items

    @with_orm_session
    def soft_delete(
        self, *, publish_id: int, tenant: str, env: str, modifier: str
    ) -> None:
        log.info(
            "soft_delete: publish_id=%s, tenant=%s, env=%s", publish_id, tenant, env
        )
        from sqlalchemy import func

        row = (
            self._session.query(PublishModel)
            .filter(
                PublishModel.id == publish_id,
                PublishModel.tenant == tenant,
                PublishModel.env == env,
                PublishModel.is_deleted == 0,
            )
            .first()
        )
        if row is None:
            log.info("[publish:soft_delete] result: not found")
            return
        self._session.query(PublishModel).filter(
            PublishModel.id == publish_id,
            PublishModel.tenant == tenant,
            PublishModel.env == env,
            PublishModel.is_deleted == 0,
        ).update(
            {
                "is_deleted": publish_id,
                "modifier": modifier,
                "gmt_modified": func.now(),
            },
            synchronize_session=False,
        )
        log.info("[publish:soft_delete] result: done")
