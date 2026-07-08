"""Publish batch repository ORM implementation."""

from datetime import datetime

from secbaas.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.logger import get_logger

from ._orm_model import PublishBatchModel
from ._protocol import PublishBatchRepository
from ._record import PublishBatchRecord

log = get_logger("orm-repository")


class OrmPublishBatchRepository(OrmConnectionMixin, PublishBatchRepository):
    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def insert_batch(
        self,
        *,
        tenant: str,
        env: str,
        domain: str,
        publish_id: int,
        bot_id: int,
        batch_index: int,
        batch_capacity: int,
        status: str,
        creator: str,
        modifier: str,
        gmt_start: datetime | None = None,
        gmt_complete: datetime | None = None,
        error_message: str | None = None,
        extra_config: dict | None = None,
    ) -> int:
        log.info(
            "insert_batch: publish_id=%s, batch_index=%s, tenant=%s, env=%s",
            publish_id,
            batch_index,
            tenant,
            env,
        )
        import json

        row = PublishBatchModel(
            tenant=tenant,
            env=env,
            domain=domain,
            publish_id=publish_id,
            bot_id=bot_id,
            batch_index=batch_index,
            batch_capacity=batch_capacity,
            status=status,
            gmt_start=gmt_start,
            gmt_complete=gmt_complete,
            error_message=error_message,
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
        log.info("[publish-batch:insert_batch] result: id=%s", result)
        return result

    @with_orm_session
    def get_by_id(
        self, batch_id: int, tenant: str, env: str
    ) -> PublishBatchRecord | None:
        log.info("get_by_id: batch_id=%s, tenant=%s, env=%s", batch_id, tenant, env)
        row = (
            self._session.query(PublishBatchModel)
            .filter(
                PublishBatchModel.id == batch_id,
                PublishBatchModel.tenant == tenant,
                PublishBatchModel.env == env,
                PublishBatchModel.is_deleted == 0,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[publish-batch:get_by_id] result: %s", record.id if record else "None"
        )
        return record

    @with_orm_session
    def update_status(
        self,
        *,
        batch_id: int,
        tenant: str,
        env: str,
        status: str,
        modifier: str | None = None,
    ) -> None:
        log.info(
            "update_status: batch_id=%s, tenant=%s, env=%s, status=%s",
            batch_id,
            tenant,
            env,
            status,
        )
        from sqlalchemy import func

        values = {"status": status, "gmt_modified": func.now()}
        if modifier is not None:
            values["modifier"] = modifier
        self._session.query(PublishBatchModel).filter(
            PublishBatchModel.id == batch_id,
            PublishBatchModel.tenant == tenant,
            PublishBatchModel.env == env,
            PublishBatchModel.is_deleted == 0,
        ).update(values, synchronize_session=False)
        log.info("[publish-batch:update_status] result: done")

    @with_orm_session
    def list_by_publish_id(
        self, publish_id: int, tenant: str, env: str
    ) -> list[PublishBatchRecord]:
        log.info(
            "list_by_publish_id: publish_id=%s, tenant=%s, env=%s",
            publish_id,
            tenant,
            env,
        )
        rows = (
            self._session.query(PublishBatchModel)
            .filter(
                PublishBatchModel.publish_id == publish_id,
                PublishBatchModel.tenant == tenant,
                PublishBatchModel.env == env,
                PublishBatchModel.is_deleted == 0,
            )
            .order_by(PublishBatchModel.batch_index.asc())
            .all()
        )
        items = [r.to_record() for r in rows]
        log.info("[publish-batch:list_by_publish_id] result: %s rows", len(items))
        return items

    @with_orm_session
    def list_by_publish_and_stage(
        self, publish_id: int, tenant: str, env: str, stage: str
    ) -> list[PublishBatchRecord]:
        all_batches = self.list_by_publish_id(publish_id, tenant, env)
        return [b for b in all_batches if b.stage == stage]

    @with_orm_session
    def soft_delete(
        self, *, batch_id: int, tenant: str, env: str, modifier: str
    ) -> None:
        log.info("soft_delete: batch_id=%s, tenant=%s, env=%s", batch_id, tenant, env)
        from sqlalchemy import func

        row = (
            self._session.query(PublishBatchModel)
            .filter(
                PublishBatchModel.id == batch_id,
                PublishBatchModel.tenant == tenant,
                PublishBatchModel.env == env,
                PublishBatchModel.is_deleted == 0,
            )
            .first()
        )
        if row is None:
            log.info("[publish-batch:soft_delete] result: not found")
            return
        self._session.query(PublishBatchModel).filter(
            PublishBatchModel.id == batch_id,
            PublishBatchModel.tenant == tenant,
            PublishBatchModel.env == env,
            PublishBatchModel.is_deleted == 0,
        ).update(
            {
                "is_deleted": batch_id,
                "modifier": modifier,
                "gmt_modified": func.now(),
            },
            synchronize_session=False,
        )
        log.info("[publish-batch:soft_delete] result: done")
