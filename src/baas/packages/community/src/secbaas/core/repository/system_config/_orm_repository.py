from secbaas.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.logger import get_logger

from ._orm_model import SystemConfigModel
from ._protocol import SystemConfigRepository
from ._record import SystemConfigRecord

log = get_logger("orm-repository")


class OrmSystemConfigRepository(OrmConnectionMixin, SystemConfigRepository):
    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def insert_config(
        self,
        *,
        conf_key: str,
        conf_value: str | None,
        env: str,
        name: str,
        description: str | None = None,
        creator: str,
        modifier: str,
    ) -> int:
        log.info("insert_config: conf_key=%s, env=%s, name=%s", conf_key, env, name)
        row = SystemConfigModel(
            conf_key=conf_key,
            conf_value=conf_value,
            env=env,
            name=name,
            description=description,
            creator=creator,
            modifier=modifier,
        )
        self._session.add(row)
        self._session.flush()
        result = int(row.id)
        log.info("[system-config:insert_config] result: id=%s", result)
        return result

    @with_orm_session
    def get_by_id(self, config_id: int) -> SystemConfigRecord | None:
        log.info("get_by_id: config_id=%s", config_id)
        row = (
            self._session.query(SystemConfigModel)
            .filter(SystemConfigModel.id == config_id)
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[system-config:get_by_id] result: %s", record.id if record else "None"
        )
        return record

    @with_orm_session
    def get_by_env_and_key(self, env: str, conf_key: str) -> SystemConfigRecord | None:
        log.info("get_by_env_and_key: env=%s, conf_key=%s", env, conf_key)
        row = (
            self._session.query(SystemConfigModel)
            .filter(
                SystemConfigModel.env == env, SystemConfigModel.conf_key == conf_key
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[system-config:get_by_env_and_key] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def update_config(
        self,
        *,
        config_id: int,
        conf_value: str | None = None,
        name: str | None = None,
        description: str | None = None,
        modifier: str | None = None,
    ) -> int:
        log.info("update_config: config_id=%s", config_id)
        from sqlalchemy import func

        values = {}
        if conf_value is not None:
            values["conf_value"] = conf_value
        if name is not None:
            values["name"] = name
        if description is not None:
            values["description"] = description
        if modifier is not None:
            values["modifier"] = modifier
        if not values:
            return 0
        values["gmt_modified"] = func.now()
        result = (
            self._session.query(SystemConfigModel)
            .filter(SystemConfigModel.id == config_id)
            .update(values, synchronize_session=False)
        )
        result = int(result)
        log.info("[system-config:update_config] result: %s rows", result)
        return result

    @with_orm_session
    def delete_config(self, *, config_id: int) -> int:
        log.info("delete_config: config_id=%s", config_id)
        result = (
            self._session.query(SystemConfigModel)
            .filter(SystemConfigModel.id == config_id)
            .delete(synchronize_session=False)
        )
        result = int(result)
        log.info("[system-config:delete_config] result: %s rows", result)
        return result

    @with_orm_session
    def list_configs(
        self,
        *,
        env: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[SystemConfigRecord]]:
        log.info("list_configs: env=%s, page=%s", env, page)
        from sqlalchemy import func

        query = self._session.query(SystemConfigModel)
        if env is not None:
            query = query.filter(SystemConfigModel.env == env)
        total = query.with_entities(func.count(SystemConfigModel.id)).scalar()
        offset = (page - 1) * page_size
        rows = (
            query.order_by(SystemConfigModel.id.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )
        items = [r.to_record() for r in rows]
        log.info("[system-config:list_configs] result: %s rows", len(items))
        return total, items
