"""Deploy configuration models with unified DeployConfig class.

Provides type-safe configuration passing from create_bot through start_device
to PaasServiceFacade. Uses a single unified DeployConfig class that contains
all platform-specific fields. Platform type is determined at runtime from template.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ._models import MountPermission, MountPoint, ResourceSpecification, Storage
from ._outbound_rule import OutBoundOperationRule

__all__ = [
    "AgentCodingBotParams",
    "ArcaDeployConfig",
    "DeployConfig",
    "DeviceCredentials",
    "LocalDeployConfig",
    "SigmaDeployConfig",
]


def _convert_mount_points(
    v: list[MountPoint] | list[dict[str, Any]] | None,
) -> list[MountPoint] | None:
    """Validate and convert mount_points to Arca MountPoint objects.

    Handles conversion from dict (JSON deserialization) to MountPoint.
    Also handles permission string 'READ_ONLY'/'READ_WRITE' to enum conversion.
    """
    if v is None:
        return None
    result = []
    for item in v:
        if isinstance(item, MountPoint):
            result.append(item)
        elif isinstance(item, dict):
            perm_str = item.get("permission", "READ_WRITE")
            if perm_str == "READ_ONLY":
                permission = MountPermission.READ_ONLY
            elif perm_str == "READ_WRITE":
                permission = MountPermission.READ_WRITE
            else:
                try:
                    permission = MountPermission(perm_str)
                except ValueError as exc:
                    raise ValueError(
                        f"Invalid mount permission '{perm_str}'. "
                        f"Must be 'READ_ONLY', 'READ_WRITE', or a valid MountPermission enum value.",
                    ) from exc
            result.append(
                MountPoint(
                    id=item.get("id", ""),
                    remote_dir=item["remote_dir"],
                    local_dir=item["local_dir"],
                    permission=permission,
                ),
            )
        else:
            raise ValueError(f"Invalid mount_point type: {type(item)}")
    return result


class DeviceCredentials(BaseModel):
    """Device-level credentials passed through DeployConfig to the mng daemon."""

    model_config = ConfigDict(populate_by_name=True)

    token: str | None = Field(default=None, serialization_alias="TOKEN")
    client_id: str | None = Field(default=None, serialization_alias="CLIENT_ID")
    owner_id: str | None = Field(default=None, serialization_alias="OWNER_ID")
    bot_id: str | None = Field(default=None, serialization_alias="BOT_ID")
    entity_id: str | None = Field(default=None, serialization_alias="ENTITY_ID")
    entity_type: str | None = Field(default=None, serialization_alias="ENTITY_TYPE")
    bot_type: str | None = Field(default=None, serialization_alias="BOT_TYPE")
    agent_code: str | None = Field(default=None, serialization_alias="AGENT_CODE")
    stage: str | None = Field(default=None, serialization_alias="STAGE")


class ArcaDeployConfig(BaseModel):
    """ARCA platform deploy configuration.

    Contains Arca-specific fields for device deployment:
    - mount_points: OSS mount points
    - ttl_in_minutes: Device lifetime
    - arca_metadata: Custom metadata for Arca SDK
    - outbound_operation_rule: Outbound traffic rules
    - storage: NAS storage binding
    - Lifecycle hooks for create/destroy operations

    Note: This is preserved for internal platform-specific use.
    The unified DeployConfig should be used for public API.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Arca-specific fields
    mount_points: list[MountPoint] | None = Field(
        default=None,
        description="OSS mount points for Arca platform devices",
    )
    ttl_in_minutes: int | None = Field(
        default=None,
        ge=10,
        description="Device lifetime duration in minutes (default: use template or Arca default 1440)",
    )
    arca_metadata: dict[str, str] | None = Field(
        default=None,
        description="Custom metadata for Arca SDK (merged with template metadata)",
    )
    outbound_operation_rule: OutBoundOperationRule | None = Field(
        default=None,
        description="Outbound traffic operation rules for Arca platform devices",
    )
    storage: Storage | None = Field(
        default=None,
        description="Storage configuration for NAS binding (Arca platform)",
    )
    oss_mount_id: str | None = Field(
        default=None,
        description="OSS mount configuration ID for Arca platform",
    )
    resource_spec: ResourceSpecification | None = Field(
        default=None,
        description="Hardware resource specification (shared with Sigma)",
    )

    # Common fields
    envs: dict[str, str] | None = Field(
        default=None,
        description="Environment variables for container (merged with template envs, DeployConfig takes precedence)",
    )
    after_create_cmd_hook: str | None = Field(
        default=None,
        description="Lifecycle hook: shell script executed after PaaS device creation",
    )
    after_create_hook_wait_seconds: int = Field(
        default=300,
        ge=0,
        description="Wait time for after_create_cmd_hook execution in seconds (default 5 min)",
    )
    before_destroy_cmd_hook: str | None = Field(
        default=None,
        description="Lifecycle hook: shell script executed before PaaS device destruction",
    )
    before_destroy_hook_wait_seconds: int = Field(
        default=300,
        ge=0,
        description="Wait time for before_destroy_cmd_hook execution in seconds (default 5 min)",
    )

    @field_validator("mount_points", mode="before")
    @classmethod
    def _validate_mount_points(
        cls,
        v: list[MountPoint] | list[dict[str, Any]] | None,
    ) -> list[MountPoint] | None:
        """Validate and convert mount_points to Arca MountPoint objects."""
        return _convert_mount_points(v)


class LocalDeployConfig(BaseModel):
    """Local platform deploy configuration.

    Contains Local-specific fields for device deployment:
    - machine_id: Target machine for device operations (per D-FF05)
    - agent_code: Agent code for identifying the business agent
    - envs: Environment variables
    - Lifecycle hooks for create/destroy operations

    Note: This is preserved for internal platform-specific use.
    The unified DeployConfig should be used for public API.
    """

    # Local-specific fields
    machine_id: str | None = Field(
        default=None,
        description="Target machine for device operations (Local-specific per D-FF05)",
    )
    agent_code: str | None = Field(
        default=None,
        description="Agent代码，用于标识设备所属的业务代理",
    )
    tc_bot_id: str | None = Field(
        default=None,
        description="Bot业务标识符，由调用方直接传入",
    )

    # Common fields
    envs: dict[str, str] | None = Field(
        default=None,
        description="Environment variables for container (merged with template envs, DeployConfig takes precedence)",
    )
    after_create_cmd_hook: str | None = Field(
        default=None,
        description="Lifecycle hook: shell script executed after PaaS device creation",
    )
    after_create_hook_wait_seconds: int = Field(
        default=300,
        ge=0,
        description="Wait time for after_create_cmd_hook execution in seconds (default 5 min)",
    )
    before_destroy_cmd_hook: str | None = Field(
        default=None,
        description="Lifecycle hook: shell script executed before PaaS device destruction",
    )
    before_destroy_hook_wait_seconds: int = Field(
        default=300,
        ge=0,
        description="Wait time for before_destroy_cmd_hook execution in seconds (default 5 min)",
    )


class SigmaDeployConfig(BaseModel):
    """Sigma platform deploy configuration.

    Contains Sigma-specific fields for device deployment:
    - zone: Availability zone
    - vpc_config: VPC network configuration
    - sigma_metadata: Custom metadata
    - resource_spec: Hardware resource specification
    - envs: Environment variables
    - Lifecycle hooks for create/destroy operations

    Note: This is preserved for internal platform-specific use.
    The unified DeployConfig should be used for public API.
    """

    # Sigma-specific fields
    zone: str | None = Field(
        default=None,
        description="Availability zone for Sigma platform",
    )
    vpc_config: dict[str, Any] | None = Field(
        default=None,
        description="VPC configuration for Sigma platform",
    )
    sigma_metadata: dict[str, str] | None = Field(
        default=None,
        description="Device metadata for Sigma platform",
    )
    resource_spec: ResourceSpecification | None = Field(
        default=None,
        description="Hardware resource specification (shared with ARCA)",
    )

    # Common fields
    envs: dict[str, str] | None = Field(
        default=None,
        description="Environment variables for container (merged with template envs, DeployConfig takes precedence)",
    )
    after_create_cmd_hook: str | None = Field(
        default=None,
        description="Lifecycle hook: shell script executed after PaaS device creation",
    )
    after_create_hook_wait_seconds: int = Field(
        default=300,
        ge=0,
        description="Wait time for after_create_cmd_hook execution in seconds (default 5 min)",
    )
    before_destroy_cmd_hook: str | None = Field(
        default=None,
        description="Lifecycle hook: shell script executed before PaaS device destruction",
    )
    before_destroy_hook_wait_seconds: int = Field(
        default=300,
        ge=0,
        description="Wait time for before_destroy_cmd_hook execution in seconds (default 5 min)",
    )


class AgentCodingBotParams(BaseModel):
    """Opaque AgentCoding parameters consumed only at the Arca boundary."""

    theta_key: str | None = Field(default=None, description="Encrypted theta key")


class DeployConfig(BaseModel):
    """Unified deploy configuration for all platforms.

    Contains ALL fields from ArcaDeployConfig, LocalDeployConfig, and SigmaDeployConfig.
    Platform type is determined at runtime from device_template lookup, NOT from a
    platform_type discriminator field.

    Design principles:
    - No platform_type field - platform resolved at runtime from template
    - Metadata fields prefixed (arca_metadata, sigma_metadata) to avoid conflicts
    - resource_spec is shared between ARCA and Sigma platforms
    - All fields are optional to allow platform-agnostic config construction

    Usage:
        # Caller constructs unified config without knowing platform
        config = DeployConfig(
            machine_id="machine-123",
            envs={"KEY": "value"},
            ttl_in_minutes=1440,
        )

        # Platform-specific extraction happens in device_service.start_device
        # based on template.config type (ArcaTemplateConfig, LocalTemplateConfig, etc.)
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    agent_coding_bot_params: AgentCodingBotParams | None = Field(
        default=None,
        description="AgentCoding Bot sandbox extension parameters",
    )

    # === Arca-specific fields ===
    mount_points: list[MountPoint] | None = Field(
        default=None,
        description="OSS mount points for Arca platform devices",
    )
    ttl_in_minutes: int | None = Field(
        default=None,
        ge=10,
        description="Device lifetime duration in minutes (default: use template or Arca default 1440)",
    )
    arca_metadata: dict[str, str] | None = Field(
        default=None,
        description="Custom metadata for Arca SDK (renamed from 'metadata' to avoid conflict)",
    )
    outbound_operation_rule: OutBoundOperationRule | None = Field(
        default=None,
        description="Outbound traffic operation rules for Arca platform devices",
    )
    storage: Storage | None = Field(
        default=None,
        description="Storage configuration for NAS binding (Arca platform)",
    )
    oss_mount_id: str | None = Field(
        default=None,
        description="OSS mount configuration ID for Arca platform",
    )
    # NOTE: resource_spec (shared between ARCA and Sigma) is in === Common
    # fields === below, not duplicated here.  It is fully usable for ARCA.

    # === Local-specific fields ===
    machine_id: str | None = Field(
        default=None,
        description="Target machine for device operations (Local-specific per D-FF05)",
    )
    agent_code: str | None = Field(
        default=None,
        description="Agent代码，用于标识设备所属的业务代理 (Local-specific)",
    )
    user_id: str | None = Field(
        default=None,
        description="用户ID，用于LOCAL平台设备部署认证",
    )
    mount_path: str | None = Field(
        default=None,
        description="Host directory path to mount into the container (optional, passed to mng daemon)",
    )
    credentials: DeviceCredentials | None = Field(
        default=None,
        description="凭证数据，用于写入新创建的设备中",
    )
    tc_bot_id: str | None = Field(
        default=None,
        description="Bot业务标识符，由调用方直接传入",
    )
    engine_type: str | None = Field(
        default=None,
        description="Container engine type (e.g., 'openclaw', 'moltis'). "
        "BaaS performs no validation; mng daemon interprets and sets default.",
    )

    # === Sigma-specific fields ===
    zone: str | None = Field(
        default=None,
        description="Availability zone for Sigma platform",
    )
    vpc_config: dict[str, Any] | None = Field(
        default=None,
        description="VPC configuration for Sigma platform",
    )
    sigma_metadata: dict[str, str] | None = Field(
        default=None,
        description="Device metadata for Sigma platform (prefixed to avoid conflict)",
    )

    # === Poolab-specific fields ===
    poolab_user_id: str | None = Field(
        default=None,
        description="Poolab user ID for machine creation (required for POOLAB platform)",
    )
    poolab_tenant_id: str | None = Field(
        default=None,
        description="Poolab tenant ID (optional, falls back to template if not set)",
    )
    poolab_image_id: str | None = Field(
        default=None,
        description="Poolab image ID (optional, falls back to template default for env if not set)",
    )
    poolab_envs: dict[str, str] | None = Field(
        default=None,
        description="Poolab environment variables (optional, passed directly to createMachine API)",
    )
    poolab_spec: str | None = Field(
        default=None,
        description="Poolab spec string (e.g., '2C4G10G'), optional, passed directly to createMachine API",
    )

    # === TeClaw-specific fields ===
    teclaw_bot_config: dict[str, Any] | None = Field(
        default=None,
        description="TeClaw bot 透传配置，由调用方构建，直接传递至 TeClaw API",
    )

    # === Docker-specific fields ===
    docker_image: str | None = Field(
        default=None,
        description="Docker 镜像名称，覆盖 template 中的默认镜像",
    )
    docker_container_port: int | None = Field(
        default=None,
        ge=1,
        le=65535,
        description="容器内服务端口，覆盖 template 中的默认端口",
    )

    # === Common fields (all platforms) ===
    resource_spec: ResourceSpecification | None = Field(
        default=None,
        description="Hardware resource specification (shared between ARCA and Sigma)",
    )
    envs: dict[str, str] | None = Field(
        default=None,
        description="Environment variables for container (merged with template envs, DeployConfig takes precedence)",
    )
    after_create_cmd_hook: str | None = Field(
        default=None,
        description="Lifecycle hook: shell script executed after PaaS device creation",
    )
    after_create_hook_wait_seconds: int = Field(
        default=300,
        ge=0,
        description="Wait time for after_create_cmd_hook execution in seconds (default 5 min)",
    )
    before_destroy_cmd_hook: str | None = Field(
        default=None,
        description="Lifecycle hook: shell script executed before PaaS device destruction",
    )
    before_destroy_hook_wait_seconds: int = Field(
        default=300,
        ge=0,
        description="Wait time for before_destroy_cmd_hook execution in seconds (default 5 min)",
    )

    @field_validator("mount_points", mode="before")
    @classmethod
    def _validate_mount_points(
        cls,
        v: list[MountPoint] | list[dict[str, Any]] | None,
    ) -> list[MountPoint] | None:
        """Validate and convert mount_points to Arca MountPoint objects."""
        return _convert_mount_points(v)
