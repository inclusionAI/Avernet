"""PaaS platform credentials types for type-safe factory creation.

Provides polymorphic credentials types for different PaaS platforms,
enabling type-safe credential passing and IDE autocompletion.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ._outbound_proxy_rule import K8sOutboundProxyRule


class PaasCredentials(BaseModel):
    """Base class for PaaS platform credentials.

    This is an abstract base class used for type annotations.
    Use ArcaCredentials or SigmaCredentials for actual instances.

    Template identification fields (template_id, template_uuid) are set by
    PaasServiceFactory when resolving credentials from device template config.
    tenant_name is the parent tenant name for context.
    """

    template_id: int = Field(
        default=0,
        description="PaaS平台租户业务ID，来自 baas_device_template.template_id 字段",
    )
    template_uuid: str = Field(
        default="",
        description="Device template UUID (business identifier)",
    )
    tenant_name: str | None = Field(
        default=None,
        description="Parent tenant name for context",
    )


class ArcaCredentials(PaasCredentials):
    """Credentials for Arca platform.

    Supports both explicit credentials and lazy loading from tenant configuration.
    All fields are optional to support config merge strategy where defaults
    are loaded from SystemConfigService + TenantService.

    Attributes:
        base_url: Arca API base URL (e.g)
        api_key: Arca API key for authentication
        timeout: Request timeout in seconds (default: 30.0)
        arca_template_id: Arca platform's sandbox template ID for device creation
        oss_mount_id: OSS mount configuration ID
        default_ttl_minutes: Default device lifetime in minutes (default: 1440)
        app_name: Application identifier for Arca SDK (default: "secbaas")
    """

    base_url: str | None = Field(
        default=None,
        description="Arca API base URL",
    )
    api_key: str | None = Field(
        default=None,
        description="Arca API key for authentication",
    )
    timeout: float = Field(
        default=30.0,
        description="Request timeout in seconds",
    )
    arca_template_id: str | None = Field(
        default=None,
        description="Arca platform sandbox template ID for device creation",
    )
    oss_mount_id: str | None = Field(
        default=None,
        description="OSS mount configuration ID",
    )
    default_ttl_minutes: int = Field(
        default=1440,
        description="Default device lifetime in minutes",
    )
    app_name: str = Field(
        default="secbaas",
        description="Application identifier for Arca SDK",
    )

    def is_configured(self) -> bool:
        """Check if credentials are fully configured.

        Returns True when all required credential fields are populated,
        indicating the credentials are ready for API calls.
        """
        return (
            self.base_url is not None
            and self.api_key is not None
            and self.arca_template_id is not None
        )


class SigmaCredentials(PaasCredentials):
    """Credentials for Sigma platform.

    Attributes:
        endpoint: Sigma API endpoint URL
        access_key: Sigma access key for authentication
        secret_key: Sigma secret key for authentication
        region: Target region (default: "default")
    """

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


class LocalCredentials(PaasCredentials):
    """Credentials for Local platform.

    Per D-FF02: Runtime parameters (machine_id, bot_id) come from merged config,
    not from template-stored credentials. This class inherits all fields from
    PaasCredentials (template_id, template_uuid, tenant_name) with no
    additional fields.
    """

    # Inherits template_id, template_uuid, tenant_name from PaasCredentials
    # No custom fields - minimal credential pattern per D-FF02
    pass


class PoolabCredentials(PaasCredentials):
    """Credentials for Poolab (虾池) platform.

    All Poolab-specific fields use poolab_ prefix for clear distinction
    from BaaS platform parameters inherited from PaasCredentials.

    Attributes:
        poolab_endpoint: Poolab API endpoint URL)
        poolab_tenant_id: Poolab tenant ID for X-Auth-Tenant-Id header
        poolab_tenant_token: Poolab tenant token for X-Auth-Tenant-Token header
        poolab_image_id: Poolab platform image ID for device creation
    """

    poolab_endpoint: str | None = Field(
        default=None,
        description="Poolab API endpoint URL",
    )
    poolab_tenant_id: str | None = Field(
        default=None,
        description="Poolab tenant ID for authentication",
    )
    poolab_tenant_token: str | None = Field(
        default=None,
        description="Poolab tenant token for authentication",
    )
    poolab_image_id: str | None = Field(
        default=None,
        description="Poolab platform image ID for device creation",
    )

    def is_configured(self) -> bool:
        """Check if credentials are fully configured.

        Returns True when all required credential fields are populated,
        indicating the credentials are ready for API calls.
        """
        return (
            self.poolab_endpoint is not None
            and self.poolab_tenant_id is not None
            and self.poolab_tenant_token is not None
            and self.poolab_image_id is not None
        )


class K8sCredentials(PaasCredentials):
    """Credentials for Kubernetes platform.

    All fields are optional to support config merge strategy where
    defaults are loaded from K8sTemplateConfig + environment.

    Attributes:
        kubeconfig: Inline kubeconfig YAML content for cluster authentication
        context: K8s context name within kubeconfig (optional, uses current-context)
        namespace: Target namespace for Deployment creation
        image: Container image for Bot runtime
        cpu_request: CPU request (e.g., "500m", "1")
        cpu_limit: CPU limit (e.g., "1", "2")
        memory_request: Memory request (e.g., "512Mi", "1Gi")
        memory_limit: Memory limit (e.g., "1Gi", "2Gi")
        extra_k8s_opts: Passthrough dict for K8s-specific extensions
    """

    model_config = ConfigDict(extra="forbid")

    kubeconfig: str | None = Field(
        default=None,
        description="kubeconfig YAML 内容（内联存储）用于集群认证",
    )
    context: str | None = Field(
        default=None,
        description="K8s context name within kubeconfig (uses current-context if None)",
    )
    namespace: str | None = Field(
        default=None,
        description="Target namespace for Deployment creation",
    )
    image: str | None = Field(
        default=None,
        description="Container image for Bot runtime",
    )
    cpu_request: str | None = Field(
        default=None,
        description="CPU request (e.g., '500m', '1')",
    )
    cpu_limit: str | None = Field(
        default=None,
        description="CPU limit (e.g., '1', '2')",
    )
    memory_request: str | None = Field(
        default=None,
        description="Memory request (e.g., '512Mi', '1Gi')",
    )
    memory_limit: str | None = Field(
        default=None,
        description="Memory limit (e.g., '1Gi', '2Gi')",
    )
    extra_k8s_opts: dict[str, Any] = Field(
        default_factory=dict,
        description="Passthrough dict for K8s-specific extensions (e.g., nodeSelector, tolerations)",
    )
    outbound_proxy_rules: list[K8sOutboundProxyRule] | None = Field(
        default=None,
        description="Outbound proxy rewrite rules extracted from K8sTemplateConfig (per D-03a). When None or empty, no sidecar is injected.",
    )

    def is_configured(self) -> bool:
        """Check if credentials are fully configured.

        Returns True when kubeconfig content is non-empty,
        indicating the credentials are ready for K8s API calls.
        """
        return bool(self.kubeconfig)


class TeClawCredentials(PaasCredentials):
    """Credentials for TeClaw platform.

    TeClaw 无认证机制，仅需 endpoint 即可直接 HTTP 调用。
    endpoint 由 Factory 从 TemplateConfig 填充（D-03）。
    """

    teclaw_endpoint: str | None = Field(
        default=None,
        description="TeClaw API endpoint URL",
    )
    timeout: float = Field(
        default=30.0,
        description="HTTP request timeout in seconds",
    )


class DockerCredentials(PaasCredentials):
    """Credentials for Docker platform.

    Minimal pass-through subclass per DOCKER_API-05. Inherits template_id,
    template_uuid, tenant_name from PaasCredentials with no custom fields.
    Docker auth is socket-based (docker.from_env()) and registry credentials
    are passed via envs at the PaasService layer.
    """

    # Inherits template_id, template_uuid, tenant_name from PaasCredentials
    # No custom fields - minimal credential pattern
    pass
