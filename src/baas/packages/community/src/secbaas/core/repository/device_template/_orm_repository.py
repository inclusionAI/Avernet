import json

from secbaas.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.logger import get_logger

from ._orm_model import DeviceTemplateModel
from ._protocol import DeviceTemplateRepository
from ._record import DeviceTemplateRecord

log = get_logger("orm-repository")


class OrmDeviceTemplateRepository(OrmConnectionMixin, DeviceTemplateRepository):
    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def insert_template(
        self,
        *,
        template_uuid: str,
        template_id: int,
        type: str,
        tenant: str,
        creator: str,
        modifier: str,
        status: str = "CREATED",
        name: str,
        description: str | None = None,
        config: dict | None = None,
    ) -> int:
        log.info(
            "insert_template: template_uuid=%s, type=%s, tenant=%s, name=%s",
            template_uuid,
            type,
            tenant,
            name,
        )
        row = DeviceTemplateModel(
            template_uuid=template_uuid,
            template_id=template_id,
            type=type,
            tenant=tenant,
            creator=creator,
            modifier=modifier,
            status=status,
            name=name,
            description=description,
            config=json.dumps(config, ensure_ascii=False) if config else None,
        )
        self._session.add(row)
        self._session.flush()
        result = int(row.id)
        log.info("[device-template:insert_template] result: id=%s", result)
        return result

    @with_orm_session
    def get_by_id(self, template_id: int, tenant: str) -> DeviceTemplateRecord | None:
        log.info("get_by_id: template_id=%s, tenant=%s", template_id, tenant)
        row = (
            self._session.query(DeviceTemplateModel)
            .filter(
                DeviceTemplateModel.id == template_id,
                DeviceTemplateModel.tenant == tenant,
                DeviceTemplateModel.is_deleted == 0,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[device-template:get_by_id] result: %s", record.id if record else "None"
        )
        return record

    @with_orm_session
    def get_by_template_id(self, template_id: int) -> DeviceTemplateRecord | None:
        log.info("get_by_template_id: template_id=%s", template_id)
        row = (
            self._session.query(DeviceTemplateModel)
            .filter(
                DeviceTemplateModel.template_id == template_id,
                DeviceTemplateModel.is_deleted == 0,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[device-template:get_by_template_id] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def get_by_template_uuid(
        self, template_uuid: str, tenant: str, status: str
    ) -> DeviceTemplateRecord | None:
        log.info(
            "get_by_template_uuid: template_uuid=%s, tenant=%s, status=%s",
            template_uuid,
            tenant,
            status,
        )
        row = (
            self._session.query(DeviceTemplateModel)
            .filter(
                DeviceTemplateModel.template_uuid == template_uuid,
                DeviceTemplateModel.tenant == tenant,
                DeviceTemplateModel.status == status,
                DeviceTemplateModel.is_deleted == 0,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[device-template:get_by_template_uuid] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def list_by_template_uuid(
        self, template_uuid: str, tenant: str
    ) -> list[DeviceTemplateRecord]:
        log.info(
            "list_by_template_uuid: template_uuid=%s, tenant=%s", template_uuid, tenant
        )
        rows = (
            self._session.query(DeviceTemplateModel)
            .filter(
                DeviceTemplateModel.template_uuid == template_uuid,
                DeviceTemplateModel.tenant == tenant,
                DeviceTemplateModel.is_deleted == 0,
            )
            .order_by(DeviceTemplateModel.id.desc())
            .all()
        )
        items = [r.to_record() for r in rows]
        log.info("[device-template:list_by_template_uuid] result: %s rows", len(items))
        return items

    @with_orm_session
    def get_online_by_template_uuid(
        self, template_uuid: str, tenant: str
    ) -> DeviceTemplateRecord | None:
        log.info(
            "get_online_by_template_uuid: template_uuid=%s, tenant=%s",
            template_uuid,
            tenant,
        )
        row = (
            self._session.query(DeviceTemplateModel)
            .filter(
                DeviceTemplateModel.template_uuid == template_uuid,
                DeviceTemplateModel.tenant == tenant,
                DeviceTemplateModel.status == "ONLINE",
                DeviceTemplateModel.is_deleted == 0,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[device-template:get_online_by_template_uuid] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def update_template(
        self,
        *,
        template_uuid: str,
        tenant: str,
        status: str,
        modifier: str,
        name: str | None = None,
        description: str | None = None,
        config: dict | None = None,
    ) -> int:
        log.info(
            "update_template: template_uuid=%s, tenant=%s, status=%s",
            template_uuid,
            tenant,
            status,
        )
        from sqlalchemy import func

        values = {"modifier": modifier, "gmt_modified": func.now()}
        if name is not None:
            values["name"] = name
        if description is not None:
            values["description"] = description
        if config is not None:
            values["config"] = json.dumps(config, ensure_ascii=False)
        result = (
            self._session.query(DeviceTemplateModel)
            .filter(
                DeviceTemplateModel.tenant == tenant,
                DeviceTemplateModel.template_uuid == template_uuid,
                DeviceTemplateModel.status == status,
                DeviceTemplateModel.is_deleted == 0,
            )
            .update(values, synchronize_session=False)
        )
        result = int(result)
        log.info("[device-template:update_template] result: %s rows", result)
        return result

    @with_orm_session
    def update_status(
        self, *, template_uuid: str, tenant: str, current_status: str, new_status: str
    ) -> None:
        log.info(
            "update_status: template_uuid=%s, tenant=%s, current_status=%s, new_status=%s",
            template_uuid,
            tenant,
            current_status,
            new_status,
        )
        from sqlalchemy import func

        self._session.query(DeviceTemplateModel).filter(
            DeviceTemplateModel.template_uuid == template_uuid,
            DeviceTemplateModel.tenant == tenant,
            DeviceTemplateModel.status == current_status,
            DeviceTemplateModel.is_deleted == 0,
        ).update(
            {
                "status": new_status,
                "gmt_modified": func.now(),
            },
            synchronize_session=False,
        )
        log.info("[device-template:update_status] result: done")

    @with_orm_session
    def soft_delete(
        self, *, template_uuid: str, tenant: str, status: str, modifier: str
    ) -> None:
        log.info(
            "soft_delete: template_uuid=%s, tenant=%s, status=%s",
            template_uuid,
            tenant,
            status,
        )
        from sqlalchemy import func

        row = (
            self._session.query(DeviceTemplateModel)
            .filter(
                DeviceTemplateModel.template_uuid == template_uuid,
                DeviceTemplateModel.tenant == tenant,
                DeviceTemplateModel.status == status,
                DeviceTemplateModel.is_deleted == 0,
            )
            .first()
        )
        if row is None:
            log.info("[device-template:soft_delete] result: not found")
            return
        record_id = row.id
        self._session.query(DeviceTemplateModel).filter(
            DeviceTemplateModel.id == record_id,
            DeviceTemplateModel.is_deleted == 0,
        ).update(
            {
                "is_deleted": record_id,
                "modifier": modifier,
                "gmt_modified": func.now(),
            },
            synchronize_session=False,
        )
        log.info("[device-template:soft_delete] result: done")

    @with_orm_session
    def get_default_local_template_id(self) -> int | None:
        log.info("get_default_local_template_id")
        row = (
            self._session.query(DeviceTemplateModel.template_id)
            .filter(
                DeviceTemplateModel.type == "Local",
                DeviceTemplateModel.status == "ONLINE",
                DeviceTemplateModel.is_deleted == 0,
            )
            .order_by(DeviceTemplateModel.template_id.asc())
            .first()
        )
        result = row.template_id if row else None
        log.info("[device-template:get_default_local_template_id] result: %s", result)
        return result

    @with_orm_session
    def list_templates(
        self,
        *,
        tenant: str,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[DeviceTemplateRecord]]:
        log.info("list_templates: tenant=%s, status=%s, page=%s", tenant, status, page)
        from sqlalchemy import func

        query = self._session.query(DeviceTemplateModel).filter(
            DeviceTemplateModel.is_deleted == 0,
            DeviceTemplateModel.tenant == tenant,
        )
        if status is not None:
            query = query.filter(DeviceTemplateModel.status == status)
        total = query.with_entities(func.count(DeviceTemplateModel.id)).scalar()
        offset = (page - 1) * page_size
        rows = (
            query.order_by(DeviceTemplateModel.id.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )
        items = [r.to_record() for r in rows]
        log.info("[device-template:list_templates] result: %s rows", len(items))
        return total, items
