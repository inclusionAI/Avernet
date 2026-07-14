"""
Default implementation of DeviceTemplateManageService.

Moved from domain/service/device_template_service.py as part of Phase 3 refactoring.
"""

from typing import Any
from uuid import uuid4

from secbaas.community.api.template_manage import (
    ArcaTemplateConfig,
    DeviceTemplateManageService,
    DeviceTemplateResponse,
    DockerTemplateConfig,
    K8sTemplateConfig,
    LocalTemplateConfig,
    PoolabTemplateConfig,
    SigmaTemplateConfig,
    TeClawTemplateConfig,
    TemplateCreate,
    TemplateListResponse,
    TemplateStatus,
    TemplateUpdate,
)
from secbaas.community.api.tenant_manage import TenantManageService
from secbaas.community.core.repository.device_template import (
    DeviceTemplateRepository,
)
from secbaas.community.core.utils.secret_utils import common_sm4_encrypt
from secbaas.community.logger import get_logger
from secbaas.community.spi.secret import SecretStorePlugin

logger = get_logger("core-service")


def _ensure_api_key_encrypted(config: ArcaTemplateConfig | None, key_b64: str) -> None:
    """Encrypt api_key in-place before DB persistence (forced, regardless of caller flag).

    This function guarantees that any non-empty api_key is always SM4-encrypted
    before being written to the database. The caller's ``encrypt_api_key`` flag is
    ignored for the write decision — encryption is mandatory for all ARCA templates
    with a non-empty api_key.

    Called during template creation/update before persistence.
    Modifies config in-place (api_key becomes encrypted, encrypt_api_key set to True).

    Guard clauses (processed in order):
    1. config is None or not ArcaTemplateConfig → return immediately (no-op)
    2. config.api_key is falsy (empty/None) → return immediately (nothing to encrypt)
    3. config.encrypt_api_key is already True → return immediately (already encrypted;
       prevents double-encryption in read-modify-write scenarios)

    After all three guards pass: encrypt config.api_key via SM4 and set
    encrypt_api_key = True unconditionally.

    Args:
        config: Template config to encrypt in-place.
        key_b64: Base64-encoded SM4 key.
    """
    if config is None or not isinstance(config, ArcaTemplateConfig):
        return
    if not config.api_key:
        return
    if config.encrypt_api_key:
        return
    config.api_key = common_sm4_encrypt(config.api_key, key_b64)
    config.encrypt_api_key = True


def _encrypt_tenant_token_if_needed(
    config: PoolabTemplateConfig | None, key_b64: str
) -> None:
    """Encrypt poolab_tenant_token immediately if encrypt_tenant_token flag is True.

    Called during template creation/update before persistence.
    Modifies config in-place (tenant_token becomes encrypted, flag stays True).

    Args:
        config: Template config to encrypt in-place.
        key_b64: Base64-encoded SM4 key. If None, encryption is skipped.
    """
    if config is None or not isinstance(config, PoolabTemplateConfig):
        return
    if config.encrypt_tenant_token and config.poolab_tenant_token:
        config.poolab_tenant_token = common_sm4_encrypt(
            config.poolab_tenant_token, key_b64
        )


def _record_to_response(record: Any) -> DeviceTemplateResponse:
    """将 DeviceTemplateRecord 转换为 DeviceTemplateResponse"""
    # Deserialize config dict to appropriate config type based on record.type
    config: (
        ArcaTemplateConfig
        | DockerTemplateConfig
        | SigmaTemplateConfig
        | LocalTemplateConfig
        | PoolabTemplateConfig
        | TeClawTemplateConfig
        | K8sTemplateConfig
        | None
    ) = None
    if record.config:
        # Normalize type for case-insensitive matching
        config_dict = dict(record.config)
        raw_type = config_dict.get("type", "ARCA")
        config_type_upper = raw_type.upper()
        if config_type_upper == "SIGMA":
            config_dict["type"] = "Sigma"
            config = SigmaTemplateConfig.model_validate(config_dict)
        elif config_type_upper == "LOCAL":
            config_dict["type"] = "LOCAL"
            config = LocalTemplateConfig.model_validate(config_dict)
        elif config_type_upper == "POOLAB":
            config_dict["type"] = "POOLAB"
            config = PoolabTemplateConfig.model_validate(config_dict)
        elif config_type_upper == "TECLAW":
            config_dict["type"] = "TECLAW"
            config = TeClawTemplateConfig.model_validate(config_dict)
        elif config_type_upper == "K8S":
            config_dict["type"] = "K8s"
            config = K8sTemplateConfig.model_validate(config_dict)
        elif config_type_upper == "DOCKER":
            config_dict["type"] = "DOCKER"
            config = DockerTemplateConfig.model_validate(config_dict)
        else:
            config_dict["type"] = "ARCA"
            # Backward compat: fill missing required fields for old DB records
            config_dict.setdefault("api_key", "")
            config_dict.setdefault("base_url", "")
            config = ArcaTemplateConfig.model_validate(config_dict)
    return DeviceTemplateResponse(
        id=record.id,
        template_id=record.template_id,
        type=record.type,
        template_uuid=record.template_uuid,
        tenant=record.tenant,
        name=record.name,
        description=record.description,
        status=record.status,
        config=config,
        creator=record.creator,
        modifier=record.modifier,
        gmt_create=record.gmt_create,
        gmt_modified=record.gmt_modified,
    )


class DefaultDeviceTemplateService(DeviceTemplateManageService):
    """设备模板业务服务"""

    def __init__(
        self,
        repository: DeviceTemplateRepository,
        tenant_service: TenantManageService,
        secret_plugin: SecretStorePlugin,
    ) -> None:
        self._repository = repository
        self._tenant_service = tenant_service
        self._secret_plugin = secret_plugin

    def create_template(
        self, tenant: str, data: TemplateCreate
    ) -> DeviceTemplateResponse:
        """创建设备模板 (默认状态为 CREATED)"""
        # Auto-generate template_uuid if not provided (format: TEMPLATE-{uuid})
        template_uuid = data.template_uuid
        if template_uuid is None:
            template_uuid = f"TEMPLATE-{uuid4().hex}"

        logger.info(
            f"Creating template: tenant={tenant}, uuid={template_uuid}, name={data.name}"
        )

        # Always encrypt api_key for storage (forced, regardless of caller flag)
        if data.config and isinstance(data.config, ArcaTemplateConfig):
            key = self._secret_plugin.resolve_common_sm4_key()
            _ensure_api_key_encrypted(data.config, key)
        if data.config and isinstance(data.config, PoolabTemplateConfig):
            key = self._secret_plugin.resolve_common_sm4_key()
            _encrypt_tenant_token_if_needed(data.config, key)

        repo = self._repository

        # Check template_id uniqueness (global unique constraint)
        existing = repo.get_by_template_id(data.template_id)
        if existing:
            raise ValueError(
                f"Template with template_id={data.template_id} already exists "
                f"(tenant={existing.tenant}, uuid={existing.template_uuid})"
            )

        record_id = repo.insert_template(
            template_uuid=template_uuid,
            template_id=data.template_id,
            type=data.type.value,
            tenant=tenant,
            creator=data.operator,
            modifier=data.operator,  # Initial creation: creator = modifier
            status=TemplateStatus.CREATED.value,  # Default status
            name=data.name,
            description=data.description,
            config=data.config.model_dump(exclude_none=True) if data.config else {},
        )

        logger.info(f"Template created successfully: record_id={record_id}")

        # Query back to get complete record
        record = repo.get_by_id(record_id, tenant)
        return _record_to_response(record)

    def get_by_template_id(self, template_id: int) -> DeviceTemplateResponse | None:
        """根据template_id（PaaS平台租户业务ID）获取设备模板

        Note: template_id 是全局唯一的，不需要 tenant 参数。
        """
        logger.info(f"Getting template by template_id: {template_id}")

        repo = self._repository
        record = repo.get_by_template_id(template_id)

        if record:
            return _record_to_response(record)
        return None

    def get_default_or_explicit_template(
        self,
        tenant: str,
        template_uuid: str | None = None,
    ) -> DeviceTemplateResponse:
        """获取模板：优先使用显式template_uuid，否则使用租户配置的默认模板

        Two-tier lookup strategy per D-02:
        1. If template_uuid provided: look up specific template
        2. Otherwise: get default_template_uuid from tenant.extra_config

        Args:
            tenant: Tenant name
            template_uuid: Optional explicit template UUID

        Returns:
            DeviceTemplateResponse for resolved template

        Raises:
            ValueError: If template not found or template doesn't belong to tenant
        """
        repo = self._repository

        # Tier 1: Use explicit template_uuid if provided
        if template_uuid:
            logger.info(
                f"Using explicit template_uuid: {template_uuid}, tenant={tenant}"
            )
            record = repo.get_online_by_template_uuid(template_uuid, tenant)
            if not record:
                raise ValueError(
                    f"Template not found: uuid={template_uuid}, tenant={tenant}"
                )
            # Security validation: template must belong to tenant
            if record.tenant != tenant:
                raise ValueError(
                    f"Template {template_uuid} does not belong to tenant {tenant}"
                )
            return _record_to_response(record)

        # Tier 2: Look up default template from tenant config
        logger.info(f"Looking up default template for tenant: {tenant}")
        tenant_obj = self._tenant_service.get_tenant_by_name(tenant)
        if not tenant_obj or not tenant_obj.extra_config:
            raise ValueError(f"Tenant not found or has no config: {tenant}")

        default_template_uuid = tenant_obj.extra_config.default_template_uuid
        if not default_template_uuid:
            raise ValueError(
                f"No default_template_uuid configured for tenant: {tenant}"
            )

        record = repo.get_online_by_template_uuid(default_template_uuid, tenant)
        if not record:
            raise ValueError(
                f"Default template not found: uuid={default_template_uuid}, tenant={tenant}"
            )

        return _record_to_response(record)

    def get_online_template_by_uuid(
        self, tenant: str, template_uuid: str
    ) -> DeviceTemplateResponse | None:
        """根据UUID获取设备模板（带租户隔离）"""
        logger.info(f"Getting template by uuid: {template_uuid}, tenant={tenant}")

        repo = self._repository
        record = repo.get_online_by_template_uuid(
            template_uuid=template_uuid, tenant=tenant
        )

        if record:
            return _record_to_response(record)
        return None

    def update_template(
        self,
        tenant: str,
        template_uuid: str,
        status: TemplateStatus,
        data: TemplateUpdate,
    ) -> DeviceTemplateResponse | None:
        """更新指定 UUID 和状态的设备模板信息"""
        logger.info(
            f"Updating template: uuid={template_uuid}, status={status}, tenant={tenant}"
        )

        repo = self._repository
        # Use composite key (tenant, template_uuid, status) for lookup
        record = repo.get_by_template_uuid(template_uuid, tenant, status.value)
        if not record:
            logger.warning(
                f"Template not found: uuid={template_uuid}, status={status}, tenant={tenant}"
            )
            return None

        # Always encrypt api_key for storage (forced, regardless of caller flag)
        if data.config and isinstance(data.config, ArcaTemplateConfig):
            key = self._secret_plugin.resolve_common_sm4_key()
            _ensure_api_key_encrypted(data.config, key)
        if data.config and isinstance(data.config, PoolabTemplateConfig):
            key = self._secret_plugin.resolve_common_sm4_key()
            _encrypt_tenant_token_if_needed(data.config, key)

        # Build update fields
        update_fields: dict[str, Any] = {}
        if data.name is not None:
            update_fields["name"] = data.name
        if data.description is not None:
            update_fields["description"] = data.description
        if data.config is not None:
            update_fields["config"] = data.config.model_dump(exclude_none=True)

        if not update_fields:
            return _record_to_response(record)

        repo.update_template(
            template_uuid=template_uuid,
            tenant=tenant,
            status=status.value,
            modifier=data.operator,
            **update_fields,
        )

        logger.info(f"Template {template_uuid} updated successfully")

        # Return updated record by re-querying
        record = repo.get_by_template_uuid(template_uuid, tenant, status.value)
        return _record_to_response(record) if record else None

    def update_status(
        self,
        tenant: str,
        template_uuid: str,
        current_status: TemplateStatus,
        new_status: TemplateStatus,
    ) -> DeviceTemplateResponse | None:
        """更新模板状态 (CREATED -> AUDITED -> ONLINE <-> OFFLINE)

        Args:
            tenant: 租户名
            template_uuid: 模板 UUID
            current_status: 当前状态（用于定位记录）
            new_status: 新状态
        """
        logger.info(
            f"Updating template {template_uuid} status from {current_status.value} to {new_status.value}, tenant={tenant}"
        )

        repo = self._repository
        record = repo.get_by_template_uuid(template_uuid, tenant, current_status.value)
        if not record:
            logger.warning(
                f"Template not found: uuid={template_uuid}, status={current_status}, tenant={tenant}"
            )
            return None

        repo.update_status(
            template_uuid=template_uuid,
            tenant=tenant,
            current_status=current_status.value,
            new_status=new_status.value,
        )

        logger.info(f"Template {template_uuid} status updated to {new_status.value}")

        # Return updated record (status changed, re-query with new status)
        record = repo.get_by_template_uuid(template_uuid, tenant, new_status.value)
        return _record_to_response(record) if record else None

    def list_templates(
        self,
        tenant: str,
        status: TemplateStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> TemplateListResponse:
        """获取租户下的设备模板列表"""
        logger.info(f"Listing templates: tenant={tenant}, status={status}, page={page}")

        repo = self._repository
        status_value = status.value if status else None

        total, records = repo.list_templates(
            tenant=tenant,
            status=status_value,
            page=page,
            page_size=page_size,
        )

        items = [_record_to_response(r) for r in records]
        return TemplateListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )

    def list_online_templates(
        self, tenant: str, page: int = 1, page_size: int = 20
    ) -> TemplateListResponse:
        """获取租户下的ONLINE状态模板列表（用于Bot创建）"""
        logger.info(f"Listing ONLINE templates for tenant {tenant}")

        repo = self._repository
        total, records = repo.list_templates(
            tenant=tenant,
            status=TemplateStatus.ONLINE.value,
            page=page,
            page_size=page_size,
        )

        items = [_record_to_response(r) for r in records]
        return TemplateListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )

    def soft_delete_template(
        self,
        tenant: str,
        template_uuid: str,
        status: TemplateStatus,
        operator: str,
    ) -> bool:
        """软删除设备模板

        Args:
            tenant: 租户名
            template_uuid: 模板 UUID
            status: 当前状态（用于定位记录）
            operator: 操作人
        """
        logger.info(
            f"Soft deleting template: uuid={template_uuid}, status={status}, tenant={tenant}"
        )

        repo = self._repository
        record = repo.get_by_template_uuid(template_uuid, tenant, status.value)
        if not record:
            logger.warning(
                f"Template not found: uuid={template_uuid}, status={status}, tenant={tenant}"
            )
            return False

        repo.soft_delete(
            template_uuid=template_uuid,
            tenant=tenant,
            status=status.value,
            modifier=operator,
        )
        logger.info(f"Template {template_uuid} soft deleted successfully")
        return True
