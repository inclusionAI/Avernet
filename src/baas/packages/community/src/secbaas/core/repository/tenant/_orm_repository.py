from secbaas.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.logger import get_logger

from ._orm_model import TenantModel
from ._protocol import TenantRepository

log = get_logger("orm-repository")


class OrmTenantRepository(OrmConnectionMixin, TenantRepository):
    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def insert_tenant(
        self,
        *,
        creator,
        modifier,
        name,
        description=None,
        env="prod",
        extra_config=None,
    ):
        log.info("insert_tenant: name=%s, env=%s", name, env)
        import json

        row = TenantModel(
            creator=creator,
            modifier=modifier,
            name=name,
            description=description,
            env=env,
            extra_config=json.dumps(extra_config, ensure_ascii=False)
            if extra_config
            else None,
        )
        self._session.add(row)
        self._session.flush()
        result = int(row.id)
        log.info("[tenant:insert_tenant] result: id=%s", result)
        return result

    @with_orm_session
    def get_by_id(self, id: int):
        log.info("get_by_id: id=%s", id)
        row = (
            self._session.query(TenantModel)
            .filter(TenantModel.id == id, TenantModel.is_deleted == 0)
            .first()
        )
        record = row.to_record() if row else None
        log.info("[tenant:get_by_id] result: %s", record.id if record else "None")
        return record

    @with_orm_session
    def get_by_name(self, name, env):
        log.info("get_by_name: name=%s, env=%s", name, env)
        row = (
            self._session.query(TenantModel)
            .filter(
                TenantModel.name == name,
                TenantModel.env == env,
                TenantModel.is_deleted == 0,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info("[tenant:get_by_name] result: %s", record.id if record else "None")
        return record

    @with_orm_session
    def update_tenant(
        self, *, name, env, modifier, description=None, extra_config=None
    ):
        log.info("update_tenant: name=%s, env=%s", name, env)
        import json

        values = {
            "modifier": modifier,
            "gmt_modified": __import__("sqlalchemy").func.now(),
        }
        if description is not None:
            values["description"] = description
        if extra_config is not None:
            values["extra_config"] = json.dumps(extra_config, ensure_ascii=False)
        result = (
            self._session.query(TenantModel)
            .filter(
                TenantModel.name == name,
                TenantModel.env == env,
                TenantModel.is_deleted == 0,
            )
            .update(values, synchronize_session=False)
        )
        result = int(result)
        log.info("[tenant:update_tenant] result: %s rows", result)
        return result

    @with_orm_session
    def soft_delete(self, *, name, env, modifier):
        log.info("soft_delete: name=%s, env=%s", name, env)
        row = (
            self._session.query(TenantModel)
            .filter(
                TenantModel.name == name,
                TenantModel.env == env,
                TenantModel.is_deleted == 0,
            )
            .first()
        )
        if row is None:
            log.info("[tenant:soft_delete] result: not found")
            return
        record_id = row.id
        self._session.query(TenantModel).filter(
            TenantModel.id == record_id, TenantModel.is_deleted == 0
        ).update(
            {
                "is_deleted": record_id,
                "modifier": modifier,
                "gmt_modified": __import__("sqlalchemy").func.now(),
            },
            synchronize_session=False,
        )
        log.info("[tenant:soft_delete] result: done")

    @with_orm_session
    def list_tenants(self, *, env, page=1, page_size=20):
        log.info("list_tenants: env=%s, page=%s", env, page)
        from sqlalchemy import func

        total = (
            self._session.query(func.count(TenantModel.id))
            .filter(TenantModel.env == env, TenantModel.is_deleted == 0)
            .scalar()
        )
        offset = (page - 1) * page_size
        rows = (
            self._session.query(TenantModel)
            .filter(TenantModel.env == env, TenantModel.is_deleted == 0)
            .order_by(TenantModel.id.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )
        items = [r.to_record() for r in rows]
        log.info("[tenant:list_tenants] result: %s rows", len(items))
        return total, items
