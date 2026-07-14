"""
Device status enumeration and Pydantic models for baas_device table.

对应表: baas_device
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from secbaas.community.api import DomainError

from ._command_result import CommandResult
from ._deploy_config import DeployConfig


class DeviceStatus(StrEnum):
    """Device status enumeration.

    Maps to PaaS container state:
    - PENDING: Container provisioning in progress
    - ACTIVE: Container running (PaaS RUNNING)
    - UPDATING: Device being updated/drained, excluded from load balancing
    - RELEASED: Container destroyed (PaaS DESTROYED)
    - STOPPED: Device stopped (provider destroyed, record preserved for restart)
    - FAILED: Container failed or error state
    - OFFLINE: Device is reachable but its daemon/service is not running
    """

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    UPDATING = "UPDATING"
    RELEASED = "RELEASED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    OFFLINE = "OFFLINE"


# ==================== Pydantic Schemas ====================


class DeviceConfig(BaseModel):
    """Typed model for baas_device.extra_config JSON column.

    Stores device startup configuration:
    - template_uuid: Device template UUID (optional, PaaS layer handles fallback)
    - deploy_config: Bot deploy configuration for device provisioning
    - metadata: Arbitrary key-value metadata for the device
    """

    model_config = ConfigDict(extra="allow")

    template_uuid: str | None = Field(
        default=None, description="设备模板UUID（optional，PaaS层处理fallback）"
    )
    deploy_config: DeployConfig | None = Field(
        default=None, description="Bot deploy configuration for device provisioning"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="设备元数据，用于存储扩展信息"
    )


class DeviceCreate(BaseModel):
    """创建设备请求 - template_uuid 移除"""

    domain: str = Field(default="default", max_length=128, description="域名标识")
    extra_config: DeviceConfig = Field(
        default_factory=DeviceConfig, description="扩展配置"
    )
    operator: str = Field(..., min_length=1, max_length=128, description="操作人 ID")


class DeviceResponse(BaseModel):
    """设备响应"""

    id: int
    device_uuid: str
    tenant: str
    env: str
    domain: str
    status: str
    provider_type: str | None
    provider_device_id: str | None
    provider_device_props: dict[str, Any] | None
    extra_config: DeviceConfig | None = Field(default=None, description="扩展配置")
    err_msg: str | None = None
    creator: str
    modifier: str
    gmt_create: datetime
    gmt_modified: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class DeviceInfo(BaseModel):
    """Lightweight device model for bot embed (subset of DeviceResponse fields)."""

    device_uuid: str
    status: str
    provider_type: str | None = None
    provider_device_id: str | None = None
    gmt_create: datetime
    health: str = Field(
        default="unknown",
        description="Realtime health status: 'true', 'false', or 'unknown' when health_check is not enabled",
    )

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class DeviceListResponse(BaseModel):
    """设备列表响应"""

    items: list[DeviceResponse]
    total: int
    page: int
    page_size: int


class DeviceNotFoundError(DomainError):
    error_code = "DEVICE_NOT_FOUND"
    http_status = 404

    def __init__(self, device_id: str | int = ""):
        self.device_id = device_id
        self.message = f"Device not found: {device_id}"
        super().__init__(self.message)


class DestroyDeviceResponse(BaseModel):
    """Response for device destruction operation.

    Captures overall success, error aggregation from multiple sources,
    and complete hook execution result for debugging.
    """

    success: bool = Field(
        description="Overall operation success (True if device is destroyed or already gone)"
    )
    error_message: str | None = Field(
        default=None,
        description="Aggregated error messages from hook and/or PaaS failures",
    )
    hook_result: CommandResult | None = Field(
        default=None,
        description="Full hook execution result if before_destroy_cmd_hook was configured",
    )
