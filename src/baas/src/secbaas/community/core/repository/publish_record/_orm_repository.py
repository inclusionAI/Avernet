"""Publish record repository ORM implementation."""

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.logger import get_logger

from ._orm_model import PublishRecordModel
from ._protocol import PublishRecordRepository
from ._record import PublishRecordRecord

log = get_logger("orm-repository")


class OrmPublishRecordRepository(OrmConnectionMixin, PublishRecordRepository):
    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def insert_record(
        self,
        *,
        tenant: str,
        env: str,
        domain: str,
        device_id: int | None,
        bot_id: int | None,
        publish_id: int | None,
        batch_id: int | None,
        event_type: str,
        result_status: str,
        creator: str,
        modifier: str,
        trigger_source: str | None = None,
        publish_reason: str | None = None,
        result_message: str | None = None,
        extra_config: dict | None = None,
    ) -> int:
        log.info(
            "insert_record: device_id=%s, publish_id=%s, event_type=%s, tenant=%s",
            device_id,
            publish_id,
            event_type,
            tenant,
        )
        import json

        row = PublishRecordModel(
            tenant=tenant,
            env=env,
            domain=domain,
            device_id=device_id,
            bot_id=bot_id,
            publish_id=publish_id,
            batch_id=batch_id,
            event_type=event_type,
            result_status=result_status,
            trigger_source=trigger_source,
            publish_reason=publish_reason,
            result_message=result_message,
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
        log.info("[publish-record:insert_record] result: id=%s", result)
        return result

    @with_orm_session
    def get_by_id(
        self, record_id: int, tenant: str, env: str
    ) -> PublishRecordRecord | None:
        log.info("get_by_id: record_id=%s, tenant=%s, env=%s", record_id, tenant, env)
        row = (
            self._session.query(PublishRecordModel)
            .filter(
                PublishRecordModel.id == record_id,
                PublishRecordModel.tenant == tenant,
                PublishRecordModel.env == env,
                PublishRecordModel.is_deleted == 0,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[publish-record:get_by_id] result: %s", record.id if record else "None"
        )
        return record

    @with_orm_session
    def list_by_batch_id(
        self, batch_id: int, tenant: str, env: str
    ) -> list[PublishRecordRecord]:
        log.info(
            "list_by_batch_id: batch_id=%s, tenant=%s, env=%s", batch_id, tenant, env
        )
        rows = (
            self._session.query(PublishRecordModel)
            .filter(
                PublishRecordModel.batch_id == batch_id,
                PublishRecordModel.tenant == tenant,
                PublishRecordModel.env == env,
                PublishRecordModel.is_deleted == 0,
            )
            .order_by(PublishRecordModel.id.asc())
            .all()
        )
        items = [r.to_record() for r in rows]
        log.info("[publish-record:list_by_batch_id] result: %s rows", len(items))
        return items

    @with_orm_session
    def list_by_device_id(
        self, device_id: int, tenant: str, env: str
    ) -> list[PublishRecordRecord]:
        log.info(
            "list_by_device_id: device_id=%s, tenant=%s, env=%s", device_id, tenant, env
        )
        rows = (
            self._session.query(PublishRecordModel)
            .filter(
                PublishRecordModel.device_id == device_id,
                PublishRecordModel.tenant == tenant,
                PublishRecordModel.env == env,
                PublishRecordModel.is_deleted == 0,
            )
            .order_by(PublishRecordModel.id.asc())
            .all()
        )
        items = [r.to_record() for r in rows]
        log.info("[publish-record:list_by_device_id] result: %s rows", len(items))
        return items

    @with_orm_session
    def update_result(
        self,
        *,
        record_id: int,
        tenant: str,
        env: str,
        result_status: str,
        result_message: str | None = None,
        modifier: str | None = None,
    ) -> None:
        log.info(
            "update_result: record_id=%s, tenant=%s, env=%s, result_status=%s",
            record_id,
            tenant,
            env,
            result_status,
        )
        from sqlalchemy import func

        values = {"result_status": result_status, "gmt_modified": func.now()}
        if result_message is not None:
            values["result_message"] = result_message
        if modifier is not None:
            values["modifier"] = modifier
        self._session.query(PublishRecordModel).filter(
            PublishRecordModel.id == record_id,
            PublishRecordModel.tenant == tenant,
            PublishRecordModel.env == env,
            PublishRecordModel.is_deleted == 0,
        ).update(values, synchronize_session=False)
        log.info("[publish-record:update_result] result: done")

    @with_orm_session
    def update_result_if_processing(
        self,
        *,
        record_id: int,
        tenant: str,
        env: str,
        result_status: str,
        result_message: str | None = None,
        modifier: str | None = None,
    ) -> bool:
        log.info(
            "update_result_if_processing: record_id=%s, tenant=%s, env=%s, result_status=%s",
            record_id,
            tenant,
            env,
            result_status,
        )
        from sqlalchemy import func

        values = {"result_status": result_status, "gmt_modified": func.now()}
        if result_message is not None:
            values["result_message"] = result_message
        if modifier is not None:
            values["modifier"] = modifier
        result = (
            self._session.query(PublishRecordModel)
            .filter(
                PublishRecordModel.id == record_id,
                PublishRecordModel.tenant == tenant,
                PublishRecordModel.env == env,
                PublishRecordModel.is_deleted == 0,
                PublishRecordModel.result_status == "PROCESSING",
            )
            .update(values, synchronize_session=False)
        )
        result = int(result) > 0
        log.info("[publish-record:update_result_if_processing] result: %s", result)
        return result

    @with_orm_session
    def get_by_device_id_and_publish_id(
        self, device_id: int, publish_id: int, tenant: str, env: str
    ) -> PublishRecordRecord | None:
        log.info(
            "get_by_device_id_and_publish_id: device_id=%s, publish_id=%s, tenant=%s, env=%s",
            device_id,
            publish_id,
            tenant,
            env,
        )
        row = (
            self._session.query(PublishRecordModel)
            .filter(
                PublishRecordModel.device_id == device_id,
                PublishRecordModel.publish_id == publish_id,
                PublishRecordModel.tenant == tenant,
                PublishRecordModel.env == env,
                PublishRecordModel.is_deleted == 0,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[publish-record:get_by_device_id_and_publish_id] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def get_processing_record_by_device_and_publish(
        self, device_id: int, publish_id: int, tenant: str, env: str
    ) -> PublishRecordRecord | None:
        log.info(
            "get_processing_record_by_device_and_publish: device_id=%s, publish_id=%s, tenant=%s",
            device_id,
            publish_id,
            tenant,
        )
        row = (
            self._session.query(PublishRecordModel)
            .filter(
                PublishRecordModel.device_id == device_id,
                PublishRecordModel.publish_id == publish_id,
                PublishRecordModel.tenant == tenant,
                PublishRecordModel.env == env,
                PublishRecordModel.is_deleted == 0,
                PublishRecordModel.result_status == "PROCESSING",
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[publish-record:get_processing_record_by_device_and_publish] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def exists_record_for_device_and_publish(
        self, device_id: int, publish_id: int, tenant: str, env: str
    ) -> bool:
        log.info(
            "exists_record_for_device_and_publish: device_id=%s, publish_id=%s, tenant=%s",
            device_id,
            publish_id,
            tenant,
        )
        from sqlalchemy import func

        count = (
            self._session.query(func.count(PublishRecordModel.id))
            .filter(
                PublishRecordModel.device_id == device_id,
                PublishRecordModel.publish_id == publish_id,
                PublishRecordModel.tenant == tenant,
                PublishRecordModel.env == env,
                PublishRecordModel.is_deleted == 0,
            )
            .scalar()
        )
        result = count is not None and count > 0
        log.info(
            "[publish-record:exists_record_for_device_and_publish] result: %s", result
        )
        return result

    @with_orm_session
    def update_device_id(
        self,
        *,
        record_id: int,
        device_id: int,
        tenant: str,
        env: str,
        modifier: str | None = None,
    ) -> None:
        log.info(
            "update_device_id: record_id=%s, device_id=%s, tenant=%s",
            record_id,
            device_id,
            tenant,
        )
        from sqlalchemy import func

        values = {"device_id": device_id, "gmt_modified": func.now()}
        if modifier is not None:
            values["modifier"] = modifier
        self._session.query(PublishRecordModel).filter(
            PublishRecordModel.id == record_id,
            PublishRecordModel.tenant == tenant,
            PublishRecordModel.env == env,
            PublishRecordModel.is_deleted == 0,
        ).update(values, synchronize_session=False)
        log.info("[publish-record:update_device_id] result: done")

    @with_orm_session
    def list_by_publish_id_and_batch_id(
        self,
        publish_id: int,
        batch_id: int,
        tenant: str,
        env: str,
        status: str | None = None,
    ) -> list[PublishRecordRecord]:
        log.info(
            "list_by_publish_id_and_batch_id: publish_id=%s, batch_id=%s, tenant=%s, env=%s, status=%s",
            publish_id,
            batch_id,
            tenant,
            env,
            status,
        )
        query = self._session.query(PublishRecordModel).filter(
            PublishRecordModel.publish_id == publish_id,
            PublishRecordModel.batch_id == batch_id,
            PublishRecordModel.tenant == tenant,
            PublishRecordModel.env == env,
            PublishRecordModel.is_deleted == 0,
        )
        if status is not None:
            query = query.filter(PublishRecordModel.result_status == status)
        rows = query.order_by(PublishRecordModel.id.asc()).all()
        items = [r.to_record() for r in rows]
        log.info(
            "[publish-record:list_by_publish_id_and_batch_id] result: %s rows",
            len(items),
        )
        return items

    @with_orm_session
    def count_records_by_batch_id(
        self, batch_id: int, tenant: str, env: str
    ) -> dict[str, int]:
        log.info(
            "count_records_by_batch_id: batch_id=%s, tenant=%s, env=%s",
            batch_id,
            tenant,
            env,
        )
        from sqlalchemy import func

        rows = (
            self._session.query(
                PublishRecordModel.result_status,
                func.count(PublishRecordModel.id),
            )
            .filter(
                PublishRecordModel.batch_id == batch_id,
                PublishRecordModel.tenant == tenant,
                PublishRecordModel.env == env,
                PublishRecordModel.is_deleted == 0,
            )
            .group_by(PublishRecordModel.result_status)
            .all()
        )
        result = {row[0]: row[1] for row in rows}
        log.info("[publish-record:count_records_by_batch_id] result: %s", result)
        return result

    @with_orm_session
    def count_records_by_publish_id(
        self, publish_id: int, tenant: str, env: str
    ) -> dict[str, int]:
        log.info(
            "count_records_by_publish_id: publish_id=%s, tenant=%s, env=%s",
            publish_id,
            tenant,
            env,
        )
        from sqlalchemy import func

        from secbaas.community.core.repository.publish_batch import PublishBatchModel

        rows = (
            self._session.query(
                PublishRecordModel.result_status,
                func.count(PublishRecordModel.id),
            )
            .join(
                PublishBatchModel,
                PublishRecordModel.batch_id == PublishBatchModel.id,
            )
            .filter(
                PublishBatchModel.publish_id == publish_id,
                PublishBatchModel.is_deleted == 0,
                PublishRecordModel.tenant == tenant,
                PublishRecordModel.env == env,
                PublishRecordModel.is_deleted == 0,
            )
            .group_by(PublishRecordModel.result_status)
            .all()
        )
        result = {row[0]: row[1] for row in rows}
        log.info("[publish-record:count_records_by_publish_id] result: %s", result)
        return result

    @with_orm_session
    def list_stale_processing_records(
        self, publish_id: int, timeout_seconds: int, tenant: str, env: str
    ) -> list[PublishRecordRecord]:
        log.info(
            "list_stale_processing_records: publish_id=%s, timeout_seconds=%s, tenant=%s",
            publish_id,
            timeout_seconds,
            tenant,
        )
        from datetime import timedelta

        from sqlalchemy import func, text

        db_now = self._session.execute(func.now()).scalar()
        cutoff = db_now - timedelta(seconds=timeout_seconds)

        sql = text(
            """SELECT pr.*, d.device_uuid
            FROM baas_publish_record pr
            LEFT JOIN baas_device d ON pr.device_id = d.id AND d.is_deleted = 0
            WHERE pr.publish_id = :publish_id
              AND pr.result_status = 'PROCESSING'
              AND pr.gmt_create < :cutoff
              AND pr.tenant = :tenant AND pr.env = :env AND pr.is_deleted = 0"""
        )
        result = self._session.execute(
            sql,
            {
                "publish_id": publish_id,
                "cutoff": cutoff,
                "tenant": tenant,
                "env": env,
            },
        )
        import json

        rows = result.fetchall()
        records = []
        for row in rows:
            extra_config = row.extra_config
            if isinstance(extra_config, str):
                try:
                    extra_config = json.loads(extra_config)
                except (json.JSONDecodeError, TypeError):
                    extra_config = {}
            elif extra_config is None:
                extra_config = {}
            record = PublishRecordRecord(
                id=row.id,
                gmt_create=row.gmt_create,
                gmt_modified=row.gmt_modified,
                tenant=row.tenant,
                env=row.env,
                domain=row.domain,
                is_deleted=row.is_deleted or 0,
                creator=row.creator,
                modifier=row.modifier,
                device_id=row.device_id,
                bot_id=row.bot_id,
                publish_id=row.publish_id,
                batch_id=row.batch_id,
                event_type=row.event_type,
                trigger_source=row.trigger_source,
                publish_reason=row.publish_reason,
                result_status=row.result_status,
                result_message=row.result_message,
                extra_config=extra_config or {},
                device_uuid=row.device_uuid,
            )
            records.append(record)
        log.info(
            "[publish-record:list_stale_processing_records] result: %s rows",
            len(records),
        )
        return records

    @with_orm_session
    def get_latest_processing_record_by_device(
        self, device_id: int, tenant: str, env: str
    ) -> PublishRecordRecord | None:
        log.info(
            "get_latest_processing_record_by_device: device_id=%s, tenant=%s, env=%s",
            device_id,
            tenant,
            env,
        )
        row = (
            self._session.query(PublishRecordModel)
            .filter(
                PublishRecordModel.device_id == device_id,
                PublishRecordModel.tenant == tenant,
                PublishRecordModel.env == env,
                PublishRecordModel.result_status == "PROCESSING",
                PublishRecordModel.is_deleted == 0,
            )
            .order_by(PublishRecordModel.id.desc())
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[publish-record:get_latest_processing_record_by_device] result: %s",
            record.id if record else "None",
        )
        return record
