"""PaaS Service Facade configuration models.

Defines merged configuration types that combine credentials and creation
configuration for simplified facade API usage. These types flatten the
separate credentials and create_config structures per D-01.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ._deploy_config import DeviceCredentials
from ._device_config import (
    ArcaCreateConfig,
    LocalCreateConfig,
    PoolabCreateConfig,
    SigmaCreateConfig,
)
from ._encryptable_header_rule import (
    EncryptableOutBoundRule,
)
from ._models import MountPoint, ResourceSpecification, Storage
from ._outbound_rule import OutBoundOperationRule


class BaseDeviceConfig(BaseModel):
    """Base configuration for device creation via Facade.

    Contains common CreateConfig fields shared across all platform configs.
    Note: tenant_name is passed separately to create_device(), not part of config override.

    Common fields:
        - name: Device name/metadata
        - description: Device description
    """

    # Common CreateConfig fields - metadata
    name: str | None = Field(
        default=None,
        description="Device name",
    )
    description: str | None = Field(
        default=None,
        description="Device description",
    )


class ArcaDeviceConfig(BaseDeviceConfig):
    """Merged configuration for Arca device creation via Facade.

    Extends BaseDeviceConfig with Arca-specific fields.
    Combines ArcaCredentials and ArcaCreateConfig fields into a flattened
    structure for simplified facade API usage.

    Inherited from BaseDeviceConfig:
        - tenant_name, name, description

    ArcaCredentials part (all optional with defaults, determined by template):
        - base_url, api_key: Direct credentials (used if provided)
        - timeout, app_name: Connection configuration

    ArcaCreateConfig part:
        - template_id: Optional Arca template ID (falls back to tenant's
          extra_config.template_id if not provided)
        - ttl_in_minutes: Device lifetime (default: 1440)
        - mount_points, envs: Mount and environment configuration
        - resource_spec: Hardware resource specification
        - metadata: Custom metadata for Arca SDK
        - oss_mount_id: OSS mount configuration
        - outbound_operation_rule: Outbound traffic rules

    SM4 Encryption Support (D-13.02) - Header value encryption:
    - Use EncryptableOutBoundRule with EncryptableHeaderRule for encrypted values
    - encrypt_value: bool flag indicates encryption state
    - Encryption performed in device_service.create_device (or caller before pass)
    - Decryption performed in device_service.start_device

    Example:
        ArcaDeviceConfig(
            outbound_operation_rule=EncryptableOutBoundRule(
                header_operation_rules=[
                    EncryptableHeaderRule(
                        domains=["*.api.com"],
                        action="SET_HEADER",
                        header_name="Authorization",
                        value="Bearer token",
                        encrypt_value=True,
                    )
                ]
            )
        )
    """

    # CreateConfig part - arca_template_id optional (fallback to template config)
    arca_template_id: str | None = Field(
        default=None,
        description="Arca platform sandbox template ID for device creation (from template config)",
    )
    ttl_in_minutes: int = Field(
        default=1440,
        ge=10,
        description="Device lifetime in minutes",
    )
    mount_points: list[MountPoint] | None = Field(
        default=None,
        description="OSS mount points list",
    )
    envs: dict[str, str] | None = Field(
        default=None,
        description="Environment variables",
    )
    resource_spec: ResourceSpecification | None = Field(
        default=None,
        description="Hardware resource specification",
    )
    metadata: dict[str, str] | None = Field(
        default=None,
        description="Device metadata passed to Arca SDK",
    )
    oss_mount_id: str | None = Field(
        default=None,
        description="OSS mount configuration ID",
    )
    outbound_operation_rule: EncryptableOutBoundRule | OutBoundOperationRule | None = (
        Field(
            default=None,
            description="出站操作规则(支持EncryptableOutBoundRule类型以启用加密)",
        )
    )
    storage: Storage | None = Field(
        default=None,
        description="NAS storage binding configuration",
    )
    docker_image: str | None = Field(
        default=None,
        description="Docker 镜像名称，覆盖 template 中的默认镜像",
    )
    extra_properties: dict[str, Any] | None = Field(
        default=None,
        description="Opaque engine-owned properties for platform extensions",
    )

    def to_create_config(self) -> ArcaCreateConfig:
        """Extract create config fields into ArcaCreateConfig.

        Returns:
            ArcaCreateConfig: Creation configuration for Arca device
        """
        return ArcaCreateConfig(
            template_id=self.arca_template_id,
            ttl_in_minutes=self.ttl_in_minutes,
            name=self.name,
            description=self.description,
            mount_points=self.mount_points,
            envs=self.envs,
            outbound_operation_rule=self.outbound_operation_rule,
            resource_spec=self.resource_spec,
            metadata=self.metadata,
            storage=self.storage,
            docker_image=self.docker_image,
            extra_properties=self.extra_properties,
        )


class SigmaDeviceConfig(BaseDeviceConfig):
    """Merged configuration for Sigma device creation via Facade.

    Extends BaseDeviceConfig with Sigma-specific fields.
    Combines SigmaCredentials and SigmaCreateConfig fields into a flattened
    structure for simplified facade API usage.

    Inherited from BaseDeviceConfig:
        - tenant_name, name, description

    SigmaCredentials part (endpoint, access_key, secret_key required):
        - endpoint: Sigma API endpoint URL
        - access_key, secret_key: Authentication credentials
        - region: Target region (default: "default")

    SigmaCreateConfig part:
        - zone: Availability zone
        - vpc_config: VPC network configuration
        - resource_spec: Hardware resource specification
        - metadata: Custom metadata
    """

    # Credentials part - required fields
    endpoint: str = Field(
        ...,
        description="Sigma API endpoint URL",
    )
    access_key: str = Field(
        ...,
        description="Sigma access key for authentication",
    )
    secret_key: str = Field(
        ...,
        description="Sigma secret key for authentication",
    )
    region: str = Field(
        default="default",
        description="Target region",
    )

    # CreateConfig part - all optional
    zone: str | None = Field(
        default=None,
        description="Availability zone",
    )
    vpc_config: dict[str, Any] | None = Field(
        default=None,
        description="VPC configuration",
    )
    resource_spec: ResourceSpecification | None = Field(
        default=None,
        description="Hardware resource specification",
    )
    metadata: dict[str, str] | None = Field(
        default=None,
        description="Device metadata",
    )

    def to_create_config(self) -> SigmaCreateConfig:
        """Extract create config fields into SigmaCreateConfig.

        Returns:
            SigmaCreateConfig: Creation configuration for Sigma device
        """
        return SigmaCreateConfig(
            region=self.region,
            name=self.name,
            description=self.description,
            zone=self.zone,
            vpc_config=self.vpc_config,
            resource_spec=self.resource_spec,
            metadata=self.metadata,
        )


class LocalDeviceConfig(BaseDeviceConfig):
    """Merged configuration for Local device creation via Facade.

    Extends BaseDeviceConfig with Local-specific fields.
    Combines LocalCredentials and LocalCreateConfig fields into a flattened
    structure for simplified facade API usage.

    Inherited from BaseDeviceConfig:
        - name, description

    Fields:
        - user_id: User ID for identifying the user and authentication
        - machine_id: Target machine for device operations
        - bot_uuid: Bot UUID for the device (globally unique identifier)
        - agent_code: Agent code for the device
        - envs: Environment variables
    """

    # Required fields
    user_id: str = Field(
        ...,
        description="用户ID",
    )
    machine_id: str = Field(
        ...,
        description="Target machine for device operations",
    )
    tc_bot_id: str = Field(
        ...,
        description="Bot业务标识符，由调用方直接传入",
    )
    agent_code: str = Field(
        ...,
        description="Agent代码",
    )

    # Optional fields
    envs: dict[str, str] | None = Field(
        default=None,
        description="Environment variables",
    )
    mount_path: str | None = Field(
        default=None,
        description="Host directory path to mount into container",
    )
    credentials: DeviceCredentials | None = Field(
        default=None, description="凭证数据，用于写入新创建的设备中"
    )
    engine_type: str | None = Field(
        default=None,
        description="Container engine type for mng daemon",
    )

    def to_create_config(self) -> LocalCreateConfig:
        """Extract create config fields into LocalCreateConfig.

        Returns:
            LocalCreateConfig: Creation configuration for Local device
        """
        return LocalCreateConfig(
            name=self.name,
            description=self.description,
            user_id=self.user_id,
            machine_id=self.machine_id,
            tc_bot_id=self.tc_bot_id,
            agent_code=self.agent_code,
            envs=self.envs,
            mount_path=self.mount_path,
            credentials=self.credentials,
            engine_type=self.engine_type,
        )


class PoolabDeviceConfig(BaseDeviceConfig):
    """Merged configuration for Poolab device creation via Facade.

    Extends BaseDeviceConfig with Poolab-specific fields.
    Combines PoolabCredentials and PoolabCreateConfig fields into a flattened
    structure for simplified facade API usage.
    """

    # CreateConfig part
    poolab_user_id: str = Field(..., description="User ID for Poolab machine")
    poolab_tenant_id: str | None = Field(default=None, description="Tenant ID")
    poolab_image_id: str | None = Field(default=None, description="Image ID")
    poolab_envs: dict[str, str] | None = Field(
        default=None, description="Environment variables"
    )
    poolab_spec: str | None = Field(
        default=None, description="Resource spec string (e.g., '2C4G10G')"
    )

    def to_create_config(self) -> PoolabCreateConfig:
        """Extract create config fields into PoolabCreateConfig."""
        from ._device_config import PoolabCreateConfig

        return PoolabCreateConfig(
            name=self.name,
            description=self.description,
            poolab_user_id=self.poolab_user_id,
            poolab_tenant_id=self.poolab_tenant_id,
            poolab_image_id=self.poolab_image_id,
            poolab_envs=self.poolab_envs,
            poolab_spec=self.poolab_spec,
        )


class TeClawDeviceConfig(BaseDeviceConfig):
    """Merged configuration for TeClaw device creation via Facade.

    Extends BaseDeviceConfig with TeClaw-specific fields.
    """

    # CreateConfig part
    teclaw_bot_config: dict[str, Any] | None = Field(
        default=None,
        description="TeClaw bot 透传配置",
    )

    def to_create_config(self) -> TeClawCreateConfig:  # noqa: F821
        """Extract create config fields into TeClawCreateConfig."""
        from ._device_config import TeClawCreateConfig

        return TeClawCreateConfig(
            name=self.name,
            description=self.description,
            teclaw_bot_config=self.teclaw_bot_config,
        )


class DockerDeviceConfig(BaseDeviceConfig):
    """Merged configuration for Docker device creation via Facade.

    Extends BaseDeviceConfig with Docker-specific fields per DOCKER_API-03.
    5 Docker-specific fields: image (required), container_port (required),
    envs (optional), cpu_limit (default 1.0), memory_limit (required).
    2 inherited fields: name (optional), description (optional).

    Template-only fields (in DockerTemplateConfig, NOT here):
    image_pull_policy, credentials, mount_path.
    """

    # Docker-specific fields
    image: str = Field(..., description="Docker 镜像名称")
    container_port: int = Field(..., ge=1, le=65535, description="容器内服务端口")
    envs: dict[str, str] | None = Field(default=None, description="环境变量")
    cpu_limit: float = Field(default=1.0, ge=0.1, le=64, description="CPU 核心数上限")
    memory_limit: str = Field(..., description="内存上限（Docker 格式）")

    def to_create_config(self) -> DockerCreateConfig:  # noqa: F821
        """Extract create config fields into DockerCreateConfig.

        Returns:
            DockerCreateConfig: Creation configuration for Docker device
        """
        from ._device_config import DockerCreateConfig

        return DockerCreateConfig(
            name=self.name,
            description=self.description,
            image=self.image,
            container_port=self.container_port,
            envs=self.envs,
            cpu_limit=self.cpu_limit,
            memory_limit=self.memory_limit,
        )


class K8sDeviceConfig(BaseDeviceConfig):
    """Merged configuration for K8s device creation via Facade.

    Extends BaseDeviceConfig with no K8s-specific fields. All credential and
    runtime config (kubeconfig, namespace, image, resources) comes from
    K8sTemplateConfig via K8sCredentials, not from detail_config overrides.
    """

    def to_create_config(self) -> K8sCreateConfig:  # noqa: F821
        """Extract create config fields into K8sCreateConfig."""
        from ._device_config import K8sCreateConfig

        return K8sCreateConfig(
            name=self.name,
            description=self.description,
        )
