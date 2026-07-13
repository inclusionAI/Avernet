"""ORM-based API key repository for baas_api_key table."""

from typing import Any

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.logger import get_logger

from ._orm_model import APIKeyModel
from ._protocol import APIKeyRepository
from ._record import APIKeyRecord

log = get_logger("orm-repository")


class OrmAPIKeyRepository(OrmConnectionMixin, APIKeyRepository):
    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def insert(
        self,
        *,
        api_key_hash: str,
        api_key_prefix: str,
        key_name: str | None,
        app_id: str,
        app_type: str | None,
        description: str | None,
        rate_limit_rpm: int | None,
        rate_limit_rpd: int | None,
        status: str,
        owner: str,
        tenant: str | None,
        env: str,
        creator: str,
        policy: str | None,
    ) -> int:
        log.info(
            "insert: api_key_prefix=%s, app_id=%s, tenant=%s, env=%s",
            api_key_prefix,
            app_id,
            tenant,
            env,
        )
        row = APIKeyModel(
            api_key_hash=api_key_hash,
            api_key_prefix=api_key_prefix,
            key_name=key_name,
            app_id=app_id,
            app_type=app_type,
            description=description,
            rate_limit_rpm=rate_limit_rpm,
            rate_limit_rpd=rate_limit_rpd,
            status=status,
            owner=owner,
            tenant=tenant,
            env=env,
            creator=creator,
            modifier=creator,
            policy=policy,
        )
        self._session.add(row)
        self._session.flush()
        result = int(row.id)
        log.info("[api-gateway:insert] result: id=%s", result)
        return result

    @with_orm_session
    def get_by_id(self, key_id: int) -> APIKeyRecord | None:
        log.info("get_by_id: key_id=%s", key_id)
        row = (
            self._session.query(APIKeyModel)
            .filter(
                APIKeyModel.id == key_id,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info("[api-gateway:get_by_id] result: %s", record.id if record else "None")
        return record

    @with_orm_session
    def get_by_prefix(self, prefix: str) -> APIKeyRecord | None:
        log.info("get_by_prefix: prefix=%s", prefix)
        row = (
            self._session.query(APIKeyModel)
            .filter(
                APIKeyModel.api_key_prefix == prefix,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[api-gateway:get_by_prefix] result: %s", record.id if record else "None"
        )
        return record

    @with_orm_session
    def get_by_prefix_and_status(
        self, prefix: str, status: str, env: str | None = None
    ) -> APIKeyRecord | None:
        log.info(
            "get_by_prefix_and_status: prefix=%s, status=%s, env=%s",
            prefix,
            status,
            env,
        )
        query = self._session.query(APIKeyModel).filter(
            APIKeyModel.api_key_prefix == prefix,
            APIKeyModel.status == status,
        )
        # env 过滤：共享 DB 下钉死当前环境，避免跨环境 API Key 互认。
        if env is not None:
            query = query.filter(APIKeyModel.env == env)
        row = query.first()
        record = row.to_record() if row else None
        log.info(
            "[api-gateway:get_by_prefix_and_status] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def list_keys(
        self,
        *,
        app_id: str | None = None,
        app_type: str | None = None,
        status: str | None = None,
        creator: str | None = None,
        owner: str | None = None,
        tenant: str | None = None,
        env: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[APIKeyRecord]]:
        log.info(
            "list_keys: app_id=%s, app_type=%s, status=%s, owner=%s, tenant=%s, page=%s",
            app_id,
            app_type,
            status,
            owner,
            tenant,
            page,
        )
        from sqlalchemy import func as sa_func

        query = self._session.query(APIKeyModel)
        if app_id is not None:
            query = query.filter(APIKeyModel.app_id == app_id)
        if app_type is not None:
            query = query.filter(APIKeyModel.app_type == app_type)
        if status is not None:
            query = query.filter(APIKeyModel.status == status)
        if creator is not None:
            query = query.filter(APIKeyModel.creator == creator)
        if owner is not None:
            query = query.filter(APIKeyModel.owner == owner)
        if tenant is not None:
            query = query.filter(APIKeyModel.tenant == tenant)
        if env is not None:
            query = query.filter(APIKeyModel.env == env)

        total = query.with_entities(sa_func.count(APIKeyModel.id)).scalar()
        offset = (page - 1) * page_size
        rows = (
            query.order_by(APIKeyModel.id.desc()).offset(offset).limit(page_size).all()
        )
        items = [r.to_record() for r in rows]
        log.info("[api-gateway:list_keys] result: %s rows", len(items))
        return total, items

    @with_orm_session
    def update(
        self,
        key_id: int,
        *,
        key_name: str | None = None,
        description: str | None = None,
        app_id: str | None = None,
        app_type: str | None = None,
        rate_limit_rpm: int | None = None,
        rate_limit_rpd: int | None = None,
        owner: str | None = None,
        tenant: str | None = None,
        modifier: str | None = None,
        policy: str | None = None,
    ) -> None:
        log.info("update: key_id=%s", key_id)
        values: dict[str, Any] = {}
        if key_name is not None:
            values["key_name"] = key_name
        if description is not None:
            values["description"] = description
        if app_id is not None:
            values["app_id"] = app_id
        if app_type is not None:
            values["app_type"] = app_type
        if rate_limit_rpm is not None:
            values["rate_limit_rpm"] = rate_limit_rpm
        if rate_limit_rpd is not None:
            values["rate_limit_rpd"] = rate_limit_rpd
        if owner is not None:
            values["owner"] = owner
        if tenant is not None:
            values["tenant"] = tenant
        if modifier is not None:
            values["modifier"] = modifier
        if policy is not None:
            values["policy"] = policy

        if not values:
            log.info("[api-gateway:update] result: no changes")
            return
        from sqlalchemy import func

        values["gmt_modified"] = func.now()
        self._session.query(APIKeyModel).filter(
            APIKeyModel.id == key_id,
        ).update(values, synchronize_session=False)
        log.info("[api-gateway:update] result: done")

    @with_orm_session
    def update_status(
        self,
        key_id: int,
        status: str,
        modifier: str | None = None,
    ) -> None:
        log.info("update_status: key_id=%s, status=%s", key_id, status)
        from sqlalchemy import func

        values: dict[str, Any] = {"status": status, "gmt_modified": func.now()}
        if modifier is not None:
            values["modifier"] = modifier
        self._session.query(APIKeyModel).filter(
            APIKeyModel.id == key_id,
        ).update(values, synchronize_session=False)
        log.info("[api-gateway:update_status] result: done")

    @with_orm_session
    def exists_prefix(self, prefix: str) -> bool:
        log.info("exists_prefix: prefix=%s", prefix)
        from sqlalchemy import func as sa_func

        count = (
            self._session.query(sa_func.count(APIKeyModel.id))
            .filter(
                APIKeyModel.api_key_prefix == prefix,
            )
            .scalar()
        )
        result = bool(count)
        log.info("[api-gateway:exists_prefix] result: %s", result)
        return result
