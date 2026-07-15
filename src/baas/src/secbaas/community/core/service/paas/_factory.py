"""PaaS Service Factory for runtime platform selection."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from secbaas.community.api.device_manage import (
    ArcaCredentials,
    DockerCredentials,
    K8sCredentials,
    LocalCredentials,
    PaasCredentials,
    PoolabCredentials,
    SigmaCredentials,
    TeClawCredentials,
)
from secbaas.community.api.paas import PaasServiceFactory as PaasServiceFactoryProtocol
from secbaas.community.api.template_manage import DeviceTemplateResponse
from secbaas.community.api.tenant_manage import TenantType
from secbaas.community.core.service.paas.desktop import get_instance_id
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger
from secbaas.community.spi.sandbox import PaasSandboxPlugins
from secbaas.community.spi.secret import SecretStorePlugin

from ._arca_paas_service import ArcaPaasService
from ._k8s_paas_service import K8sPaasService
from ._local_paas_service import LocalPaasService
from ._mock_paas_service import MockPaasService
from ._paas_service import PaasService
from ._poolab_paas_service import PoolabPaasService
from ._sigma_paas_service import SigmaPaasService
from ._standalone_paas_service import StandalonePaasService
from ._teclaw_paas_service import TeClawPaasService

if TYPE_CHECKING:
    from secbaas.community.api.paas import ConnectionManager
    from secbaas.community.core.repository.device import DeviceRepository
    from secbaas.community.core.repository.device_template import (
        DeviceTemplateRepository,
    )
    from secbaas.community.core.repository.local_user_machine import (
        LocalUserMachineRepository,
    )
    from secbaas.community.core.repository.publish_record import PublishRecordRepository
    from secbaas.community.core.repository.ws_relay_session import (
        WsRelaySessionRepository,
    )
    from secbaas.community.core.service.paas.desktop.instance_router import (
        ThreadSafeLazyRouter,
    )
    from secbaas.community.core.service.paas.desktop.worker_router import WorkerRouter
    from secbaas.community.core.service.template_manage import (
        DeviceTemplateManageService,
    )


def is_paas_mock_mode() -> bool:
    """Check if PaaS mock mode is enabled via environment variable."""
    return os.environ.get("PAAS_MOCK_MODE", "").lower() in ("true", "1", "yes")


_LOCAL_WS_CONN_MODE = "direct"


logger = get_logger("core-service")


class PaasServiceFactory(PaasServiceFactoryProtocol):
    """Factory for creating PaasService implementations at runtime.

    Enables platform selection based on DeviceTemplate configuration.
    Factory resolves template (explicit or default) and creates service
    with credentials from template.config.

    All dependencies are injected via the constructor (container-managed).
    Callers resolve a factory instance from the DI container rather than
    calling a static method.

    Example:
        factory = container.services.paas_service_factory()
        service = factory.create(tenant_name="acme_corp")
    """

    def __init__(
        self,
        template_service: DeviceTemplateManageService,
        connection_manager: ConnectionManager,
        worker_router: WorkerRouter,
        instance_router: ThreadSafeLazyRouter,
        device_template_repository: DeviceTemplateRepository,
        device_repository: DeviceRepository,
        publish_record_repository: PublishRecordRepository,
        local_user_machine_repository: LocalUserMachineRepository,
        paas_sandbox_plugins: PaasSandboxPlugins,
        secret_plugin: SecretStorePlugin,
        ws_relay_session_repository: WsRelaySessionRepository | None = None,
    ) -> None:
        """Inject all PaasServiceFactory dependencies.

        Args:
            template_service: Device template service for template resolution.
            connection_manager: ConnectionManager for WebSocket management.
            worker_router: WorkerRouter for cross-process forwarding.
            instance_router: ThreadSafeLazyRouter for cross-instance routing.
            device_template_repository: Repository for device template queries.
            device_repository: Repository for device CRUD.
            publish_record_repository: Repository for publish records.
            local_user_machine_repository: Repository for local user machine data.
            ws_relay_session_repository: WS relay session repository (Phase 65).
        """
        self._template_service = template_service
        self._connection_manager = connection_manager
        self._worker_router = worker_router
        self._instance_router = instance_router
        self._device_template_repository = device_template_repository
        self._device_repository = device_repository
        self._publish_record_repository = publish_record_repository
        self._local_user_machine_repository = local_user_machine_repository
        self._paas_sandbox_plugins = paas_sandbox_plugins
        self._secret_plugin = secret_plugin
        self._ws_relay_session_repository = ws_relay_session_repository

    def create(
        self,
        tenant_name: str,
        template_uuid: str | None = None,
        template: DeviceTemplateResponse | None = None,
    ) -> PaasService:
        """Create appropriate PaasService implementation.

        Resolves template using two-tier lookup (D-02):
        1. If template provided: use pre-resolved template (skip DB query)
        2. If template_uuid provided: use explicit template
        3. Otherwise: look up default_template_uuid from tenant config

        Args:
            tenant_name: Business tenant name for template resolution
            template_uuid: Optional explicit template UUID. If None, uses tenant's default.
            template: Optional pre-resolved DeviceTemplateResponse. When provided,
                skips template resolution (saves 1-2 DB queries).

        Returns:
            PaasService implementation instance (ArcaPaasService, SigmaPaasService,
            LocalPaasService, PoolabPaasService, TeClawPaasService, or K8sPaasService)

        Raises:
            ValueError: If tenant/template not found, or template doesn't belong to tenant
        """
        if template is None:
            template = self._template_service.get_default_or_explicit_template(
                tenant=tenant_name, template_uuid=template_uuid
            )

        # Mock mode: return MockPaasService without touching real PaaS
        if is_paas_mock_mode():
            logger.info(
                f"[create] Mock mode enabled, returning MockPaasService for tenant={tenant_name}"
            )
            return MockPaasService(
                credentials=PaasCredentials(
                    template_id=template.template_id,
                    template_uuid=template.template_uuid,
                    tenant_name=tenant_name,
                )
            )

        # Security validation (D-02): template must belong to tenant
        if template.tenant != tenant_name:
            logger.error(
                f"[create] Template {template.template_uuid} belongs to tenant "
                f"{template.tenant}, not {tenant_name}"
            )
            raise ValueError(
                f"Template {template.template_uuid} does not belong to tenant {tenant_name}"
            )

        # Determine platform type from template
        if not template.type:
            logger.error(
                f"[create] Template {template.template_uuid} has no type configured"
            )
            raise ValueError(
                f"Template {template.template_uuid} has no type configured"
            )
        platform_upper = template.type.upper()

        logger.info(
            f"[create] Creating PaasService: tenant={tenant_name}, "
            f"template_uuid={template.template_uuid}, platform={platform_upper}"
        )

        # Create platform-specific service with credentials from template.config
        if platform_upper == TenantType.ARCA.value.upper():
            arca_creds = self._create_arca_credentials_from_template(template)
            arca_sandbox_plugin = (
                self._paas_sandbox_plugins.arca_sandbox_plugin_factory(
                    arca_creds,
                )
            )
            logger.info(
                f"[create] Created ArcaPaasService: template_uuid={template.template_uuid}, "
                f"plugin={type(arca_sandbox_plugin).__name__}"
            )
            return ArcaPaasService(
                credentials=arca_creds, arca_sandbox_plugin=arca_sandbox_plugin
            )

        elif platform_upper == TenantType.SIGMA.value.upper():
            sigma_creds = self._create_sigma_credentials_from_template(template)
            logger.info(
                f"[create] Created SigmaPaasService: template_uuid={template.template_uuid}"
            )
            return SigmaPaasService(credentials=sigma_creds)

        elif platform_upper == TenantType.LOCAL.value.upper():
            local_creds = self._create_local_credentials_from_template(template)
            logger.info(
                f"[create] Created LocalPaasService: template_uuid={template.template_uuid}"
            )
            return LocalPaasService(
                credentials=local_creds,
                repository=self._local_user_machine_repository,
                connection_manager=self._connection_manager,
                instance_router=self._instance_router,
                server_ip=get_instance_id(),
                env=get_current_env(),
                device_template_repository=self._device_template_repository,
                device_repository=self._device_repository,
                publish_record_repository=self._publish_record_repository,
                worker_router=self._worker_router,
                desktop_sandbox_plugin=self._paas_sandbox_plugins.desktop_sandbox_plugin,
                relay_repository=self._ws_relay_session_repository,
                ws_conn_mode=_LOCAL_WS_CONN_MODE,
            )

        elif platform_upper == TenantType.POOLAB.value.upper():
            poolab_creds = self._create_poolab_credentials_from_template(template)
            factory = self._paas_sandbox_plugins.poolab_sandbox_plugin_factory
            if factory is None:
                raise RuntimeError("Poolab sandbox plugin factory is not configured")
            poolab_plugin = factory(poolab_creds)
            logger.info(
                f"[create] Created PoolabPaasService: template_uuid={template.template_uuid}, "
                f"plugin={type(poolab_plugin).__name__}"
            )
            return PoolabPaasService(credentials=poolab_creds, plugin=poolab_plugin)

        elif platform_upper == TenantType.TECLAW.value.upper():
            teclaw_creds = self._create_teclaw_credentials_from_template(template)
            teclaw_plugin = self._paas_sandbox_plugins.teclaw_bot_plugin_factory(
                teclaw_creds.teclaw_endpoint,
                teclaw_creds.timeout,
            )
            logger.info(
                f"[create] Created TeClawPaasService: template_uuid={template.template_uuid}, "
                f"plugin={type(teclaw_plugin).__name__}"
            )
            return TeClawPaasService(plugin=teclaw_plugin, credentials=teclaw_creds)

        elif platform_upper == TenantType.K8S.value.upper():
            k8s_creds = self._create_k8s_credentials_from_template(template)
            k8s_factory = self._paas_sandbox_plugins.k8s_sandbox_plugin_factory
            if k8s_factory is None:
                raise RuntimeError(
                    "k8s_sandbox_plugin_factory is not configured "
                    "(container wiring missing K8S sandbox plugin)"
                )
            k8s_plugin = k8s_factory(k8s_creds)
            logger.info(
                f"[create] Created K8sPaasService: template_uuid={template.template_uuid}, "
                f"plugin={type(k8s_plugin).__name__}"
            )
            return K8sPaasService(plugin=k8s_plugin, credentials=k8s_creds)
        elif platform_upper == TenantType.DOCKER.value.upper():
            docker_creds = self._create_docker_credentials_from_template(template)
            docker_plugin = self._paas_sandbox_plugins.docker_sandbox_plugin
            if docker_plugin is None:
                raise RuntimeError(
                    "docker_sandbox_plugin is not configured "
                    "(container wiring missing Docker sandbox plugin)"
                )
            config = template.config  # DockerTemplateConfig
            logger.info(
                f"[create] Created StandalonePaasService: "
                f"template_uuid={template.template_uuid}, "
                f"plugin={type(docker_plugin).__name__}"
            )
            return StandalonePaasService(
                plugin=docker_plugin,
                credentials=docker_creds,
                health_endpoint=config.health_endpoint,
                health_timeout_seconds=config.health_timeout_seconds,
            )

        else:
            supported = [
                TenantType.ARCA.value,
                TenantType.SIGMA.value,
                TenantType.LOCAL.value,
                TenantType.POOLAB.value,
                TenantType.TECLAW.value,
                TenantType.K8S.value,
                TenantType.DOCKER.value,
            ]
            logger.error(
                f"[create] Unsupported platform type: {template.type}, "
                f"supported: {supported}"
            )
            raise ValueError(
                f"Unsupported platform type: {template.type}. "
                f"Supported platforms: {supported}"
            )

    def _create_arca_credentials_from_template(
        self, template: DeviceTemplateResponse
    ) -> ArcaCredentials:
        """Create ArcaCredentials from template config.

        Args:
            template: Resolved DeviceTemplate with config containing PaaS credentials

        Returns:
            Populated ArcaCredentials instance

        Raises:
            ValueError: If template.config is invalid or missing required fields
        """
        from secbaas.community.api.template_manage import ArcaTemplateConfig

        if not template.config:
            raise ValueError(
                f"Invalid config for template {template.template_uuid}: config is None"
            )

        # Ensure we have ArcaTemplateConfig
        if isinstance(template.config, ArcaTemplateConfig):
            config = template.config
        else:
            raise ValueError(
                f"Invalid config type for ARCA template {template.template_uuid}: "
                "expected ArcaTemplateConfig"
            )

        # Environment-aware template_id selection (Phase 9.16)
        env = get_current_env()
        effective_template_id = config.get_effective_template_id(env)

        # Decrypt api_key if encrypted (D-13.01)
        api_key = config.api_key
        if config.encrypt_api_key:
            from secbaas.community.core.utils.secret_utils import common_sm4_decrypt

            key = self._secret_plugin.resolve_common_sm4_key()
            api_key = common_sm4_decrypt(config.api_key, key)

        return ArcaCredentials(
            template_id=template.template_id,
            template_uuid=template.template_uuid,
            tenant_name=template.tenant,
            base_url=config.base_url,
            api_key=api_key,
            app_name=config.app_name,
            arca_template_id=effective_template_id,
            oss_mount_id=config.oss_mount_id,
            default_ttl_minutes=config.default_ttl_minutes,
            timeout=config.timeout,
        )

    def _create_poolab_credentials_from_template(
        self, template: DeviceTemplateResponse
    ) -> PoolabCredentials:
        """Create PoolabCredentials from template config.

        Args:
            template: Resolved DeviceTemplate with config containing PaaS credentials

        Returns:
            Populated PoolabCredentials instance

        Raises:
            ValueError: If template.config is invalid or missing required fields
        """
        from secbaas.community.api.template_manage import PoolabTemplateConfig

        if not template.config:
            raise ValueError(
                f"Invalid config for template {template.template_uuid}: config is None"
            )

        if isinstance(template.config, PoolabTemplateConfig):
            config = template.config
        else:
            raise ValueError(
                f"Invalid config type for POOLAB template {template.template_uuid}: "
                "expected PoolabTemplateConfig"
            )

        env = get_current_env()
        effective_endpoint = config.get_effective_endpoint(env)
        effective_image_id = config.get_effective_image_id(env)

        tenant_token = config.poolab_tenant_token
        if config.encrypt_tenant_token:
            from secbaas.community.core.utils.secret_utils import common_sm4_decrypt

            key = self._secret_plugin.resolve_common_sm4_key()
            tenant_token = common_sm4_decrypt(config.poolab_tenant_token, key)

        return PoolabCredentials(
            template_id=template.template_id,
            template_uuid=template.template_uuid,
            tenant_name=template.tenant,
            poolab_endpoint=effective_endpoint,
            poolab_tenant_id=config.poolab_tenant_id,
            poolab_tenant_token=tenant_token,
            poolab_image_id=effective_image_id,
        )

    @staticmethod
    def _create_sigma_credentials_from_template(
        template: DeviceTemplateResponse,
    ) -> SigmaCredentials:
        """Create SigmaCredentials from template config.

        Args:
            template: Resolved DeviceTemplate with config containing PaaS credentials

        Returns:
            Populated SigmaCredentials instance

        Raises:
            ValueError: If template.config is invalid or missing required fields
        """
        from secbaas.community.api.template_manage import SigmaTemplateConfig

        if not template.config:
            raise ValueError(
                f"Invalid config for template {template.template_uuid}: config is None"
            )

        # Ensure we have SigmaTemplateConfig
        if isinstance(template.config, SigmaTemplateConfig):
            config = template.config
        else:
            raise ValueError(
                f"Invalid config type for Sigma template {template.template_uuid}: "
                "expected SigmaTemplateConfig"
            )

        return SigmaCredentials(
            template_id=template.template_id,
            template_uuid=template.template_uuid,
            tenant_name=template.tenant,
            endpoint=config.endpoint,
            access_key=config.access_key,
            secret_key=config.secret_key,
            region=config.region,
        )

    @staticmethod
    def _create_local_credentials_from_template(
        template: DeviceTemplateResponse,
    ) -> LocalCredentials:
        """Create LocalCredentials from template.

        Per D-FF03: Local platform credentials are minimal - no validation,
        no custom fields. Runtime parameters (machine_id, bot_id) come from
        merged config during create_device(), not from template-stored credentials.

        Args:
            template: Resolved DeviceTemplate with basic template info.

        Returns:
            LocalCredentials with template_id, template_uuid, tenant_name only.
        """
        # Per D-FF03: No validation - minimal credential creation
        return LocalCredentials(
            template_id=template.template_id,
            template_uuid=template.template_uuid,
            tenant_name=template.tenant,
            # Note: owner_user_id, target_machine_id, bot_id are NOT set here per D-FF02
            # They come from merged_detail_config during facade.create_device()
        )

    @staticmethod
    def _create_docker_credentials_from_template(
        template: DeviceTemplateResponse,
    ) -> DockerCredentials:
        """Create DockerCredentials from template.

        Docker credentials are minimal — template_id, template_uuid, tenant_name
        only. Docker auth is socket-based (docker.from_env()) with no custom
        credential fields.

        Args:
            template: Resolved DeviceTemplate with config containing Docker settings.

        Returns:
            Populated DockerCredentials instance.

        Raises:
            ValueError: If template.config is None or not a DockerTemplateConfig.
        """
        from secbaas.community.api.template_manage import DockerTemplateConfig

        if not template.config:
            raise ValueError(
                f"Invalid config for template {template.template_uuid}: config is None"
            )

        if not isinstance(template.config, DockerTemplateConfig):
            raise ValueError(
                f"Invalid config type for DOCKER template {template.template_uuid}: "
                "expected DockerTemplateConfig"
            )

        return DockerCredentials(
            template_id=template.template_id,
            template_uuid=template.template_uuid,
            tenant_name=template.tenant,
        )

    @staticmethod
    def _create_teclaw_credentials_from_template(
        template: DeviceTemplateResponse,
    ) -> TeClawCredentials:
        """Create TeClawCredentials from template config.

        Args:
            template: Resolved DeviceTemplate with config containing PaaS credentials

        Returns:
            Populated TeClawCredentials instance

        Raises:
            ValueError: If template.config is invalid or missing required fields
        """
        from secbaas.community.api.template_manage import TeClawTemplateConfig

        if not template.config:
            raise ValueError(
                f"Invalid config for template {template.template_uuid}: config is None"
            )

        if isinstance(template.config, TeClawTemplateConfig):
            config = template.config
        else:
            raise ValueError(
                f"Invalid config type for TECLAW template {template.template_uuid}: "
                "expected TeClawTemplateConfig"
            )

        return TeClawCredentials(
            template_id=template.template_id,
            template_uuid=template.template_uuid,
            tenant_name=template.tenant,
            teclaw_endpoint=config.teclaw_endpoint,
            timeout=config.timeout,
        )

    @staticmethod
    def _create_k8s_credentials_from_template(
        template: DeviceTemplateResponse,
    ) -> K8sCredentials:
        """Create K8sCredentials from template config.

        Args:
            template: Resolved DeviceTemplate with config containing PaaS credentials

        Returns:
            Populated K8sCredentials instance

        Raises:
            ValueError: If template.config is invalid or missing required fields
        """
        from secbaas.community.api.template_manage import K8sTemplateConfig

        if not template.config:
            raise ValueError(
                f"Invalid config for template {template.template_uuid}: config is None"
            )

        if isinstance(template.config, K8sTemplateConfig):
            config = template.config
        else:
            raise ValueError(
                f"Invalid config type for K8S template {template.template_uuid}: "
                "expected K8sTemplateConfig"
            )

        return K8sCredentials(
            template_id=template.template_id,
            template_uuid=template.template_uuid,
            tenant_name=template.tenant,
            kubeconfig=config.kubeconfig,
            namespace=config.namespace,
            image=config.image,
            cpu_request=config.cpu_request,
            cpu_limit=config.cpu_limit,
            memory_request=config.memory_request,
            memory_limit=config.memory_limit,
            outbound_proxy_rules=config.outbound_proxy_rules,
        )

    def create_local_paas_service(
        self,
        user_id: str,
        machine_id: str,
        env: str | None = None,
    ) -> LocalPaasService:
        """Create LocalPaasService with direct credentials (no template resolution).

        Uses the factory's already-injected dependencies. No container access.

        Args:
            user_id: The owner user ID from login context.
            machine_id: The target machine ID for this service instance.
            env: Environment identifier (dev, pre, prod). If None, auto-detected.

        Returns:
            Configured LocalPaasService ready for mng register/heartbeat handling.
        """
        logger.info(
            f"[create_local_paas_service] Creating LocalPaasService: "
            f"user_id={user_id}, machine_id={machine_id}, env={env}"
        )
        credentials = LocalCredentials(
            template_id=0,
            template_uuid=f"direct-{user_id}",
            tenant_name=None,
        )

        return LocalPaasService(
            credentials=credentials,
            repository=self._local_user_machine_repository,
            connection_manager=self._connection_manager,
            instance_router=self._instance_router,
            server_ip=get_instance_id(),
            desktop_sandbox_plugin=self._paas_sandbox_plugins.desktop_sandbox_plugin,
            env=env or get_current_env(),
            device_template_repository=self._device_template_repository,
            device_repository=self._device_repository,
            publish_record_repository=self._publish_record_repository,
            worker_router=self._worker_router,
            relay_repository=self._ws_relay_session_repository,
            ws_conn_mode=_LOCAL_WS_CONN_MODE,
        )
