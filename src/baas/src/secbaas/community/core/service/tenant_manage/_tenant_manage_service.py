"""
Default implementation of TenantManageService.

Moved from domain/service/tenant_service.py as part of Phase 3 refactoring.
"""

from __future__ import annotations

from typing import Any

from secbaas.community.api.tenant_manage import (
    TenantConfig,
    TenantCreate,
    TenantListResponse,
    TenantManageService,
    TenantResponse,
    TenantUpdate,
)
from secbaas.community.core.repository.tenant import (
    TenantRecord,
    TenantRepository,
)
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

logger = get_logger("core-service")


def _record_to_response(record: TenantRecord) -> TenantResponse:
    """将 TenantRecord 转换为 TenantResponse"""
    extra_config = None
    if record.extra_config:
        extra_config = TenantConfig.model_validate(record.extra_config)
    return TenantResponse(
        name=record.name,
        description=record.description,
        env=record.env,
        extra_config=extra_config,
        creator=record.creator,
        modifier=record.modifier,
        gmt_create=record.gmt_create,
        gmt_modified=record.gmt_modified,
    )


class DefaultTenantManageService(TenantManageService):
    """租户管理业务服务实现"""

    def __init__(self, tenant_repository: TenantRepository) -> None:
        self._tenant_repository = tenant_repository

    def create_tenant(self, data: TenantCreate) -> TenantResponse:
        """创建租户"""
        env = get_current_env()
        logger.info(f"Creating tenant: name={data.name}, env={env}")

        operator = data.operator or "system"

        extra_config_dict: dict[str, Any] | None = None
        if data.extra_config:
            extra_config_dict = data.extra_config.model_dump(exclude_none=True)

        record_id = self._tenant_repository.insert_tenant(
            creator=operator,
            modifier=operator,
            name=data.name,
            description=data.description,
            env=env,
            extra_config=extra_config_dict,
        )

        logger.info(f"Tenant created successfully: record_id={record_id}")

        record = self._tenant_repository.get_by_id(record_id)
        if record is None:
            raise RuntimeError(f"Tenant record not found after insert: id={record_id}")
        return _record_to_response(record)

    def get_tenant_by_name(self, name: str) -> TenantResponse | None:
        """根据租户名称获取租户信息"""
        env = get_current_env()
        logger.info(f"Getting tenant by name: {name}, env: {env}")

        record = self._tenant_repository.get_by_name(name, env)

        if record:
            return _record_to_response(record)
        return None

    def get_tenant_config(self, name: str) -> TenantConfig | None:
        """获取租户的LLM配置 (extra_config)"""
        env = get_current_env()
        logger.info(f"Getting tenant config: name={name}, env={env}")

        record = self._tenant_repository.get_by_name(name, env)

        if record and record.extra_config:
            return TenantConfig.model_validate(record.extra_config)
        return None

    def update_tenant(self, name: str, data: TenantUpdate) -> TenantResponse | None:
        """更新租户信息"""
        env = get_current_env()
        logger.info(f"Updating tenant: name={name}, env={env}")

        record = self._tenant_repository.get_by_name(name, env)

        if not record:
            return None

        update_fields: dict[str, Any] = {}
        if data.description is not None:
            update_fields["description"] = data.description
        if data.extra_config is not None:
            update_fields["extra_config"] = data.extra_config.model_dump(
                exclude_none=True
            )

        if not update_fields:
            return _record_to_response(record)

        operator = data.operator or "system"
        self._tenant_repository.update_tenant(
            name=name, env=env, modifier=operator, **update_fields
        )

        logger.info(f"Tenant {name} updated successfully")

        record = self._tenant_repository.get_by_name(name, env)
        if record is None:
            raise RuntimeError(f"Tenant record not found after update: name={name}")
        return _record_to_response(record)

    def list_tenants(self, page: int = 1, page_size: int = 20) -> TenantListResponse:
        """获取租户列表"""
        env = get_current_env()
        logger.info(f"Listing tenants: env={env}, page={page}, page_size={page_size}")

        total, records = self._tenant_repository.list_tenants(
            env=env, page=page, page_size=page_size
        )

        items = [_record_to_response(r) for r in records]
        return TenantListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )

    def soft_delete_tenant(self, name: str, operator: str) -> bool:
        """软删除租户"""
        env = get_current_env()
        logger.info(f"Soft deleting tenant: name={name}, env={env}")

        record = self._tenant_repository.get_by_name(name, env)

        if not record:
            return False

        self._tenant_repository.soft_delete(name=name, env=env, modifier=operator)
        logger.info(f"Tenant {name} soft deleted successfully")
        return True
