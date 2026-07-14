"""Device template management Pydantic models.

Extracted from api/domain/device_template_manage.py as part of
api/domain → api/{domain_name}/ refactoring.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from secbaas.community.api.device_manage import K8sOutboundProxyRule
from secbaas.community.api.tenant_manage import ImagePullPolicy, TenantType

# ==================== Config Models ====================


class ArcaTemplateConfig(BaseModel):
    """ARCA platform specific configuration.

    Stored in baas_device_template.config JSON column for ARCA templates.

    Multi-Environment Template ID Support:
    - arca_template_id: Default template ID (with alias="template_id" for backward compat)
    - arca_template_id_pre: Pre-production environment template ID
    - arca_template_id_prod: Production environment template ID
    - get_effective_template_id(env): Selects appropriate ID based on current environment

    SM4 Encryption Support (D-13.01):
    - encrypt_api_key: System-guaranteed flag — api_key is always encrypted in DB
      before persistence by _ensure_api_key_encrypted (regardless of caller value)
    - api_key: ARCA API key — stored and transported as SM4 ciphertext when
      encrypt_api_key=True; decrypted only at point of use by
      PaasServiceFactory._create_arca_credentials_from_template (factory.py)
    - Encryption performed by device_template_service immediately after receiving params
    - Decryption is deferred to factory.py; _record_to_response returns ciphertext as-is
      (read-modify-write safety via encrypt_api_key sentinel in _ensure_api_key_encrypted)
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    type: Literal["ARCA"] = Field(..., description="平台类型")
    base_url: str = Field(..., description="ARCA API 基础 URL")
    api_key: str = Field(..., description="ARCA API 密钥")
    encrypt_api_key: bool = Field(
        default=False, description="api_key已是密文（由系统强制保证）"
    )
    app_name: str = Field(default="secbaas", description="应用名称")
    arca_template_id: str | None = Field(
        None, description="ARCA 模板 ID (默认)", alias="template_id"
    )
    arca_template_id_pre: str | None = Field(
        None, description="ARCA 模板 ID (预发环境)"
    )
    arca_template_id_prod: str | None = Field(
        None, description="ARCA 模板 ID (正式环境)"
    )
    oss_mount_id: str | None = Field(None, description="OSS 挂载 ID")
    default_ttl_minutes: int = Field(default=1440, description="默认 TTL（分钟）")
    timeout: float = Field(default=30.0, description="请求超时（秒）")

    def get_effective_template_id(self, env: str) -> str | None:
        """Get environment-aware template ID.

        Selection priority:
        - "pre" env: arca_template_id_pre > arca_template_id (fallback)
        - "prod" env: arca_template_id_prod > arca_template_id (fallback)
        - other envs: arca_template_id

        Args:
            env: Current environment string (e.g., "pre", "prod", "dev")

        Returns:
            Effective template ID for the given environment
        """
        env_lower = env.lower()
        if env_lower == "pre":
            return self.arca_template_id_pre or self.arca_template_id
        elif env_lower == "prod":
            return self.arca_template_id_prod or self.arca_template_id
        return self.arca_template_id


class SigmaTemplateConfig(BaseModel):
    """Sigma platform specific configuration.

    Stored in baas_device_template.config JSON column for Sigma templates.
    """

    model_config = ConfigDict(extra="allow")

    type: Literal["Sigma"] = Field(..., description="平台类型")
    endpoint: str = Field(..., description="Sigma API 端点")
    access_key: str = Field(..., description="访问密钥")
    secret_key: str = Field(..., description="秘密密钥")
    region: str = Field(default="default", description="区域")


class LocalTemplateConfig(BaseModel):
    """Local platform specific configuration.

    Stored in baas_device_template.config JSON column for Local templates.
    """

    model_config = ConfigDict(extra="allow")

    type: Literal["LOCAL"] = Field(default="LOCAL", description="平台类型")
    mng_offline_threshold_seconds: int = Field(
        default=30, description="mng超时判定阈值（秒）"
    )


class PoolabTemplateConfig(BaseModel):
    """Poolab (虾池) platform specific configuration.

    Stored in baas_device_template.config JSON column for POOLAB templates.

    SM4 Encryption Support (follows Arca D-13.01):
    - encrypt_tenant_token: Flag indicating whether tenant_token should be encrypted
    - poolab_tenant_token: Poolab tenant token (always plain from caller's perspective)
    - Encryption performed by device_template_service immediately after receiving params
    - Decryption performed in factory.py when creating PoolabCredentials for usage

    Multi-Environment Endpoint and Image ID Support:
    - poolab_endpoint_pre/poolab_endpoint_prod: Per-environment Poolab API endpoints
    - poolab_default_image_id_pre/poolab_default_image_id_prod: Per-environment image IDs
    - get_effective_endpoint(env): Selects endpoint based on current environment
    - get_effective_image_id(env): Selects image ID based on current environment
    - Non-pre/prod callers must handle None returns from both getters.

    Note: No resource_spec field. Poolab API does not accept it;
    image_id determines resources on Poolab's side.
    """

    model_config = ConfigDict(extra="allow")

    type: Literal["POOLAB"] = Field(..., description="平台类型")
    poolab_endpoint_pre: str | None = Field(
        default=None, description="Poolab API endpoint (预发环境)"
    )
    poolab_endpoint_prod: str | None = Field(
        default=None, description="Poolab API endpoint (正式环境)"
    )
    poolab_tenant_id: str = Field(
        ..., description="Poolab tenant ID for authentication"
    )
    poolab_tenant_token: str = Field(
        ..., description="Poolab tenant token (plain when encrypt_tenant_token=False)"
    )
    encrypt_tenant_token: bool = Field(
        default=False, description="tenant_token是否应SM4加密存储"
    )
    poolab_default_image_id_pre: str | None = Field(
        default=None, description="Default Poolab image ID (预发环境)"
    )
    poolab_default_image_id_prod: str | None = Field(
        default=None, description="Default Poolab image ID (正式环境)"
    )

    def get_effective_endpoint(self, env: str) -> str | None:
        """Get environment-aware Poolab endpoint URL.

        Selection:
        - "pre" env: poolab_endpoint_pre
        - "prod" env: poolab_endpoint_prod
        - other envs: None

        Args:
            env: Current environment string (e.g., "pre", "prod", "dev")

        Returns:
            Effective endpoint for the given environment, or None if the
            environment is not "pre" or "prod" (no fallback to a base field).
        """
        env_lower = env.lower()
        if env_lower == "pre":
            return self.poolab_endpoint_pre
        elif env_lower == "prod":
            return self.poolab_endpoint_prod
        return None

    def get_effective_image_id(self, env: str) -> str | None:
        """Get environment-aware Poolab image ID.

        Selection:
        - "pre" env: poolab_default_image_id_pre
        - "prod" env: poolab_default_image_id_prod
        - other envs: None

        Args:
            env: Current environment string (e.g., "pre", "prod", "dev")

        Returns:
            Effective image ID for the given environment, or None if the
            environment is not "pre" or "prod" (no fallback to a base field).
        """
        env_lower = env.lower()
        if env_lower == "pre":
            return self.poolab_default_image_id_pre
        elif env_lower == "prod":
            return self.poolab_default_image_id_prod
        return None


class TeClawTemplateConfig(BaseModel):
    """TeClaw platform specific configuration.

    Stored in baas_device_template.config JSON column for TECLAW templates.
    """

    model_config = ConfigDict(extra="allow")

    type: Literal["TECLAW"] = Field(..., description="平台类型")
    teclaw_endpoint: str = Field(..., description="TeClaw API endpoint URL")
    timeout: float = Field(
        default=30.0,
        description="HTTP request timeout in seconds",
    )


class K8sTemplateConfig(BaseModel):
    """Kubernetes platform specific configuration.

    Stored in baas_device_template.config JSON column for K8S templates.
    Resource fields are defaults -- can be overridden per-device.
    """

    model_config = ConfigDict(extra="allow")

    type: Literal["K8s"] = Field(..., description="平台类型")
    kubeconfig: str = Field(..., description="kubeconfig YAML 原文内容（内联存储）")
    namespace: str = Field(default="default", description="默认 K8s namespace")
    image: str = Field(
        default="registry.example.com/bot-runtime:latest",
        description="默认容器镜像",
    )
    cpu_request: str | None = Field(
        default=None, description="默认 CPU 请求 (e.g., '500m')"
    )
    cpu_limit: str | None = Field(default=None, description="默认 CPU 限制 (e.g., '1')")
    memory_request: str | None = Field(
        default=None, description="默认内存请求 (e.g., '512Mi')"
    )
    memory_limit: str | None = Field(
        default=None, description="默认内存限制 (e.g., '1Gi')"
    )
    outbound_proxy_rules: list[K8sOutboundProxyRule] | None = Field(
        default=None,
        description="Envoy sidecar outbound proxy rewrite rules (per D-03: template-level config). When None or empty, no sidecar is injected.",
    )


class DockerTemplateConfig(BaseModel):
    """Docker platform specific configuration.

    Stored in baas_device_template.config JSON column for DOCKER templates.
    """

    type: Literal["DOCKER"] = Field(..., description="平台类型")
    image: str = Field(..., description="Docker 镜像名称")
    image_pull_policy: ImagePullPolicy = Field(
        default=ImagePullPolicy.IF_NOT_PRESENT, description="镜像拉取策略"
    )
    container_port: int = Field(..., ge=1, le=65535, description="容器内服务端口")
    health_endpoint: str = Field(default="/health", description="容器健康检查端点路径")
    health_timeout_seconds: int = Field(
        default=120,
        ge=10,
        le=600,
        description="健康检查超时秒数",
    )
    default_ttl_minutes: int = Field(default=1440, ge=1, description="默认 TTL（分钟）")
    envs: dict[str, str] | None = Field(default=None, description="环境变量")
    cpu_limit: float = Field(default=1.0, ge=0.1, le=64, description="CPU 核心数上限")
    memory_limit: str = Field(..., description="内存上限（Docker 格式: 512m, 1g 等）")


def _default_arca_config() -> ArcaTemplateConfig:
    return ArcaTemplateConfig(
        type="ARCA",
        base_url="",
        api_key="",
        arca_template_id=None,
        arca_template_id_pre=None,
        arca_template_id_prod=None,
        oss_mount_id=None,
    )


DeviceTemplateConfigUnion = (
    ArcaTemplateConfig
    | SigmaTemplateConfig
    | LocalTemplateConfig
    | PoolabTemplateConfig
    | TeClawTemplateConfig
    | K8sTemplateConfig
    | DockerTemplateConfig
)

DeviceTemplateConfig = (
    ArcaTemplateConfig
    | SigmaTemplateConfig
    | LocalTemplateConfig
    | PoolabTemplateConfig
    | TeClawTemplateConfig
    | K8sTemplateConfig
    | DockerTemplateConfig
)


# ==================== Pydantic Schemas ====================


class TemplateCreate(BaseModel):
    """创建设备模板请求"""

    template_uuid: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="多版本追踪 UUID (可选，不提供则自动生成 TEMPLATE-{uuid})",
    )
    template_id: int = Field(..., ge=0, description="PaaS平台租户业务ID")
    type: TenantType = Field(
        ..., description="PaaS平台类型: Sigma、ARCA、Local、Poolab、TeClaw"
    )
    name: str = Field(
        ..., min_length=1, max_length=64, description="模板名称: openclaw, moltis, etc."
    )
    description: str | None = Field(None, max_length=1024, description="描述")
    config: (
        ArcaTemplateConfig
        | SigmaTemplateConfig
        | LocalTemplateConfig
        | PoolabTemplateConfig
        | TeClawTemplateConfig
        | K8sTemplateConfig
        | DockerTemplateConfig
    ) = Field(
        default_factory=_default_arca_config,
        description="平台特定配置 (Sigma、ARCA、Local、Poolab、TeClaw、Docker)",
    )
    operator: str = Field(..., min_length=1, max_length=128, description="操作人 ID")


class TemplateUpdate(BaseModel):
    """更新设备模板请求"""

    template_id: int | None = Field(None, ge=1, description="PaaS平台租户业务ID")
    type: TenantType | None = Field(
        None, description="PaaS平台类型: Sigma、ARCA、Local、Poolab、TeClaw"
    )
    name: str | None = Field(None, min_length=1, max_length=64, description="模板名称")
    description: str | None = Field(None, max_length=1024, description="描述")
    config: (
        ArcaTemplateConfig
        | SigmaTemplateConfig
        | LocalTemplateConfig
        | PoolabTemplateConfig
        | TeClawTemplateConfig
        | K8sTemplateConfig
        | DockerTemplateConfig
        | None
    ) = Field(None, description="平台特定配置")
    operator: str = Field(..., min_length=1, max_length=128, description="操作人 ID")


class DeviceTemplateResponse(BaseModel):
    """设备模板响应"""

    id: int
    template_id: int
    type: str
    template_uuid: str
    tenant: str
    name: str
    description: str | None
    status: str
    config: (
        ArcaTemplateConfig
        | SigmaTemplateConfig
        | LocalTemplateConfig
        | PoolabTemplateConfig
        | TeClawTemplateConfig
        | K8sTemplateConfig
        | DockerTemplateConfig
        | None
    )
    creator: str
    modifier: str
    gmt_create: datetime
    gmt_modified: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class TemplateListResponse(BaseModel):
    """设备模板列表响应"""

    items: list[DeviceTemplateResponse]
    total: int
    page: int
    page_size: int
