"""通用配置服务层。"""

from __future__ import annotations

import json
from typing import Any

from injector import inject

from agentclaw.community.core.common_config.models import CommonConfigRecord
from agentclaw.community.core.common_config.repository import CommonConfigRepositoryProtocol
from agentclaw.community.log import get_logger


logger = get_logger()


class CommonConfigService:
    """基于 ``ac_common_config`` 的通用配置管理服务。"""

    @inject
    def __init__(self, repository: CommonConfigRepositoryProtocol) -> None:
        self._repo = repository

    @staticmethod
    def _parse_value(value: str | None) -> Any:
        if value is None:
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value

    @staticmethod
    def _serialize_value(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    def _to_dict(self, record: CommonConfigRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "business_code": record.business_code,
            "business_name": record.business_name,
            "param_code": record.param_code,
            "param_name": record.param_name,
            "param_value": self._parse_value(record.param_value),
            "enable": record.enable,
            "ext_info": self._parse_value(record.ext_info),
            "env": record.env,
            "gmt_create": record.gmt_create.isoformat() if record.gmt_create else None,
            "gmt_modified": record.gmt_modified.isoformat()
            if record.gmt_modified
            else None,
        }

    @staticmethod
    def _normalize_enable(enable: str) -> str:
        if enable not in ("0", "1"):
            raise ValueError("enable 只允许取值 '0' 或 '1'")
        return enable

    @staticmethod
    def _validate_page(page_num: int, page_size: int) -> tuple[int, int]:
        if page_num < 1:
            raise ValueError("page_num must be >= 1")
        if page_size < 1:
            raise ValueError("page_size must be >= 1")
        return page_num, min(page_size, 500)

    def get_config(
        self,
        *,
        business_code: str,
        param_code: str,
        env: str,
        only_enabled: bool = False,
    ) -> dict[str, Any] | None:
        record = self._repo.get_by_biz_param(
            business_code=business_code, param_code=param_code, env=env
        )
        if record is None:
            return None
        if only_enabled and record.enable != "1":
            return None
        return self._to_dict(record)

    def get_value(
        self,
        *,
        business_code: str,
        param_code: str,
        env: str,
        default: Any = None,
        only_enabled: bool = True,
    ) -> Any:
        record = self._repo.get_by_biz_param(
            business_code=business_code, param_code=param_code, env=env
        )
        if record is None:
            return default
        if only_enabled and record.enable != "1":
            return default
        return self._parse_value(record.param_value)

    def list_configs(
        self,
        *,
        env: str,
        business_code: str | None = None,
        enable: str | None = None,
        keyword: str | None = None,
        page_num: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        if enable is not None:
            enable = self._normalize_enable(enable)
        page_num, page_size = self._validate_page(page_num, page_size)
        offset = (page_num - 1) * page_size
        total = self._repo.count_configs(
            env=env, business_code=business_code, enable=enable, keyword=keyword
        )
        records = self._repo.list_configs(
            env=env,
            business_code=business_code,
            enable=enable,
            keyword=keyword,
            offset=offset,
            limit=page_size,
        )
        return {
            "total": total,
            "page_num": page_num,
            "page_size": page_size,
            "items": [self._to_dict(record) for record in records],
        }

    def create_config(
        self,
        *,
        business_code: str,
        param_name: str,
        param_value: Any,
        business_name: str | None = None,
        param_code: str,
        enable: str = "1",
        ext_info: Any = None,
        env: str,
    ) -> int:
        enable = self._normalize_enable(enable)
        config_id = self._repo.create_config(
            business_code=business_code,
            business_name=business_name,
            param_code=param_code,
            param_name=param_name,
            param_value=self._serialize_value(param_value),
            enable=enable,
            ext_info=self._serialize_value(ext_info),
            env=env,
        )
        logger.info(
            "[common_config.create] business_code=%s param_code=%s env=%s id=%s",
            business_code,
            param_code,
            env,
            config_id,
        )
        return config_id

    def update_config(self, *, config_id: int, updates: dict[str, Any]) -> bool:
        serialized: dict[str, Any] = {}
        for key, value in updates.items():
            if key in ("param_value", "ext_info"):
                serialized[key] = self._serialize_value(value)
            elif key == "enable":
                serialized[key] = self._normalize_enable(value)
            else:
                serialized[key] = value
        return self._repo.update_config(config_id=config_id, updates=serialized)

    def upsert_config(
        self,
        *,
        business_code: str,
        param_name: str,
        param_value: Any,
        business_name: str | None = None,
        param_code: str,
        enable: str = "1",
        ext_info: Any = None,
        env: str,
    ) -> int:
        enable = self._normalize_enable(enable)
        config_id = self._repo.upsert_config(
            business_code=business_code,
            business_name=business_name,
            param_code=param_code,
            param_name=param_name,
            param_value=self._serialize_value(param_value),
            enable=enable,
            ext_info=self._serialize_value(ext_info),
            env=env,
        )
        logger.info(
            "[common_config.upsert] business_code=%s param_code=%s env=%s id=%s",
            business_code,
            param_code,
            env,
            config_id,
        )
        return config_id

    def delete_config(
        self,
        *,
        config_id: int | None = None,
        business_code: str | None = None,
        param_code: str | None = None,
        env: str | None = None,
    ) -> bool:
        if config_id is not None:
            return self._repo.delete_config(config_id=config_id)
        if business_code and param_code and env:
            return self._repo.delete_by_biz_param(
                business_code=business_code, param_code=param_code, env=env
            )
        raise ValueError("删除配置需要 config_id 或 business_code + param_code + env")

    def enable_config(self, *, config_id: int) -> bool:
        return self.update_config(config_id=config_id, updates={"enable": "1"})

    def disable_config(self, *, config_id: int) -> bool:
        return self.update_config(config_id=config_id, updates={"enable": "0"})
