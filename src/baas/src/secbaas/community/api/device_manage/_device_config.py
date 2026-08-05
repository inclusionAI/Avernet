"""Device creation configuration models.

Defines polymorphic configuration hierarchy for device creation
across different PaaS platforms (Arca, Sigma, etc.).
"""

from typing import Any

from pydantic import BaseModel, Field

from ._deploy_config import AgentCodingBotParams, DeviceCredentials
from ._models import MountPoint, ResourceSpecification, Storage
from ._outbound_rule import OutBoundOperationRule


class DeviceCreateConfig(BaseModel):
    """Base configuration for device creation.

    Platform-agnostic configuration that all PaaS platforms support.
    Platform-specific configs extend this base class.
    """

    name: str | None = Field(default=None, description="设备名称")
    description: str | None = Field(default=None, description="设备描述")


class ArcaCreateConfig(DeviceCreateConfig):
    """Arca platform-specific device creation configuration."""

    agent_coding_bot_params: AgentCodingBotParams | None = Field(
        default=None,
        description="AgentCoding Bot parameters used at sandbox creation time",
    )
    template_id: str | None = Field(
        default=None, description="Arca模板ID (不提供时从tenant extra_config获取)"
    )
    ttl_in_minutes: int = Field(default=1440, ge=10, description="TTL时间（分钟）")
    mount_points: list[MountPoint] | None = Field(
        default=None, description="OSS挂载点列表"
    )
    envs: dict[str, str] | None = Field(default=None, description="环境变量")
    outbound_operation_rule: OutBoundOperationRule | None = Field(
        default=None, description="出站操作规则"
    )
    resource_spec: ResourceSpecification | None = Field(
        default=None, description="资源配置规格"
    )
    metadata: dict[str, str] | None = Field(
        default=None, description="设备元数据，将透传至Arca SDK"
    )
    storage: Storage | None = Field(default=None, description="存储配置，用于NAS绑定")
    docker_image: str | None = Field(
        default=None, description="Docker 镜像名称，覆盖 template 中的默认镜像"
    )


class SigmaCreateConfig(DeviceCreateConfig):
    """Sigma platform-specific device creation configuration."""

    region: str | None = Field(default=None, description="区域")
    zone: str | None = Field(default=None, description="可用区")
    vpc_config: dict[str, Any] | None = Field(default=None, description="VPC配置")
    resource_spec: ResourceSpecification | None = Field(
        default=None, description="资源配置规格"
    )
    metadata: dict[str, str] | None = Field(default=None, description="设备元数据")


class LocalCreateConfig(DeviceCreateConfig):
    """Local platform-specific device creation configuration."""

    user_id: str = Field(..., description="用户ID")
    machine_id: str = Field(..., description="目标机器ID")
    tc_bot_id: str = Field(..., description="Bot业务标识符，由调用方直接传入")
    agent_code: str = Field(
        ..., description="Agent Code, 由调用方提供，颁发给本次要创建的设备的code"
    )
    envs: dict[str, str] | None = Field(default=None, description="环境变量")
    mount_path: str | None = Field(
        default=None, description="主机目录路径，用于挂载到容器中"
    )
    credentials: DeviceCredentials | None = Field(
        default=None, description="凭证数据，用于写入新创建的设备中"
    )
    engine_type: str | None = Field(
        default=None,
        description="Container engine type, passed directly to mng daemon",
    )


class PoolabCreateConfig(DeviceCreateConfig):
    """Poolab platform-specific device creation configuration.

    Fields map directly to Poolab REST API /openapi/antclaw/createMachine
    request body (see /tmp/poolab_api.md).
    """

    poolab_user_id: str = Field(
        ..., description="User ID for the Poolab machine (mapped from API userId)"
    )
    poolab_tenant_id: str | None = Field(
        default=None, description="Tenant ID (mapped from API tenantId)"
    )
    poolab_image_id: str | None = Field(
        default=None, description="Image ID (mapped from API imageId)"
    )
    poolab_envs: dict[str, str] | None = Field(
        default=None, description="Environment variables (mapped from API envs)"
    )
    poolab_spec: str | None = Field(
        default=None, description="Resource spec string (mapped from API spec)"
    )


class TeClawCreateConfig(DeviceCreateConfig):
    """TeClaw platform-specific device creation configuration."""

    teclaw_bot_config: dict[str, Any] | None = Field(
        default=None,
        description="TeClaw bot 透传配置",
    )


class DockerCreateConfig(DeviceCreateConfig):
    """Docker platform-specific device creation configuration.

    Per DOCKER_API-04: 5 Docker-specific fields + inherited name/description.
    All fields are Optional — DeployConfig may not override every template field.
    """

    image: str | None = Field(default=None, description="Docker 镜像名称")
    container_port: int | None = Field(
        default=None, ge=1, le=65535, description="容器内服务端口"
    )
    envs: dict[str, str] | None = Field(default=None, description="环境变量")
    cpu_limit: float | None = Field(
        default=None, ge=0.1, le=64, description="CPU 核心数上限"
    )
    memory_limit: str | None = Field(
        default=None, description="内存上限（Docker 格式）"
    )


class K8sCreateConfig(DeviceCreateConfig):
    """K8s platform-specific device creation configuration.

    No K8s-specific override fields. K8s credentials (kubeconfig, namespace,
    image, resources) come from the template config via K8sCredentials, not
    from detail_config overrides.
    """

    pass


DeviceCreateConfigUnion = (
    ArcaCreateConfig
    | SigmaCreateConfig
    | LocalCreateConfig
    | PoolabCreateConfig
    | TeClawCreateConfig
    | DockerCreateConfig
    | K8sCreateConfig
)
