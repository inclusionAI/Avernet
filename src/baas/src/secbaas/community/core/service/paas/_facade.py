"""PaaS Service Facade for unified device lifecycle operations.

Provides a simplified, unified interface for callers to manage device lifecycle
without handling Factory and Service coordination details directly.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from secbaas.community.api.bot_manage import FetchStartProgressResult  # noqa: F401

from secbaas.community.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.community.api.device_manage import (
    ArcaCreateConfig,
    ArcaCreationResult,
    ArcaDeviceConfig,
    CommandResult,
    DeviceCreateConfig,
    DeviceCreationError,
    DeviceCreationResult,
    DeviceFacadeException,
    DeviceInfo,
    DockerCreateConfig,
    DockerCreationResult,
    DockerDeviceConfig,
    ErrorCode,
    K8sCreateConfig,
    K8sCreationResult,
    K8sDeviceConfig,
    LocalCreateConfig,
    LocalCreationResult,
    LocalDeviceConfig,
    PaasError,
    PoolabCreateConfig,
    PoolabCreationResult,
    PoolabDeviceConfig,
    SigmaCreateConfig,
    SigmaDeviceConfig,
    TeClawCreateConfig,
    TeClawCreationResult,
    TeClawDeviceConfig,
)
from secbaas.community.api.device_manage import (
    PaasServiceFacade as PaasServiceFacadeProtocol,
)
from secbaas.community.api.tenant_manage import TenantType
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

from ._factory import PaasServiceFactory

if TYPE_CHECKING:
    from secbaas.community.api.device_manage import OutBoundOperationRule
    from secbaas.community.api.health_check.bot import TTLInfo
    from secbaas.community.api.template_manage import DeviceTemplateManageService
    from secbaas.community.core.repository.device import DeviceRepository


# Configurable timeout for HTTP invocations (seconds)
# Per WR-05: Made configurable via environment variable with 25.0s default
HTTP_INVOCATION_TIMEOUT = float(os.getenv("BAAS_HTTP_INVOCATION_TIMEOUT", "25.0"))


class PaasServiceFacade(PaasServiceFacadeProtocol):
    """Facade for PaaS Device lifecycle operations.

    Encapsulates create_device, destroy_device, and execute_command operations,
    providing a unified, simplified interface for Device lifecycle management.

    Per Decision D-10: This is a pure PaaS operation layer with no DB operations.
    Device record maintenance is the caller's responsibility.

    Device ID Format (D-04, D-05):
    - create_device returns: "{platform_device_id}@{template_id}" (e.g., "sandbox-abc123@42")
    - destroy_device/execute_command accept: "{platform_device_id}@{template_id}"
    - Backward compatibility: IDs without @ suffix are treated as template_id=0

    Note: This facade handles the @template_id suffix encoding/decoding for paas_device_id.
    The underlying PaasService implementations receive/process the raw device_id without
    the @template_id suffix.

    Error Handling (D-06):
    - PaasError from lower layers is wrapped as DeviceFacadeException
    - Wrapped exception includes context: operation, platform_type, template_id, paas_device_id
    - Provides user-friendly error messages for problem diagnosis

    NotImplementedError Guard Policy (D-06 extension):
    - Facade methods catch NotImplementedError when the base-class PaasService method
      explicitly declares it as a valid exception in its docstring (e.g., update_device_ttl,
      open_folder) or when a known concrete implementation voluntarily raises it (e.g.,
      PoolabPaasService raises NotImplementedError for update_outbound_operation_rule).
    - Abstract methods with "..." (ellipsis) bodies that raise TypeError at instantiation
      time rather than NotImplementedError at call time do not require a guard in the
      facade (e.g., create_device, destroy_device, execute_command).
    - Behavioral note: NotImplementedError is NOT a subclass of PaasError, so ordering
      between the two except blocks is purely for code clarity. The established convention
      places except NotImplementedError before except PaasError.

    Tenant Resolution:
    - create_device: Extracts tenant_name and template_uuid from config, delegates to Factory
    - destroy/execute: Parses template_id from paas_device_id suffix, looks up template
    """

    def __init__(
        self,
        device_repository: DeviceRepository,
        device_template_service: DeviceTemplateManageService,
        factory: PaasServiceFactory,
    ) -> None:
        self._logger = get_logger("core-service")
        self.device_repository = device_repository
        self._device_template_service = device_template_service
        self._factory = factory

    @staticmethod
    def _parse_device_id(paas_device_id: str) -> tuple[str, int]:
        """Parse device ID and template ID from paas_device_id string.

        Per Decision D-04:
        - New format: "{platform_device_id}@{template_id}" (e.g., "sandbox-abc123@123")
        - Old format (backward compat): no @ suffix = template_id = 0

        Args:
            paas_device_id: Device ID string, optionally with @template_id suffix

        Returns:
            Tuple of (device_id: str, template_id: int)

        Example:
            "sandbox-abc123@123" -> ("sandbox-abc123", 123)
            "legacy-device-id" -> ("legacy-device-id", 0)
        """
        if "@" in paas_device_id:
            device_id, template_str = paas_device_id.rsplit("@", 1)
            try:
                template_id = int(template_str)
            except ValueError:
                # Invalid suffix format, treat whole string as device_id with template_id=0
                return paas_device_id, 0
            return device_id, template_id
        return paas_device_id, 0

    @staticmethod
    def _validate_port(port: int) -> None:
        """Validate port is an integer in the range 1-65535.

        Raises:
            ValueError: If port is not a valid integer in range.
        """
        if (
            not isinstance(port, int)
            or isinstance(port, bool)
            or not (1 <= port <= 65535)
        ):
            raise ValueError(f"port must be an integer between 1-65535, got {port!r}")

    @staticmethod
    async def _get_platform_type(service: Any) -> str:
        """Get platform type from service instance.

        Per D-FF04: Uses explicit get_platform_type() method instead of
        fragile class name inference. Falls back to class name for legacy.

        Args:
            service: PaasService instance or None

        Returns:
            Platform type string ("ARCA", "SIGMA", "LOCAL", "POOLAB", "TECLAW", "K8S", or "UNKNOWN")
        """
        if service is None:
            return "UNKNOWN"
        # Per D-FF04: Use explicit method if available and returns a valid value
        if hasattr(service, "get_platform_type") and callable(
            getattr(service, "get_platform_type")
        ):
            platform_type = await service.get_platform_type()
            # Check if result looks like a valid enum (has value attribute with string)
            if platform_type is not None and hasattr(platform_type, "value"):
                value = platform_type.value
                if isinstance(value, str):
                    return value.upper()
        # Fallback to class name inference (legacy, can be removed after full migration)
        class_name = service.__class__.__name__.lower()
        if "arca" in class_name:
            return "ARCA"
        if "sigma" in class_name:
            return "SIGMA"
        if "local" in class_name:
            return "LOCAL"
        if "poolab" in class_name:
            return "POOLAB"
        if "teclaw" in class_name:
            return "TECLAW"
        if "k8s" in class_name:
            return "K8S"
        if "standalone" in class_name:
            return "DOCKER"
        return "UNKNOWN"

    # Allowed override fields for detail_config (CreateConfig part only)
    # Credentials fields (tenant_name, base_url, api_key, etc.) are NOT allowed
    # Note: arca_template_id maps to ArcaCreateConfig.template_id
    _ARCA_ALLOWED_OVERRIDE_FIELDS: set[str] = {
        "arca_template_id",
        "ttl_in_minutes",
        "name",
        "description",
        "mount_points",
        "envs",
        "resource_spec",
        "metadata",
        "oss_mount_id",
        "outbound_operation_rule",
        "storage",
        "docker_image",
    }

    _SIGMA_ALLOWED_OVERRIDE_FIELDS: set[str] = {
        "name",
        "description",
        "zone",
        "vpc_config",
        "resource_spec",
        "metadata",
    }

    _LOCAL_ALLOWED_OVERRIDE_FIELDS: set[str] = {
        "machine_id",
        "tc_bot_id",
        "envs",
        "user_id",
        "agent_code",
        "credentials",
        "engine_type",
        "mount_path",
        "name",
        "description",
    }

    _POOLAB_ALLOWED_OVERRIDE_FIELDS: set[str] = {
        "poolab_user_id",
        "poolab_tenant_id",
        "poolab_image_id",
        "poolab_envs",
        "poolab_spec",
        "name",
        "description",
    }

    _TECLAW_ALLOWED_OVERRIDE_FIELDS: set[str] = {
        "description",
        "name",
        "teclaw_bot_config",
    }

    # K8s credentials (kubeconfig, namespace, image, resources) come from
    # template config via K8sCredentials, not from detail_config. Only
    # name/description from DeviceCreateConfig base may be overridden.
    _K8S_ALLOWED_OVERRIDE_FIELDS: set[str] = {
        "name",
        "description",
    }
    _DOCKER_ALLOWED_OVERRIDE_FIELDS: set[str] = {
        "container_port",
        "cpu_limit",
        "description",
        "envs",
        "image",
        "memory_limit",
        "name",
    }

    # Field name mapping from detail_config field to merged config field
    _ARCA_FIELD_MAP: dict[str, str] = {
        "arca_template_id": "template_id",
    }

    @staticmethod
    def _is_provided(value: Any) -> bool:
        """Check if a value is 'provided' for merge purposes.

        Per Phase 9.1 D-02: Empty strings treated as "not provided"
        """
        return value is not None and value != ""

    def _merge_config(
        self,
        template_config: Any,
        detail_config: Any,
        platform_type: str,
    ) -> dict[str, Any]:
        """Merge configs: detail_config > template_config (D-01).

        Shallow merge (top-level fields only):
        - Fields present in detail_config override template_config
        - Fields not in detail_config but in template_config use template value
        - Fields only in detail_config are added to merged config
        - Cross-platform config mismatch raises ValueError

        Security: Only CreateConfig fields are allowed in detail_config.
        Credentials fields (base_url, api_key, etc.) are filtered out
        as they are determined by the resolved template, not caller override.

        Args:
            template_config: Config from resolved template (base)
            detail_config: Caller-provided override config (CreateConfig fields only)
            platform_type: "ARCA", "SIGMA", "LOCAL", "POOLAB", "TECLAW", or "DOCKER"

        Returns:
            Merged config dict for service.create_device()

        Raises:
            ValueError: If detail_config type doesn't match platform_type
        """
        # Validate detail_config matches platform_type
        if detail_config is not None:
            is_arca_config = (
                hasattr(detail_config, "to_create_config")
                and "arca" in detail_config.__class__.__name__.lower()
            )
            is_sigma_config = (
                hasattr(detail_config, "to_create_config")
                and "sigma" in detail_config.__class__.__name__.lower()
            )
            is_local_config = (
                hasattr(detail_config, "to_create_config")
                and "local" in detail_config.__class__.__name__.lower()
            )
            is_poolab_config = (
                hasattr(detail_config, "to_create_config")
                and "poolab" in detail_config.__class__.__name__.lower()
            )
            is_teclaw_config = (
                hasattr(detail_config, "to_create_config")
                and "teclaw" in detail_config.__class__.__name__.lower()
            )
            is_k8s_config = (
                hasattr(detail_config, "to_create_config")
                and "k8s" in detail_config.__class__.__name__.lower()
            )
            is_docker_config = (
                hasattr(detail_config, "to_create_config")
                and "docker" in detail_config.__class__.__name__.lower()
            )

            if platform_type == "ARCA" and not is_arca_config:
                raise ValueError(
                    f"detail_config must be ArcaDeviceConfig for ARCA platform, "
                    f"got {type(detail_config).__name__}"
                )
            if platform_type == "SIGMA" and not is_sigma_config:
                raise ValueError(
                    f"detail_config must be SigmaDeviceConfig for SIGMA platform, "
                    f"got {type(detail_config).__name__}"
                )
            if platform_type == "LOCAL" and not is_local_config:
                raise ValueError(
                    f"detail_config must be LocalDeviceConfig for LOCAL platform, "
                    f"got {type(detail_config).__name__}"
                )
            if platform_type == "POOLAB" and not is_poolab_config:
                raise ValueError(
                    f"detail_config must be PoolabDeviceConfig for POOLAB platform, "
                    f"got {type(detail_config).__name__}"
                )
            if platform_type == "TECLAW" and not is_teclaw_config:
                raise ValueError(
                    f"detail_config must be TeClawDeviceConfig for TECLAW platform, "
                    f"got {type(detail_config).__name__}"
                )
            if platform_type == "K8S" and not is_k8s_config:
                raise ValueError(
                    f"detail_config must be K8sDeviceConfig for K8S platform, "
                    f"got {type(detail_config).__name__}"
                )
            if platform_type == "DOCKER" and not is_docker_config:
                raise ValueError(
                    f"detail_config must be DockerDeviceConfig for DOCKER platform, "
                    f"got {type(detail_config).__name__}"
                )

        # Start with template_config as base if exists
        # Note: arca_template_id_pre/prod are NOT included in merge as they are
        # environment-specific selectors used to determine the effective template_id
        # before merging (see Phase 9.16 design decision)
        field_map = self._ARCA_FIELD_MAP if platform_type == "ARCA" else {}
        if template_config is not None:
            merged = template_config.model_dump(exclude_none=True, by_alias=False)
            # Apply field mapping for template_config fields (e.g., arca_template_id -> template_id)
            if platform_type == "ARCA":
                # Get environment-aware template_id before removing env-specific fields
                env = get_current_env()
                effective_template_id = template_config.get_effective_template_id(env)
                # Remove environment-specific template_id fields (not for ArcaCreateConfig)
                merged.pop("arca_template_id_pre", None)
                merged.pop("arca_template_id_prod", None)
                # Apply field mapping with environment-aware value
                for source_key, target_key in field_map.items():
                    if (
                        source_key == "arca_template_id"
                        and effective_template_id is not None
                    ):
                        merged[target_key] = effective_template_id
                        merged.pop(source_key, None)
                    elif source_key in merged:
                        merged[target_key] = merged.pop(source_key)
        else:
            merged = {}

        # Overlay detail_config if provided (filtering to allowed fields only)
        if detail_config is not None:
            allowed_fields = (
                self._ARCA_ALLOWED_OVERRIDE_FIELDS
                if platform_type == "ARCA"
                else self._SIGMA_ALLOWED_OVERRIDE_FIELDS
                if platform_type == "SIGMA"
                else self._LOCAL_ALLOWED_OVERRIDE_FIELDS
                if platform_type == "LOCAL"
                else self._POOLAB_ALLOWED_OVERRIDE_FIELDS
                if platform_type == "POOLAB"
                else self._TECLAW_ALLOWED_OVERRIDE_FIELDS
                if platform_type == "TECLAW"
                else self._K8S_ALLOWED_OVERRIDE_FIELDS
                if platform_type == "K8S"
                else self._DOCKER_ALLOWED_OVERRIDE_FIELDS
                if platform_type == "DOCKER"
                else set()
            )
            detail_dict = detail_config.model_dump(exclude_none=True)
            for key, value in detail_dict.items():
                if key in allowed_fields and self._is_provided(value):
                    # Map detail_config field name to merged config field name
                    merged_key = field_map.get(key, key)
                    merged[merged_key] = value
                elif key not in allowed_fields:
                    # Log warning for filtered fields (security audit)
                    self._logger.warning(
                        f"detail_config field '{key}' is not allowed for override "
                        f"and will be ignored. Allowed fields: {allowed_fields}"
                    )

        return dict[str, Any](merged)

    async def create_device(
        self,
        tenant_name: str,
        device_template_uuid: str | None = None,
        detail_config: ArcaDeviceConfig
        | SigmaDeviceConfig
        | LocalDeviceConfig
        | PoolabDeviceConfig
        | TeClawDeviceConfig
        | K8sDeviceConfig
        | DockerDeviceConfig
        | None = None,
    ) -> DeviceCreationResult:
        """Create a device on the PaaS platform.

        Per D-02: Template resolution follows priority:
        1. Use device_template_uuid if provided
        2. Fallback to tenant.extra_config.default_template_uuid

        Per D-01: Config merge follows priority:
        - detail_config fields override template.config
        - Merged config used for PaaS service creation

        Per D-04: Returns paas_device_id with @template_id suffix.

        Args:
            tenant_name: Business tenant name (required per D-04)
            device_template_uuid: Optional explicit template UUID
            detail_config: Optional config overrides (ArcaDeviceConfig, SigmaDeviceConfig, LocalDeviceConfig, PoolabDeviceConfig, TeClawDeviceConfig, or K8sDeviceConfig)

        Returns:
            DeviceCreationResult with paas_device_id format: "{platform_device_id}@{template_id}"

        Raises:
            ValueError: If tenant_name is empty or config type mismatch
            DeviceFacadeException: If creation fails (wrapped with context)
        """
        if not tenant_name:
            raise ValueError("tenant_name parameter is required")

        platform_str = ""
        template_id = 0
        try:
            template = self._device_template_service.get_default_or_explicit_template(
                tenant=tenant_name,
                template_uuid=device_template_uuid,
            )

            # Determine platform_type from template.type
            platform_str = template.type.upper() if template.type else ""

            # Merge configs using D-01 shallow merge
            merged_config = self._merge_config(
                template.config, detail_config, platform_str
            )

            # Create service via Factory using resolved template
            service = self._factory.create(
                tenant_name=tenant_name,
                template_uuid=template.template_uuid,
                template=template,
            )

            # Get template_id from service's resolved credentials
            credentials = await service.get_credentials()
            template_id = credentials.template_id or 0

            self._logger.info(
                f"Creating {platform_str} device with template_id={template_id}"
            )

            # Convert merged dict to appropriate config type based on platform
            create_config: (
                ArcaCreateConfig
                | K8sCreateConfig
                | SigmaCreateConfig
                | LocalCreateConfig
                | PoolabCreateConfig
                | TeClawCreateConfig
                | DockerCreateConfig
            )
            if platform_str == TenantType.ARCA.value.upper():
                create_config = ArcaCreateConfig.model_validate(merged_config)
            elif platform_str == TenantType.K8S.value.upper():
                create_config = K8sCreateConfig.model_validate(merged_config)
            elif platform_str == TenantType.SIGMA.value.upper():
                create_config = SigmaCreateConfig.model_validate(merged_config)
            elif platform_str == TenantType.LOCAL.value.upper():
                create_config = LocalCreateConfig.model_validate(merged_config)
            elif platform_str == TenantType.POOLAB.value.upper():
                create_config = PoolabCreateConfig.model_validate(merged_config)
            elif platform_str == TenantType.TECLAW.value.upper():
                create_config = TeClawCreateConfig.model_validate(merged_config)
            elif platform_str == TenantType.DOCKER.value.upper():
                create_config = DockerCreateConfig.model_validate(merged_config)
            else:
                raise ValueError(f"Unknown platform type: {platform_str}")

            # Create device using merged config
            result = await service.create_device(create_config)
            self._logger.info(
                f"[create_device] {platform_str} create_device result: "
                f"type={type(result).__name__}, content={result}"
            )

            # Encode template_id into device ID per D-04
            if platform_str == TenantType.ARCA.value.upper():
                if isinstance(result, ArcaCreationResult):
                    # Append @template_id suffix to sandbox_id
                    suffixed_id = f"{result.sandbox_id}@{template_id}"
                    self._logger.info(f"Arca device created: {suffixed_id}")
                    # Return new result with suffixed ID (preserve other fields)
                    return ArcaCreationResult(
                        platform=result.platform,
                        status=result.status,
                        template_id=result.template_id,
                        sandbox_id=suffixed_id,  # Suffix applied per D-04
                        resources=result.resources,
                        ttl_in_minutes=result.ttl_in_minutes,
                        envs=result.envs,
                        snapshot_id=result.snapshot_id,
                        metadata=result.metadata,
                        outbound_operation_rule=result.outbound_operation_rule,
                    )

            # For Sigma and other platforms (future proofing)
            if platform_str == TenantType.SIGMA.value.upper():
                raise PaasError(
                    ErrorCode.DEVICE_CREATION_FAILED,
                    "Sigma device creation not yet implemented",
                )

            if platform_str == TenantType.LOCAL.value.upper():
                if isinstance(result, LocalCreationResult):
                    # Per D-FF07: Construct suffixed ID: container_id@template_id
                    suffixed_id = f"{result.container_id}@{template_id}"
                    self._logger.info(f"Local device created: {suffixed_id}")
                    # Return LocalCreationResult with suffixed ID
                    return LocalCreationResult(
                        container_id=suffixed_id,  # Suffix applied per D-FF07
                        platform=result.platform,
                        status=result.status,
                    )

            if platform_str == TenantType.POOLAB.value.upper():
                if isinstance(result, PoolabCreationResult):
                    suffixed_id = f"{result.poolab_id}@{template_id}"
                    self._logger.info(f"Poolab device created: {suffixed_id}")
                    return PoolabCreationResult(
                        platform=result.platform,
                        status=result.status,
                        poolab_id=suffixed_id,
                        poolab_user_id=result.poolab_user_id,
                        poolab_user_nick=result.poolab_user_nick,
                        poolab_hostname=result.poolab_hostname,
                        poolab_image_id=result.poolab_image_id,
                        poolab_order_id=result.poolab_order_id,
                        poolab_status=result.poolab_status,
                        poolab_openclaw_url=result.poolab_openclaw_url,
                        poolab_openclaw_token=result.poolab_openclaw_token,
                        poolab_display_status=result.poolab_display_status,
                        poolab_type=result.poolab_type,
                        poolab_network_type=result.poolab_network_type,
                        poolab_operations_url=result.poolab_operations_url,
                        poolab_remote_url=result.poolab_remote_url,
                        poolab_model_config_type=result.poolab_model_config_type,
                        poolab_env=result.poolab_env,
                    )

            if platform_str == TenantType.TECLAW.value.upper():
                if isinstance(result, TeClawCreationResult):
                    suffixed_id = f"{result.teclaw_bot_id}@{template_id}"
                    self._logger.info(f"TeClaw device created: {suffixed_id}")
                    return TeClawCreationResult(
                        platform=result.platform,
                        status=result.status,
                        teclaw_bot_id=suffixed_id,
                        teclaw_bot_config=result.teclaw_bot_config,
                    )

            if platform_str == TenantType.K8S.value.upper():
                if isinstance(result, K8sCreationResult):
                    suffixed_id = f"{result.device_id}@{template_id}"
                    self._logger.info(f"K8s device created: {suffixed_id}")
                    return K8sCreationResult(
                        device_id=suffixed_id,
                    )

            if platform_str == TenantType.DOCKER.value.upper():
                if isinstance(result, DockerCreationResult):
                    suffixed_id = f"{result.container_id}@{template_id}"
                    self._logger.info(f"Docker device created: {suffixed_id}")
                    return DockerCreationResult(
                        container_id=suffixed_id,
                        host_port=result.host_port,
                        platform=result.platform,
                        status=result.status,
                    )

            return result

        except PaasError as e:
            self._logger.error(
                f"create_device failed for {platform_str}: {e.code} - {e.message}"
            )
            # Wrap with DeviceFacadeException per D-06
            raise DeviceFacadeException(
                operation="create_device",
                platform_type=platform_str,
                template_id=template_id,
                paas_device_id=None,
                original_error=e,
            ) from e

    async def destroy_device(
        self,
        paas_device_id: str,
    ) -> bool:
        """Destroy a device by its paas_device_id.

        Per Decision D-02: Accepts paas_device_id in format "device_id@template_id".

        Per Decision D-05: Parses template_id from suffix. If no suffix, template_id=0.

        Per Decision D-08: Uses extracted template_id to look up template and create service.

        Args:
            paas_device_id: Device ID with optional @template_id suffix
                          (e.g., "sandbox-abc123@42" or "legacy-device")

        Returns:
            True if device was successfully destroyed or already destroyed (idempotent).

        Raises:
            DeviceFacadeException: If destruction fails, wrapped with context.

        Note:
            This method parses the @template_id suffix from paas_device_id for template
            resolution, then passes the raw device_id (without suffix) to the underlying
            PaasService. Only this facade layer handles the suffix encoding/decoding.
        """
        # Parse raw_paas_device_id (without @template_id suffix) and template_id
        raw_paas_device_id, template_id = self._parse_device_id(paas_device_id)

        self._logger.info(
            f"Destroying device: raw_id={paas_device_id}, "
            f"parsed_device_id={raw_paas_device_id}, template_id={template_id}"
        )

        service = None
        try:
            template = self._device_template_service.get_by_template_id(
                template_id=template_id,
            )
            if not template:
                raise DeviceFacadeException(
                    operation="destroy_device",
                    platform_type="UNKNOWN",
                    template_id=template_id,
                    paas_device_id=paas_device_id,
                    original_error=PaasError(
                        ErrorCode.TEMPLATE_NOT_FOUND,
                        f"Template not found for template_id: {template_id}",
                    ),
                )

            service = self._factory.create(
                tenant_name=template.tenant,
                template_uuid=template.template_uuid,
                template=template,
            )

            # Destroy device using raw_paas_device_id (without @template_id suffix)
            self._logger.info(
                f"[destroy_device] Calling service.destroy_device: "
                f"raw_paas_device_id={raw_paas_device_id}, "
                f"format_contains_dash_dash={'--' in raw_paas_device_id}"
            )
            result = await service.destroy_device(raw_paas_device_id)

            self._logger.info(f"Device {paas_device_id} destroyed successfully")
            return result

        except PaasError as e:
            self._logger.error(
                f"destroy_device failed for {paas_device_id}: {e.code} - {e.message}"
            )
            # Infer platform type from service class name if available
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="destroy_device",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=e,
            ) from e

    async def execute_command(
        self,
        paas_device_id: str,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> CommandResult:
        """Execute a command on a device.

        Per Decision D-03: Accepts paas_device_id in format "device_id@template_id".

        Per Decision D-05: Parses template_id from suffix. If no suffix, template_id=0.

        Per Decision D-08: Uses extracted template_id to look up template and create service.

        Args:
            paas_device_id: Device ID with optional @template_id suffix
                          (e.g., "sandbox-abc123@42" or "legacy-device")
            cmd: Command string to execute
            env: Optional environment variables for command execution
            timeout_seconds: Maximum execution time in seconds (default: 30)

        Returns:
            CommandResult with execution output (exit_code, stdout, stderr, etc.)

        Raises:
            DeviceFacadeException: If execution fails, wrapped with context.

        Note:
            This method parses the @template_id suffix from paas_device_id for template
            resolution, then passes the raw device_id (without suffix) to the underlying
            PaasService. Only this facade layer handles the suffix encoding/decoding.
        """
        raw_paas_device_id, template_id = self._parse_device_id(paas_device_id)

        self._logger.info(
            f"Executing command on device: raw_id={paas_device_id}, "
            f"parsed_device_id={raw_paas_device_id}, template_id={template_id}, cmd={cmd[:50]}..."
        )

        service = None
        try:
            template = self._device_template_service.get_by_template_id(
                template_id=template_id,
            )
            if not template:
                raise DeviceFacadeException(
                    operation="execute_command",
                    platform_type="UNKNOWN",
                    template_id=template_id,
                    paas_device_id=paas_device_id,
                    original_error=PaasError(
                        ErrorCode.TEMPLATE_NOT_FOUND,
                        f"Template not found for template_id: {template_id}",
                    ),
                )

            service = self._factory.create(
                tenant_name=template.tenant,
                template_uuid=template.template_uuid,
                template=template,
            )

            # Execute command using raw_paas_device_id (without @template_id suffix)
            result = await service.execute_command(
                raw_paas_device_id, cmd, env, timeout_seconds
            )

            self._logger.info(
                f"Command executed on {paas_device_id}: exit_code={result.exit_code}"
            )
            return result

        except PaasError as e:
            self._logger.error(
                f"execute_command failed for {paas_device_id}: {e.code} - {e.message}"
            )
            # Infer platform type from service class name if available
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="execute_command",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=e,
            ) from e

    async def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
        ws_conn_mode: str | None = None,
    ) -> WsConnectionInfo:
        """Resolve WebSocket connection information for a device.

        This method returns connection credentials that allow callers to establish
        direct WebSocket connections to devices via the agentclawproxy gateway.
        BaaS does NOT proxy the connection - it only resolves the connection info.

        Per D-05: This facade follows the 4-step polymorphic delegation pattern:
        1. Parse paas_device_id to extract raw_id and template_id
        2. Lookup template via DeviceTemplateService
        3. Create service via PaasServiceFactory
        4. Delegate to service.resolve_ws_conn_info() for platform-specific construction

        Platform-specific URL construction is handled by each PaasService implementation:
        - ArcaPaasService: wss:// via agentclawproxy gateway, JWT token, 5min expiry
        - LocalPaasService: ws://127.0.0.1 direct connection, empty token, 24h expiry
        - SigmaPaasService: NotImplementedError stub

        Args:
            paas_device_id: Device ID with optional @template_id suffix
                             (e.g., "sandbox-abc123@42" or "legacy-device")
            port: Target port on the device container's WebSocket service (1-65535)
            path: WebSocket path on device (e.g., /api/openclaw/ws)
            ws_conn_mode: Optional connection mode override (e.g. ``"relay"`` for
                agentclawproxy-based relay). Passed through to the platform-specific
                service. ``None`` means no override.

        Returns:
            WsConnectionInfo containing ws_url, token, target, and expiration time

        Raises:
            DeviceFacadeException: Device not found or other errors during resolution
            NotImplementedError: Sigma platform not supported
        """
        # Validate port range per docstring contract
        self._validate_port(port)

        # Parse raw_paas_device_id (without @template_id suffix) and template_id
        raw_id, template_id = self._parse_device_id(paas_device_id)

        self._logger.info(
            f"Resolving WebSocket connection info: paas_device_id={paas_device_id}, "
            f"raw_id={raw_id}, template_id={template_id}"
        )

        service = None
        try:
            # Step 1 & 2: Parse device ID and lookup template per D-05
            template = self._device_template_service.get_by_template_id(
                template_id=template_id,
            )
            if not template:
                raise DeviceFacadeException(
                    operation="resolve_ws_conn_info",
                    platform_type="UNKNOWN",
                    template_id=template_id,
                    paas_device_id=paas_device_id,
                    original_error=PaasError(
                        ErrorCode.TEMPLATE_NOT_FOUND,
                        f"Template not found for template_id: {template_id}",
                    ),
                )

            # Step 3: Create service via Factory using resolved template
            service = self._factory.create(
                tenant_name=template.tenant, template_uuid=template.template_uuid
            )

            # Step 4: Delegate to service polymorphic method per D-05
            return await service.resolve_ws_conn_info(
                raw_id, port, path, ws_conn_mode=ws_conn_mode
            )

        except NotImplementedError as e:
            # Wrap in DeviceFacadeException with full context per D-06
            raise DeviceFacadeException(
                operation="resolve_ws_conn_info",
                platform_type=await self._get_platform_type(service)
                if service
                else "UNKNOWN",
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    f"Platform does not support WebSocket connection: {e}",
                ),
            ) from e
        except PaasError as e:
            self._logger.error(
                f"resolve_ws_conn_info failed for {paas_device_id}: "
                f"{e.code} - {e.message}"
            )
            raise DeviceFacadeException(
                operation="resolve_ws_conn_info",
                platform_type=await self._get_platform_type(service)
                if service
                else "UNKNOWN",
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=e,
            ) from e
        except DeviceCreationError as e:
            # Common expected error: machine offline - log as warning to reduce alert noise
            if e.error_code == "MACHINE_OFFLINE":
                self._logger.warning(
                    f"resolve_ws_conn_info failed for {paas_device_id}: [{e.error_code}] {e.message}"
                )
            else:
                self._logger.error(
                    f"resolve_ws_conn_info failed for {paas_device_id}: {e}"
                )
            raise DeviceFacadeException(
                operation="resolve_ws_conn_info",
                platform_type=await self._get_platform_type(service)
                if service
                else "UNKNOWN",
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    str(e),
                ),
            ) from e
        except Exception as e:
            self._logger.error(f"resolve_ws_conn_info failed for {paas_device_id}: {e}")
            raise DeviceFacadeException(
                operation="resolve_ws_conn_info",
                platform_type=await self._get_platform_type(service)
                if service
                else "UNKNOWN",
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    str(e),
                ),
            ) from e

    async def resolve_invoke_http_info(
        self,
        paas_device_id: str,
        port: int,
        path: str | None = None,
    ) -> HttpConnectionInfo:
        """Resolve HTTP connection information for invoking endpoints on a device.

        This method returns connection credentials that allow callers to make
        direct HTTP requests to a device's HTTP service. BaaS does NOT proxy
        the connection — it only resolves the connection info. The caller
        connects directly to the container using the returned URL and token.

        This facade follows the 4-step polymorphic delegation pattern:
        1. Parse paas_device_id to extract raw_id and template_id
        2. Lookup template via DeviceTemplateService
        3. Create service via PaasServiceFactory
        4. Delegate to service.resolve_invoke_http_info() for
           platform-specific construction

        Platform support:
        - PoolabPaasService: Returns HttpConnectionInfo via openclaw URL + token
        - ArcaPaasService: Returns HttpConnectionInfo via proxypass proxy + JWT token
        - LocalPaasService: Returns HttpConnectionInfo via localhost HTTP URL
        - SigmaPaasService/MockPaasService:
          NotImplementedError (wrapped as DeviceFacadeException)

        Args:
            paas_device_id: Device ID with optional @template_id suffix
                             (e.g., "12345@42" or "legacy-device").
            port: Target port on the device container (1-65535).
            path: Request path to include in the HTTP URL. Defaults to None,
                  which is converted to "/" before delegating to the service.

        Returns:
            HttpConnectionInfo containing http_url and token.

        Raises:
            DeviceFacadeException: Device not found, template lookup failure,
                or platform does not support HTTP invoke info resolution.
            ValueError: If port is not an integer in the range 1-65535.
        """
        # Validate port range per docstring contract (D-05)
        self._validate_port(port)

        # Parse raw_paas_device_id (without @template_id suffix) and template_id
        raw_id, template_id = self._parse_device_id(paas_device_id)

        self._logger.info(
            f"Resolving HTTP invoke info: paas_device_id={paas_device_id}, "
            f"raw_id={raw_id}, template_id={template_id}"
        )

        service = None
        try:
            # Step 1 & 2: Parse device ID and lookup template
            template = self._device_template_service.get_by_template_id(
                template_id=template_id,
            )
            if not template:
                raise DeviceFacadeException(
                    operation="resolve_invoke_http_info",
                    platform_type="UNKNOWN",
                    template_id=template_id,
                    paas_device_id=paas_device_id,
                    original_error=PaasError(
                        ErrorCode.TEMPLATE_NOT_FOUND,
                        f"Template not found for template_id: {template_id}",
                    ),
                )

            # Step 3: Create service via Factory using resolved template
            service = self._factory.create(
                tenant_name=template.tenant, template_uuid=template.template_uuid
            )

            # Step 4: Delegate to service polymorphic method
            # Convert None to "/" before delegating (D-02 defaulting)
            return await service.resolve_invoke_http_info(
                raw_id, port, path if path is not None else "/"
            )

        except NotImplementedError as e:
            # Wrap in DeviceFacadeException with full context (D-04)
            raise DeviceFacadeException(
                operation="resolve_invoke_http_info",
                platform_type=await self._get_platform_type(service)
                if service
                else "UNKNOWN",
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    f"Platform does not support HTTP invoke info: {e}",
                ),
            ) from e
        except PaasError:
            # Re-raise PaasError as it already has platform context
            raise
        except DeviceCreationError as e:
            # Common expected error: machine offline — log as warning to
            # reduce alert noise (D-04: aligned with resolve_ws_conn_info)
            if e.error_code == "MACHINE_OFFLINE":
                self._logger.warning(
                    f"resolve_invoke_http_info failed for {paas_device_id}: "
                    f"[{e.error_code}] {e.message}"
                )
            else:
                self._logger.error(
                    f"resolve_invoke_http_info failed for {paas_device_id}: {e}"
                )
            raise DeviceFacadeException(
                operation="resolve_invoke_http_info",
                platform_type=await self._get_platform_type(service)
                if service
                else "UNKNOWN",
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    str(e),
                ),
            ) from e
        except Exception as e:
            self._logger.error(
                f"resolve_invoke_http_info failed for {paas_device_id}: {e}"
            )
            raise DeviceFacadeException(
                operation="resolve_invoke_http_info",
                platform_type=await self._get_platform_type(service)
                if service
                else "UNKNOWN",
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    str(e),
                ),
            ) from e

    async def get_device_info(
        self,
        paas_device_id: str,
    ) -> DeviceInfo:
        """Get device info by its paas_device_id.

        Per Decision D-02: Accepts paas_device_id in format "device_id@template_id".

        Per Decision D-05: Parses template_id from suffix. If no suffix, template_id=0.

        Per Decision D-08: Uses extracted template_id to look up template and create service.

        Args:
            paas_device_id: Device ID with optional @template_id suffix
                          (e.g., "sandbox-abc123@42" or "legacy-device")

        Returns:
            DeviceInfo with device details from the underlying PaaS platform.

        Raises:
            DeviceFacadeException: If info retrieval fails, wrapped with context.

        Note:
            This method parses the @template_id suffix from paas_device_id for template
            resolution, then passes the raw device_id (without suffix) to the underlying
            PaasService. Only this facade layer handles the suffix encoding/decoding.
        """
        # Parse raw_paas_device_id (without @template_id suffix) and template_id
        raw_paas_device_id, template_id = self._parse_device_id(paas_device_id)

        self._logger.info(
            f"Getting device info: raw_id={paas_device_id}, "
            f"parsed_device_id={raw_paas_device_id}, template_id={template_id}"
        )

        service = None
        try:
            # Look up template to get tenant name for factory per D-05
            template = self._device_template_service.get_by_template_id(
                template_id=template_id,
            )
            if not template:
                raise DeviceFacadeException(
                    operation="get_device_info",
                    platform_type="UNKNOWN",
                    template_id=template_id,
                    paas_device_id=paas_device_id,
                    original_error=PaasError(
                        ErrorCode.TEMPLATE_NOT_FOUND,
                        f"Template not found for template_id: {template_id}",
                    ),
                )

            # Create service via Factory using resolved template
            service = self._factory.create(
                tenant_name=template.tenant, template_uuid=template.template_uuid
            )

            # Get device info using raw_paas_device_id (without @template_id suffix)
            result = await service.get_device_info(raw_paas_device_id)

            self._logger.info(
                f"Device info retrieved successfully: {paas_device_id}, "
                f"platform={result.platform}, status={result.status}"
            )
            return result

        except PaasError as e:
            self._logger.error(
                f"get_device_info failed for {paas_device_id}: {e.code} - {e.message}"
            )
            # Infer platform type from service class name if available
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="get_device_info",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=e,
            ) from e

    async def update_outbound_operation_rule(
        self,
        paas_device_id: str,
        outbound_operation_rule: OutBoundOperationRule,
    ) -> bool:
        """Update outbound operation rule for a device.

        Per D-02: Accepts paas_device_id in format "device_id@template_id".

        Per D-05: Parses template_id from suffix. If no suffix, template_id=0.

        Per D-08: Uses extracted template_id to look up template and create service.

        Args:
            paas_device_id: Device ID with optional @template_id suffix
                          (e.g., "sandbox-abc123@42" or "legacy-device")
            outbound_operation_rule: New outbound operation rule to apply.

        Returns:
            True if successful.

        Raises:
            DeviceFacadeException: If update fails, wrapped with context.

        Note:
            This method parses the @template_id suffix from paas_device_id for template
            resolution, then passes the raw device_id (without suffix) to the underlying
            PaasService. Only this facade layer handles the suffix encoding/decoding.
        """
        # Parse raw_paas_device_id (without @template_id suffix) and template_id
        raw_paas_device_id, template_id = self._parse_device_id(paas_device_id)

        self._logger.info(
            f"Updating outbound operation rule: raw_id={paas_device_id}, "
            f"parsed_device_id={raw_paas_device_id}, template_id={template_id}"
        )

        service = None
        try:
            # Look up template to get tenant name for factory per D-05
            template = self._device_template_service.get_by_template_id(
                template_id=template_id,
            )
            if not template:
                raise DeviceFacadeException(
                    operation="update_outbound_operation_rule",
                    platform_type="UNKNOWN",
                    template_id=template_id,
                    paas_device_id=paas_device_id,
                    original_error=PaasError(
                        ErrorCode.TEMPLATE_NOT_FOUND,
                        f"Template not found for template_id: {template_id}",
                    ),
                )

            service = self._factory.create(
                tenant_name=template.tenant, template_uuid=template.template_uuid
            )

            # Update outbound operation rule using raw_paas_device_id (without @template_id suffix)
            result = await service.update_outbound_operation_rule(
                raw_paas_device_id, outbound_operation_rule
            )

            self._logger.info(
                f"Outbound operation rule updated successfully: {paas_device_id}"
            )
            return result

        except NotImplementedError as e:
            self._logger.error(
                f"Outbound operation rule update not supported for this platform: {e}"
            )
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="update_outbound_operation_rule",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    str(e),
                ),
            ) from e

        except PaasError as e:
            self._logger.error(
                f"update_outbound_operation_rule failed for {paas_device_id}: {e.code} - {e.message}"
            )
            # Infer platform type from service class name if available
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="update_outbound_operation_rule",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=e,
            ) from e

    async def update_device_ttl(self, paas_device_id: str) -> TTLInfo:
        """Extend device TTL - each platform decides its own extension strategy.

        Args:
            paas_device_id: Device ID with optional @template_id suffix
                          (e.g., "sandbox-abc123@42" or "legacy-device")

        Returns:
            TTLInfo with old and new expiration times.

        Raises:
            DeviceFacadeException: If TTL update fails, wrapped with context.
        """

        raw_paas_device_id, template_id = self._parse_device_id(paas_device_id)

        self._logger.info(
            f"Updating device TTL: raw_id={paas_device_id}, "
            f"parsed_device_id={raw_paas_device_id}, template_id={template_id}"
        )

        service = None
        try:
            template = self._device_template_service.get_by_template_id(
                template_id=template_id
            )
            if not template:
                raise DeviceFacadeException(
                    operation="update_device_ttl",
                    platform_type="UNKNOWN",
                    template_id=template_id,
                    paas_device_id=paas_device_id,
                    original_error=PaasError(
                        ErrorCode.TEMPLATE_NOT_FOUND,
                        f"Template not found for template_id: {template_id}",
                    ),
                )

            service = self._factory.create(
                tenant_name=template.tenant,
                template_uuid=template.template_uuid,
                template=template,
            )

            result = await service.update_device_ttl(raw_paas_device_id)

            # Restore @template_id suffix in TTLInfo.paas_device_id for consistency
            result.paas_device_id = paas_device_id

            self._logger.info(
                f"Device TTL updated successfully: {paas_device_id}, "
                f"success={result.success}"
            )
            return result

        except NotImplementedError as e:
            self._logger.warning(
                f"Device TTL update not supported for this platform: {e}"
            )
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="update_device_ttl",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    str(e),
                ),
            ) from e

        except PaasError as e:
            self._logger.error(
                f"update_device_ttl failed for {paas_device_id}: {e.code} - {e.message}"
            )
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="update_device_ttl",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=e,
            ) from e

    async def invoke_http_in_device(
        self,
        paas_device_id: str,
        method: str,
        port: int,
        path: str,
        query_string: str | None,
        headers: dict[str, str],
        body: bytes,
    ) -> dict[str, Any]:
        """Invoke HTTP request on a device.

        Acts as a transparent proxy to internal services running inside containers.
        Supported platforms (ARCA, LOCAL, POOLAB, TECLAW) forward requests;
        unsupported platforms (SIGMA) raise NotImplementedError
        in their PaasService implementation.

        Per D-01: Exposed in facade for unified interface.
        Per D-05: Facade remains synchronous, uses asyncio bridge for async methods.
        Per D-07: No authorization in facade, remove user_id parameter.
        Per D-08: Facade only does generic @template_id suffix parsing.
        Per D-13: Hardcoded 25s timeout in facade.

        Args:
            paas_device_id: Device ID with optional @template_id suffix
                          (e.g., "container--machine--user@42" for LOCAL platform)
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            port: Target port on the container
            path: Request path (e.g., /api/v1/users)
            query_string: Query string including leading '?' or None/empty
            headers: HTTP headers dict (hop-by-hop headers should be filtered by caller)
            body: Raw request body bytes

        Returns:
            Dict with keys: status_code (int), headers (dict), body (base64 str).
            The response body is base64-encoded for WebSocket transport.

        Raises:
            DeviceFacadeException: If invocation fails, wrapped with context.
            NotImplementedError: For platforms that do not support HTTP invocation
            (SIGMA in its PaasService implementation).
        """
        # Parse raw_paas_device_id (without @template_id suffix) and template_id
        raw_paas_device_id, template_id = self._parse_device_id(paas_device_id)

        self._logger.info(
            f"Invoking HTTP in device: raw_id={paas_device_id}, "
            f"parsed_device_id={raw_paas_device_id}, method={method}, port={port}, path={path}"
        )

        service = None
        try:
            # Look up template to get tenant name for factory per D-05
            template = self._device_template_service.get_by_template_id(
                template_id=template_id,
            )
            if not template:
                raise DeviceFacadeException(
                    operation="invoke_http_in_device",
                    platform_type="UNKNOWN",
                    template_id=template_id,
                    paas_device_id=paas_device_id,
                    original_error=PaasError(
                        ErrorCode.TEMPLATE_NOT_FOUND,
                        f"Template not found for template_id: {template_id}",
                    ),
                )

            # Create service via Factory using resolved template
            service = self._factory.create(
                tenant_name=template.tenant, template_uuid=template.template_uuid
            )

            # Per D-01 (Phase 41): Platform gate removed. All platforms dispatch
            # through the same path. Unsupported platforms (Arca/Sigma) raise
            # NotImplementedError in their PaasService, caught below.

            # Call async service method directly per D-05
            # Per WR-05: Configurable timeout for HTTP invocation
            result = await asyncio.wait_for(
                service.invoke_http_in_device(
                    paas_device_id=raw_paas_device_id,
                    method=method,
                    port=port,
                    path=path,
                    query_string=query_string,
                    headers=headers,
                    body=body,
                ),
                timeout=HTTP_INVOCATION_TIMEOUT,
            )

            self._logger.info(
                f"HTTP invocation successful: {paas_device_id}, status_code={result.get('status_code')}"
            )
            return result

        except NotImplementedError as e:
            self._logger.error(f"HTTP invocation not supported: {e}")
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="invoke_http_in_device",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    str(e),
                ),
            ) from e
        except TimeoutError as e:
            self._logger.error(
                "invoke_http_in_device timeout for %s: %ss exceeded",
                paas_device_id,
                HTTP_INVOCATION_TIMEOUT,
            )
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="invoke_http_in_device",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=PaasError(
                    ErrorCode.PLATFORM_UNAVAILABLE,
                    f"HTTP invocation timed out after {HTTP_INVOCATION_TIMEOUT}s",
                ),
            ) from e
        except PaasError as e:
            self._logger.error(
                f"invoke_http_in_device failed for {paas_device_id}: {e.code} - {e.message}"
            )
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="invoke_http_in_device",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=e,
            ) from e

    async def restart_device(
        self,
        paas_device_id: str,
    ) -> bool:
        """Restart a device by its paas_device_id.

        Per Decision D-02: Accepts paas_device_id in format "device_id@template_id".

        Per Decision D-05: Parses template_id from suffix. If no suffix, template_id=0.

        Per Decision D-08: Uses extracted template_id to look up template and create service.

        Args:
            paas_device_id: Device ID with optional @template_id suffix
                          (e.g., "sandbox-abc123@42" or "legacy-device")

        Returns:
            True if device restart was initiated successfully.

        Raises:
            DeviceFacadeException: If restart fails, wrapped with context.

        Note:
            This method parses the @template_id suffix from paas_device_id for template
            resolution, then passes the raw device_id (without suffix) to the underlying
            PaasService. Only this facade layer handles the suffix encoding/decoding.
        """
        # Parse raw_paas_device_id (without @template_id suffix) and template_id
        raw_paas_device_id, template_id = self._parse_device_id(paas_device_id)

        self._logger.info(
            f"Restarting device: raw_id={paas_device_id}, "
            f"parsed_device_id={raw_paas_device_id}, template_id={template_id}"
        )

        service = None
        try:
            template = self._device_template_service.get_by_template_id(
                template_id=template_id,
            )
            if not template:
                raise DeviceFacadeException(
                    operation="restart_device",
                    platform_type="UNKNOWN",
                    template_id=template_id,
                    paas_device_id=paas_device_id,
                    original_error=PaasError(
                        ErrorCode.TEMPLATE_NOT_FOUND,
                        f"Template not found for template_id: {template_id}",
                    ),
                )

            service = self._factory.create(
                tenant_name=template.tenant,
                template_uuid=template.template_uuid,
                template=template,
            )

            # Restart device using raw_paas_device_id (without @template_id suffix)
            result = await service.restart_device(raw_paas_device_id)

            self._logger.info(f"Device {paas_device_id} restart initiated successfully")
            return result

        except NotImplementedError as e:
            self._logger.error(f"Restart not supported for this platform: {e}")
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="restart_device",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    str(e),
                ),
            ) from e
        except PaasError as e:
            self._logger.error(
                f"restart_device failed for {paas_device_id}: {e.code} - {e.message}"
            )
            # Infer platform type from service class name if available
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="restart_device",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=e,
            ) from e

    async def update_device(
        self,
        paas_device_id: str,
        config: DeviceCreateConfig | None = None,
    ) -> bool:
        """Update a device by its paas_device_id.

        Per Decision D-02: Accepts paas_device_id in format "device_id@template_id".

        Per Decision D-05: Parses template_id from suffix. If no suffix, template_id=0.

        Per Decision D-08: Uses extracted template_id to look up template and create service.

        Semantically distinct from restart_device: update applies configuration
        changes (envs, mount_path, resource_spec, etc.) to the device container.

        Args:
            paas_device_id: Device ID with optional @template_id suffix
                          (e.g., "sandbox-abc123@42" or "legacy-device")
            config: Optional DeviceCreateConfig to pass to service.update_device.
                Defaults to None (backward compatible with LOCAL/POOLAB).

        Returns:
            True if device update was initiated successfully.

        Raises:
            DeviceFacadeException: If update fails, wrapped with context.

        Note:
            This method parses the @template_id suffix from paas_device_id for template
            resolution, then passes the raw device_id (without suffix) to the underlying
            PaasService. Only this facade layer handles the suffix encoding/decoding.
        """
        # Parse raw_paas_device_id (without @template_id suffix) and template_id
        raw_paas_device_id, template_id = self._parse_device_id(paas_device_id)

        self._logger.info(
            f"Updating device: raw_id={paas_device_id}, "
            f"parsed_device_id={raw_paas_device_id}, template_id={template_id}"
        )

        service = None
        try:
            template = self._device_template_service.get_by_template_id(
                template_id=template_id,
            )
            if not template:
                raise DeviceFacadeException(
                    operation="update_device",
                    platform_type="UNKNOWN",
                    template_id=template_id,
                    paas_device_id=paas_device_id,
                    original_error=PaasError(
                        ErrorCode.TEMPLATE_NOT_FOUND,
                        f"Template not found for template_id: {template_id}",
                    ),
                )

            service = self._factory.create(
                tenant_name=template.tenant,
                template_uuid=template.template_uuid,
                template=template,
            )

            # Update device using raw_paas_device_id (without @template_id suffix)
            result = await service.update_device(raw_paas_device_id, config)

            self._logger.info(f"Device {paas_device_id} update initiated successfully")
            return result

        except NotImplementedError as e:
            self._logger.error(f"Update not supported for this platform: {e}")
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="update_device",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    str(e),
                ),
            ) from e
        except PaasError as e:
            self._logger.error(
                f"update_device failed for {paas_device_id}: {e.code} - {e.message}"
            )
            # Infer platform type from service class name if available
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="update_device",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=e,
            ) from e

    async def open_folder(
        self, paas_device_id: str, folder_path: str | None = None
    ) -> bool:
        """Open a folder in the device's file explorer.

        Per Decision D-02: Accepts paas_device_id in format "device_id@template_id".

        Per Decision D-05: Parses template_id from suffix. If no suffix, template_id=0.

        Per Decision D-08: Uses extracted template_id to look up template and
        create service. Only LOCAL platform supports open_folder; non-LOCAL
        platforms receive NotImplementedError from the base class default.

        Args:
            paas_device_id: Device ID with optional @template_id suffix
                          (e.g., "container--machine--user@42" for LOCAL platform)
            folder_path: Optional folder path to open. If None, the platform's
                        default path is used.

        Returns:
            True if the open-folder command was sent successfully.

        Raises:
            DeviceFacadeException: If open_folder fails, wrapped with context.

        Note:
            This method parses the @template_id suffix from paas_device_id for
            template resolution, then passes the raw device_id (without suffix)
            to the underlying PaasService. Only this facade layer handles the
            suffix encoding/decoding.
        """
        # Parse raw_paas_device_id (without @template_id suffix) and template_id
        raw_paas_device_id, template_id = self._parse_device_id(paas_device_id)

        self._logger.info(
            f"Opening folder on device: raw_id={paas_device_id}, "
            f"parsed_device_id={raw_paas_device_id}, template_id={template_id}, "
            f"folder_path={folder_path}"
        )

        service = None
        try:
            template = self._device_template_service.get_by_template_id(
                template_id=template_id,
            )
            if not template:
                raise DeviceFacadeException(
                    operation="open_folder",
                    platform_type="UNKNOWN",
                    template_id=template_id,
                    paas_device_id=paas_device_id,
                    original_error=PaasError(
                        ErrorCode.TEMPLATE_NOT_FOUND,
                        f"Template not found for template_id: {template_id}",
                    ),
                )

            service = self._factory.create(
                tenant_name=template.tenant,
                template_uuid=template.template_uuid,
                template=template,
            )

            # Open folder using raw_paas_device_id (without @template_id suffix)
            result = await service.open_folder(raw_paas_device_id, folder_path)

            self._logger.info(
                f"Device {paas_device_id} open_folder completed successfully"
            )
            return result

        except NotImplementedError as e:
            self._logger.error(f"Open folder not supported for this platform: {e}")
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="open_folder",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    str(e),
                ),
            ) from e
        except PaasError as e:
            self._logger.error(
                f"open_folder failed for {paas_device_id}: {e.code} - {e.message}"
            )
            # Infer platform type from service class name if available
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="open_folder",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=e,
            ) from e

    async def fetch_start_progress(
        self, paas_device_id: str
    ) -> FetchStartProgressResult:
        """Fetch device start/initialization progress from the platform.

        Parses the @template_id suffix from paas_device_id, looks up the
        template, creates the appropriate PaasService, and delegates to the
        platform-specific fetch_start_progress() implementation.

        Only LOCAL platform supports this operation. Non-LOCAL platforms
        receive NotImplementedError from the base class default, which is
        caught here and converted to DeviceFacadeException.

        BaaS only validates that ``progress`` is present in the result;
        its type and value are defined by the mng daemon. All other fields
        are mng-daemon-defined and passed through via extra="allow".

        Args:
            paas_device_id: Device ID with optional @template_id suffix
                (e.g., "container--machine--user@42" for LOCAL platform).

        Returns:
            FetchStartProgressResult with required ``progress`` field
            plus any additional mng-daemon-defined fields.

        Raises:
            DeviceFacadeException: If template is not found, or if the
                platform does not support fetch_start_progress.
        """
        raw_paas_device_id, template_id = self._parse_device_id(paas_device_id)

        self._logger.info(
            f"Fetching start progress for device: raw_id={paas_device_id}, "
            f"parsed_device_id={raw_paas_device_id}, template_id={template_id}"
        )

        service = None
        try:
            template = self._device_template_service.get_by_template_id(
                template_id=template_id,
            )
            if not template:
                raise DeviceFacadeException(
                    operation="fetch_start_progress",
                    platform_type="UNKNOWN",
                    template_id=template_id,
                    paas_device_id=paas_device_id,
                    original_error=PaasError(
                        ErrorCode.TEMPLATE_NOT_FOUND,
                        f"Template not found for template_id: {template_id}",
                    ),
                )

            service = self._factory.create(
                tenant_name=template.tenant,
                template_uuid=template.template_uuid,
                template=template,
            )

            result = await service.fetch_start_progress(raw_paas_device_id)

            self._logger.info(
                f"Device {paas_device_id} fetch_start_progress completed: "
                f"progress={result.progress}"
            )
            return result

        except NotImplementedError as e:
            self._logger.error(
                f"Fetch start progress not supported for this platform: {e}"
            )
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="fetch_start_progress",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    str(e),
                ),
            ) from e
        except PaasError as e:
            self._logger.error(
                f"fetch_start_progress failed for {paas_device_id}: "
                f"{e.code} - {e.message}"
            )
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="fetch_start_progress",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=e,
            ) from e
        except DeviceCreationError as e:
            if e.error_code == "MACHINE_OFFLINE":
                self._logger.warning(
                    f"fetch_start_progress failed for {paas_device_id}: "
                    f"[{e.error_code}] {e.message}"
                )
            else:
                self._logger.error(
                    f"fetch_start_progress failed for {paas_device_id}: {e}"
                )
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="fetch_start_progress",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    str(e),
                ),
            ) from e
        except Exception as e:
            self._logger.error(f"fetch_start_progress failed for {paas_device_id}: {e}")
            raise DeviceFacadeException(
                operation="fetch_start_progress",
                platform_type=await self._get_platform_type(service)
                if service
                else "UNKNOWN",
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    str(e),
                ),
            ) from e

    async def pull_file(
        self, paas_device_id: str, source_url: str, device_path: str,
        timeout_seconds: int = 300,
    ) -> None:
        """Download a file from a URL to the device at the specified path.

        Parses the @template_id suffix from paas_device_id, looks up the
        template, creates the appropriate PaasService, and delegates to
        the platform-specific pull_file_from_url() implementation.

        Args:
            paas_device_id: Device ID with @template_id suffix
                (e.g., "sandbox-123@42" for Arca platform).
            source_url: URL to download from (e.g. OSS pre-signed GET URL).
            device_path: Absolute path on device to save the file to.
            timeout_seconds: Maximum download time in seconds (default: 300).

        Raises:
            DeviceFacadeException: If template is not found, or if the
                platform does not support file transfer, or if the transfer
                fails.
        """
        raw_paas_device_id, template_id = self._parse_device_id(paas_device_id)

        truncated_url = source_url[:80] + "..." if len(source_url) > 80 else source_url
        self._logger.info(
            f"Pulling file: raw_id={paas_device_id}, "
            f"parsed_device_id={raw_paas_device_id}, template_id={template_id}, "
            f"source_url={truncated_url}, device_path={device_path}, "
            f"timeout={timeout_seconds}s"
        )

        service = None
        try:
            template = self._device_template_service.get_by_template_id(
                template_id=template_id,
            )
            if not template:
                raise DeviceFacadeException(
                    operation="pull_file",
                    platform_type="UNKNOWN",
                    template_id=template_id,
                    paas_device_id=paas_device_id,
                    original_error=PaasError(
                        ErrorCode.TEMPLATE_NOT_FOUND,
                        f"Template not found for template_id: {template_id}",
                    ),
                )

            service = self._factory.create(
                tenant_name=template.tenant,
                template_uuid=template.template_uuid,
                template=template,
            )

            await service.pull_file_from_url(
                raw_paas_device_id, source_url, device_path, timeout_seconds
            )

            self._logger.info(
                f"File pull completed successfully: "
                f"paas_device_id={paas_device_id}, device_path={device_path}"
            )

        except NotImplementedError as e:
            self._logger.error(f"File pull not supported for this platform: {e}")
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="pull_file",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    str(e),
                ),
            ) from e
        except PaasError as e:
            self._logger.error(
                f"pull_file failed for {paas_device_id}: {e.code} - {e.message}"
            )
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="pull_file",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=e,
            ) from e

    async def push_file(
        self, paas_device_id: str, device_path: str, target_url: str,
        timeout_seconds: int = 300,
    ) -> None:
        """Upload a file from the device path to the target URL.

        Parses the @template_id suffix from paas_device_id, looks up the
        template, creates the appropriate PaasService, and delegates to
        the platform-specific push_file_to_url() implementation.

        Args:
            paas_device_id: Device ID with @template_id suffix
                (e.g., "sandbox-123@42" for Arca platform).
            device_path: Absolute path on device of the file to upload.
            target_url: URL to upload to (e.g. OSS pre-signed PUT URL).
            timeout_seconds: Maximum upload time in seconds (default: 300).

        Raises:
            DeviceFacadeException: If template is not found, or if the
                platform does not support file transfer, or if the transfer
                fails.
        """
        raw_paas_device_id, template_id = self._parse_device_id(paas_device_id)

        truncated_url = target_url[:80] + "..." if len(target_url) > 80 else target_url
        self._logger.info(
            f"Pushing file: raw_id={paas_device_id}, "
            f"parsed_device_id={raw_paas_device_id}, template_id={template_id}, "
            f"device_path={device_path}, target_url={truncated_url}, "
            f"timeout={timeout_seconds}s"
        )

        service = None
        try:
            template = self._device_template_service.get_by_template_id(
                template_id=template_id,
            )
            if not template:
                raise DeviceFacadeException(
                    operation="push_file",
                    platform_type="UNKNOWN",
                    template_id=template_id,
                    paas_device_id=paas_device_id,
                    original_error=PaasError(
                        ErrorCode.TEMPLATE_NOT_FOUND,
                        f"Template not found for template_id: {template_id}",
                    ),
                )

            service = self._factory.create(
                tenant_name=template.tenant,
                template_uuid=template.template_uuid,
                template=template,
            )

            await service.push_file_to_url(
                raw_paas_device_id, device_path, target_url, timeout_seconds
            )

            self._logger.info(
                f"File push completed successfully: "
                f"paas_device_id={paas_device_id}, device_path={device_path}"
            )

        except NotImplementedError as e:
            self._logger.error(f"File push not supported for this platform: {e}")
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="push_file",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    str(e),
                ),
            ) from e
        except PaasError as e:
            self._logger.error(
                f"push_file failed for {paas_device_id}: {e.code} - {e.message}"
            )
            platform_type = await self._get_platform_type(service)
            raise DeviceFacadeException(
                operation="push_file",
                platform_type=platform_type,
                template_id=template_id,
                paas_device_id=paas_device_id,
                original_error=e,
            ) from e
