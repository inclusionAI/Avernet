"""Unified common-config repository implementation.

This module is the concrete implementation of ``CommonConfigRepositoryProtocol``
for ``ac_common_config`` and lives with the common_config repository layer.
"""

from __future__ import annotations

from typing import Any

from injector import inject
from sqlalchemy import func, or_

from agentclaw.community.core.common_config.models import CommonConfigRecord
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import CommonConfig
from agentclaw.community.core.repository.protocols.config import CommonConfigRepositoryProtocol


logger = get_logger()

_ALLOWED_UPDATE_FIELDS = {
    "business_name",
    "param_name",
    "param_value",
    "enable",
    "ext_info",
}


class CommonConfigRepository(
    CommonConfigRepositoryProtocol,
):
    """Unified ORM implementation for ``CommonConfigRepositoryProtocol``."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    @staticmethod
    def _to_record(m: CommonConfig | None) -> CommonConfigRecord | None:
        if m is None:
            return None
        return CommonConfigRecord(
            id=m.id,
            business_code=m.business_code,
            business_name=m.business_name,
            param_code=m.param_code,
            param_name=m.param_name,
            param_value=m.param_value,
            enable=m.enable,
            ext_info=m.ext_info,
            env=m.env,
            gmt_create=m.gmt_create,
            gmt_modified=m.gmt_modified,
        )

    @staticmethod
    def _apply_filters(
        query: Any,
        *,
        env: str,
        business_code: str | None = None,
        enable: str | None = None,
        keyword: str | None = None,
    ) -> Any:
        query = query.filter(CommonConfig.env == env)
        if business_code is not None:
            query = query.filter(CommonConfig.business_code == business_code)
        if enable is not None:
            query = query.filter(CommonConfig.enable == enable)
        if keyword:
            pattern = f"%{keyword}%"
            query = query.filter(
                or_(
                    CommonConfig.business_name.like(pattern),
                    CommonConfig.param_code.like(pattern),
                    CommonConfig.param_name.like(pattern),
                )
            )
        return query

    def get_by_id(self, *, config_id: int) -> CommonConfigRecord | None:
        with self._db.orm_session() as db:
            m = db.query(CommonConfig).filter(CommonConfig.id == config_id).first()
            return self._to_record(m)

    def get_by_biz_param(
        self, *, business_code: str, param_code: str, env: str
    ) -> CommonConfigRecord | None:
        with self._db.orm_session() as db:
            m = (
                db.query(CommonConfig)
                .filter(
                    CommonConfig.business_code == business_code,
                    CommonConfig.param_code == param_code,
                    CommonConfig.env == env,
                )
                .first()
            )
            return self._to_record(m)

    def list_configs(
        self,
        *,
        env: str,
        business_code: str | None = None,
        enable: str | None = None,
        keyword: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[CommonConfigRecord]:
        with self._db.orm_session() as db:
            query = self._apply_filters(
                db.query(CommonConfig),
                env=env,
                business_code=business_code,
                enable=enable,
                keyword=keyword,
            )
            models = (
                query.order_by(CommonConfig.business_code, CommonConfig.param_code)
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [self._to_record(m) for m in models]

    def count_configs(
        self,
        *,
        env: str,
        business_code: str | None = None,
        enable: str | None = None,
        keyword: str | None = None,
    ) -> int:
        with self._db.orm_session() as db:
            query = self._apply_filters(
                db.query(CommonConfig),
                env=env,
                business_code=business_code,
                enable=enable,
                keyword=keyword,
            )
            return query.count()

    def create_config(
        self,
        *,
        business_code: str,
        param_name: str,
        param_value: str | None,
        business_name: str | None,
        param_code: str,
        enable: str,
        ext_info: str | None,
        env: str,
    ) -> int:
        with self._db.orm_session() as db:
            m = CommonConfig(
                business_code=business_code,
                business_name=business_name,
                param_code=param_code,
                param_name=param_name,
                param_value=param_value,
                enable=enable,
                ext_info=ext_info,
                env=env,
            )
            db.add(m)
            db.flush()
            config_id = m.id
        return config_id

    def update_config(self, *, config_id: int, updates: dict) -> bool:
        safe_updates = {k: v for k, v in updates.items() if k in _ALLOWED_UPDATE_FIELDS}
        if not safe_updates:
            return False
        with self._db.orm_session() as db:
            rowcount = (
                db.query(CommonConfig)
                .filter(CommonConfig.id == config_id)
                .update(safe_updates, synchronize_session=False)
            )
            return rowcount > 0

    def upsert_config(
        self,
        *,
        business_code: str,
        param_name: str,
        param_value: str | None,
        business_name: str | None,
        param_code: str,
        enable: str,
        ext_info: str | None,
        env: str,
    ) -> int:
        """Atomic upsert on uk_business_param_id_env."""
        with self._db.orm_session() as db:
            dialect = db.get_bind().dialect.name
            table = CommonConfig.__table__
            values = {
                "business_code": business_code,
                "business_name": business_name,
                "param_code": param_code,
                "param_name": param_name,
                "param_value": param_value,
                "enable": enable,
                "ext_info": ext_info,
                "env": env,
            }
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as _insert

                stmt = _insert(table).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["business_code", "param_code", "env"],
                    set_={
                        "business_name": business_name,
                        "param_name": param_name,
                        "param_value": param_value,
                        "enable": enable,
                        "ext_info": ext_info,
                    },
                )
                db.execute(stmt)
                db.flush()
                config_id = (
                    db.query(CommonConfig.id)
                    .filter(
                        CommonConfig.business_code == business_code,
                        CommonConfig.param_code == param_code,
                        CommonConfig.env == env,
                    )
                    .scalar()
                )
            else:
                from sqlalchemy.dialects.mysql import insert as _insert

                stmt = _insert(table).values(**values)
                stmt = stmt.on_duplicate_key_update(
                    id=func.LAST_INSERT_ID(CommonConfig.id),
                    business_name=business_name,
                    param_name=param_name,
                    param_value=param_value,
                    enable=enable,
                    ext_info=ext_info,
                )
                config_id = db.execute(stmt).lastrowid
        return config_id

    def delete_config(self, *, config_id: int) -> bool:
        with self._db.orm_session() as db:
            rowcount = (
                db.query(CommonConfig)
                .filter(CommonConfig.id == config_id)
                .delete(synchronize_session=False)
            )
            return rowcount > 0

    def delete_by_biz_param(
        self, *, business_code: str, param_code: str, env: str
    ) -> bool:
        with self._db.orm_session() as db:
            rowcount = (
                db.query(CommonConfig)
                .filter(
                    CommonConfig.business_code == business_code,
                    CommonConfig.param_code == param_code,
                    CommonConfig.env == env,
                )
                .delete(synchronize_session=False)
            )
            return rowcount > 0
