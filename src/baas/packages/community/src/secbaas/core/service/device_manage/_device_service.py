"""
Device lifecycle management service.

Coordinates PaaS layer for Device creation/destruction,
manages Device database records, provides query interfaces.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from secbaas.api.device_manage import (
    DestroyDeviceResponse,
    DeviceConfig,
    DeviceCreate,
    DeviceCreateConfig,  # noqa: F401 used in _native_update_device signature
    DeviceResponse,
    DeviceService,
    DeviceStatus,
    EncryptableHeaderRule,
    EncryptableOutBoundRule,
    OutBoundOperationRule,
    TeClawCreateConfig,  # noqa: F401 used in update_device TECLAW branch
)
from secbaas.api.template_manage import (
    ArcaTemplateConfig,
    DeviceTemplateManageService,
    DockerTemplateConfig,  # noqa: F401 used in start_device and _resolve_device_for_operation
    K8sTemplateConfig,  # noqa: F401 used in start_device and _resolve_device_for_operation
    LocalTemplateConfig,
    PoolabTemplateConfig,
    SigmaTemplateConfig,
    TeClawTemplateConfig,  # noqa: F401 used in start_device and _resolve_device_for_operation
)
from secbaas.core.repository.device import (
    DeviceRecord,
    DeviceRepository,
)
from secbaas.core.service.paas import (
    DeviceFacadeException,
    ErrorCode,
    PaasServiceFacade,
)
from secbaas.core.utils.env_utils import get_current_env
from secbaas.core.utils.secret_utils import common_sm4_decrypt, common_sm4_encrypt
from secbaas.logger import get_logger
from secbaas.spi.secret import SecretStorePlugin

from ._start_hook_dispatcher import dispatch_start_hook

logger = get_logger("core-service")


def _safe_format_hook(script: str, **kwargs) -> str:
    """Format hook script safely, leaving unknown placeholders intact.

    Uses regex substitution to replace placeholders that have known values
    while leaving unknown placeholders (e.g., {unknown_key}) untouched in the
    output.  This allows a hook script containing both known and unknown
    placeholders to be partially formatted rather than entirely unformatted.

    Args:
        script: The hook script template with placeholders like {client_id}
        **kwargs: Key-value pairs for placeholder substitution

    Returns:
        Formatted script with known placeholders substituted and unknown
        placeholders left as-is.
    """

    def _replacer(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in kwargs:
            logger.warning(
                f"Hook script contains unknown placeholder: {{{key}}} "
                f"(script prefix: {script[:100]}...)"
            )
            return match.group(0)
        value = kwargs[key]
        if value is None:
            logger.warning(
                f"Hook script placeholder {{{key}}} has None value, "
                f"leaving unsubstituted (script prefix: {script[:100]}...)"
            )
            return match.group(0)
        return str(value)

    return re.sub(r"\{(\w+)\}", _replacer, script)


def _validate_required_field(value: str | None, field_name: str, missing: list) -> None:
    """Validate that a required field is present and not whitespace-only.

    Args:
        value: The field value to validate
        field_name: Name of the field for error reporting
        missing: List to append field_name to if validation fails
    """
    if not value or not str(value).strip():
        missing.append(field_name)


def _build_arca_detail_config(
    deploy_config: Any,
    oss_mount_id: str | None,
    env: str,
    device_uuid: str,
    secret_plugin: SecretStorePlugin,
    log_prefix: str = "",
) -> tuple[Any, list[Any] | None]:
    """Build an ArcaDeviceConfig from the unified DeployConfig.

    Extracted from start_device and restart_device to eliminate ~100 lines
    of duplicated ARCA configuration building logic.

    Args:
        deploy_config: The unified DeployConfig (may be None).
        oss_mount_id: OSS mount ID from the Arca template config.
        env: The environment name (e.g. "dev", "pre") for AGENTCLAW_ENV default.
        device_uuid: The device UUID for storage_id placeholder rendering.
        log_prefix: Optional prefix for log messages (e.g. "[restart_device] ").

    Returns:
        Tuple of (ArcaDeviceConfig, list of MountPoint | None).
    """
    from secbaas.api.device_manage import ArcaDeviceConfig, MountPoint

    # MountPoint list from deploy_config (Arca SDK type used directly)
    # Inject oss_mount_id from template into each mount point's id field
    mount_points_list = None
    if deploy_config and deploy_config.mount_points:
        mount_points_list = [
            MountPoint(
                id=oss_mount_id or mp.id or "",
                remote_dir=mp.remote_dir,
                local_dir=mp.local_dir,
                permission=mp.permission,
            )
            for mp in deploy_config.mount_points
        ]

    # Build ArcaDeviceConfig with optional fields from unified DeployConfig
    detail_config = ArcaDeviceConfig(
        mount_points=mount_points_list,
    )
    if deploy_config:
        # Build envs with AGENTCLAW_ENV default entry, merged with user-provided envs
        merged_envs: dict[str, str] | None = None
        if deploy_config.envs is not None:
            # User provided envs, merge AGENTCLAW_ENV (user envs take precedence if key conflicts)
            merged_envs = {"AGENTCLAW_ENV": env, **deploy_config.envs}
        # If deploy_config.envs is None but other Arca config exists, still add AGENTCLAW_ENV
        # However, we only set envs when there's actual config or mount_points to pass
        if merged_envs is None and (
            mount_points_list is not None
            or deploy_config.ttl_in_minutes is not None
            or deploy_config.arca_metadata is not None
            or deploy_config.outbound_operation_rule is not None
        ):
            merged_envs = {"AGENTCLAW_ENV": env}
        if merged_envs is not None:
            detail_config.envs = merged_envs
        if deploy_config.ttl_in_minutes is not None:
            detail_config.ttl_in_minutes = deploy_config.ttl_in_minutes
        if deploy_config.arca_metadata is not None:
            detail_config.metadata = deploy_config.arca_metadata
        if deploy_config.outbound_operation_rule is not None:
            # Decrypt header values if EncryptableHeaderRule (D-13.02)
            decrypted_rule = _decrypt_header_rule_values(
                deploy_config.outbound_operation_rule, secret_plugin
            )
            detail_config.outbound_operation_rule = decrypted_rule
        if deploy_config.storage is not None:
            # Create a copy of Storage with rendered storage_id placeholder
            storage = deploy_config.storage
            rendered_storage_id = (
                storage.storage_id.replace("{device_uuid}", device_uuid)
                if storage.storage_id
                else None
            )
            if rendered_storage_id != storage.storage_id:
                # Rebuild Storage with rendered storage_id
                from secbaas.api.device_manage import Storage

                storage = Storage(
                    type=storage.type,
                    path=storage.path,
                    storage_id=rendered_storage_id,
                    quota=storage.quota,
                    permission=storage.permission,
                )
            detail_config.storage = storage
        if deploy_config.resource_spec is not None:
            detail_config.resource_spec = deploy_config.resource_spec
        if deploy_config.docker_image is not None:
            detail_config.docker_image = deploy_config.docker_image

    # Log which fields are being passed
    fields_info = []
    if deploy_config:
        # Log merged_envs count (includes AGENTCLAW_ENV default)
        if detail_config.envs is not None:
            fields_info.append(f"envs={len(detail_config.envs)} vars")
        if deploy_config.ttl_in_minutes is not None:
            fields_info.append(f"ttl={deploy_config.ttl_in_minutes}min")
        if deploy_config.arca_metadata is not None:
            fields_info.append(f"arca_metadata={len(deploy_config.arca_metadata)} keys")
        if deploy_config.outbound_operation_rule is not None:
            fields_info.append("outbound_rule=yes")
        if deploy_config.storage is not None:
            fields_info.append("storage=yes")
        if deploy_config.resource_spec is not None:
            fields_info.append("resource_spec=yes")
        if deploy_config.docker_image is not None:
            fields_info.append(f"docker_image={deploy_config.docker_image}")
        if mount_points_list:
            fields_info.append(f"mounts={len(mount_points_list)}")

    if fields_info:
        logger.info(
            f"{log_prefix}Built ArcaDeviceConfig with: {', '.join(fields_info)}"
        )
    else:
        logger.info(
            f"{log_prefix}Built ArcaDeviceConfig with defaults (no override fields)"
        )

    return detail_config, mount_points_list


def _encrypt_header_rule_values(
    extra_config: DeviceConfig | None, security_plugin: SecretStorePlugin
) -> None:
    """Encrypt header values if EncryptableOutBoundRule with encrypt_value=True.

    Modifies extra_config in-place. Called during device creation before persistence.

    Args:
        extra_config: Device config to encrypt in-place.
        security_plugin: SecretStorePlugin.
    """
    if extra_config is None:
        return

    deploy_config = extra_config.deploy_config
    if not deploy_config or not deploy_config.outbound_operation_rule:
        return

    outbound_rule = deploy_config.outbound_operation_rule
    # Check if this is EncryptableOutBoundRule type
    if not isinstance(outbound_rule, EncryptableOutBoundRule):
        return

    for rule in outbound_rule.header_operation_rules:
        if (
            isinstance(rule, EncryptableHeaderRule)
            and rule.encrypt_value
            and rule.value
        ):
            key_b64 = security_plugin.resolve_common_sm4_key()
            rule.value = common_sm4_encrypt(rule.value, key_b64)
            # encrypt_value flag stays True to indicate encrypted state


def _decrypt_header_rule_values(
    outbound_rule: EncryptableOutBoundRule | OutBoundOperationRule | None,
    secret_plugin: SecretStorePlugin,
) -> OutBoundOperationRule | None:
    """Decrypt header values and return SDK-compatible OutBoundOperationRule.

    Called in start_device before passing to PaaS layer.
    """
    from secbaas.api.device_manage import (
        HeaderOperationRule,
        OutBoundOperationRule,
    )

    if outbound_rule is None:
        return None

    # If already SDK type (not EncryptableOutBoundRule), return as-is
    if not isinstance(outbound_rule, EncryptableOutBoundRule):
        return (
            outbound_rule if isinstance(outbound_rule, OutBoundOperationRule) else None
        )

    # Process encryptable rules, decrypting where needed
    sdk_rules = []
    for rule in outbound_rule.header_operation_rules:
        value = rule.value
        # Decrypt if encrypt_value flag is True
        if rule.encrypt_value and rule.value:
            try:
                key_b64 = secret_plugin.resolve_common_sm4_key()
                value = common_sm4_decrypt(rule.value, key_b64)
            except Exception as e:
                # Explicitly expose decryption failures for debugging
                raise ValueError(
                    f"Decryption failed for header '{rule.header_name}'. "
                    f"Original error: {type(e).__name__}: {e}"
                ) from e

        # Create SDK-compatible HeaderOperationRule
        sdk_rule = HeaderOperationRule(
            domains=rule.domains,
            action=rule.action,
            header_name=rule.header_name,
            value=value,
            placeholder=rule.placeholder,
            separator=rule.separator,
        )
        sdk_rules.append(sdk_rule)

    return OutBoundOperationRule(header_operation_rules=sdk_rules)


async def _native_restart_device(
    facade: Any,
    repo: Any,
    record: Any,
    platform_name: str,
    device_uuid: str,
    tenant: str,
    env: str,
    modifier: str,
    has_async_callback: bool = False,
) -> DeviceResponse:
    """Execute native restart_device for platforms that support single-step restart.

    Used by LOCAL and POOLAB platforms. Calls facade.restart_device() and handles
    failure (FAILED status) and success status paths uniformly.

    Args:
        has_async_callback: When True, sets status to PENDING after restart (the
            caller has a callback or health-check mechanism that will set ACTIVE
            later). When False, sets status to ACTIVE immediately (the platform
            has no post-restart callback, so the device is ready at once).
    """
    if not record.provider_device_id:
        err_msg = f"Cannot restart {platform_name} device without provider_device_id"
        logger.error(f"[restart_device] {err_msg}: device_uuid={device_uuid}")
        raise ValueError(err_msg)

    logger.info(
        f"[restart_device] {platform_name} platform: "
        f"using native restart for device {device_uuid}"
    )

    try:
        await facade.restart_device(record.provider_device_id)
        logger.info(
            f"[restart_device] {platform_name} native restart succeeded: "
            f"device_uuid={device_uuid}"
        )
    except Exception as e:
        err_msg = f"{platform_name} platform restart failed: {str(e)}"
        logger.error(f"[restart_device] {err_msg}: device_uuid={device_uuid}")

        repo.update_device(
            device_id=record.id,
            tenant=tenant,
            env=env,
            modifier=modifier,
            status=DeviceStatus.FAILED.value,
            err_msg=err_msg,
        )

        updated_record = repo.get_by_id(record.id, tenant, env)
        if not updated_record:
            raise ValueError(
                f"Device record not found after failed restart: id={record.id}"
            )
        return device_record_to_response(updated_record)

    target_status = DeviceStatus.PENDING if has_async_callback else DeviceStatus.ACTIVE
    # Update status: PENDING if caller has a callback/health-check mechanism,
    # ACTIVE otherwise (platform has no post-restart callback — device is ready).
    repo.update_device(
        device_id=record.id,
        tenant=tenant,
        env=env,
        modifier=modifier,
        status=target_status.value,
        err_msg=None,  # Clear any prior error on successful restart
    )
    logger.info(
        f"[restart_device] {platform_name} device restarted, status set to "
        f"{target_status.value}: device_uuid={device_uuid}"
    )

    updated_record = repo.get_by_id(record.id, tenant, env)
    if not updated_record:
        raise ValueError(f"Device record not found after restart: id={record.id}")
    final_status = updated_record.status
    logger.info(
        f"[restart_device] done: device_uuid={device_uuid} status={final_status}"
    )
    return device_record_to_response(updated_record)


async def _native_update_device(
    facade: Any,
    repo: Any,
    record: Any,
    platform_name: str,
    device_uuid: str,
    tenant: str,
    env: str,
    modifier: str,
    has_async_callback: bool = False,
    config: DeviceCreateConfig | None = None,
) -> DeviceResponse:
    """Execute native update_device for platforms that support single-step update.

    Used by LOCAL, POOLAB, and TECLAW platforms. Calls facade.update_device() and handles
    failure (FAILED status) and success status paths uniformly.

    Args:
        has_async_callback: When True, sets status to PENDING after update (the
            caller has a callback or health-check mechanism that will set ACTIVE
            later). When False, sets status to ACTIVE immediately (the platform
            has no post-update callback, so the device is ready at once).
        config: Optional DeviceCreateConfig to pass to facade.update_device.
            Defaults to None for backward compatibility (LOCAL/POOLAB).
    """
    if not record.provider_device_id:
        err_msg = f"Cannot update {platform_name} device without provider_device_id"
        logger.error(f"[update_device] {err_msg}: device_uuid={device_uuid}")
        raise ValueError(err_msg)

    logger.info(
        f"[update_device] {platform_name} platform: "
        f"using native update for device {device_uuid}"
    )

    try:
        await facade.update_device(record.provider_device_id, config)
        logger.info(
            f"[update_device] {platform_name} native update succeeded: "
            f"device_uuid={device_uuid}"
        )
    except Exception as e:
        err_msg = f"{platform_name} platform update failed: {str(e)}"
        logger.error(f"[update_device] {err_msg}: device_uuid={device_uuid}")

        repo.update_device(
            device_id=record.id,
            tenant=tenant,
            env=env,
            modifier=modifier,
            status=DeviceStatus.FAILED.value,
            err_msg=err_msg,
        )

        updated_record = repo.get_by_id(record.id, tenant, env)
        if not updated_record:
            raise ValueError(
                f"Device record not found after failed update: id={record.id}"
            )
        return device_record_to_response(updated_record)

    target_status = DeviceStatus.PENDING if has_async_callback else DeviceStatus.ACTIVE
    # Update status: PENDING if caller has a callback/health-check mechanism,
    # ACTIVE otherwise (platform has no post-update callback — device is ready).
    repo.update_device(
        device_id=record.id,
        tenant=tenant,
        env=env,
        modifier=modifier,
        status=target_status.value,
        err_msg=None,  # Clear any prior error on successful update
    )
    logger.info(
        f"[update_device] {platform_name} device updated, status set to "
        f"{target_status.value}: device_uuid={device_uuid}"
    )

    updated_record = repo.get_by_id(record.id, tenant, env)
    if not updated_record:
        raise ValueError(f"Device record not found after update: id={record.id}")
    final_status = updated_record.status
    logger.info(
        f"[update_device] done: device_uuid={device_uuid} status={final_status}"
    )
    return device_record_to_response(updated_record)


# Arca SandboxStatus to DeviceStatus mapping per CONTEXT.md
_ARCA_STATUS_MAP = {
    "RUNNING": DeviceStatus.ACTIVE,
    "PENDING": DeviceStatus.PENDING,
    "DESTROYED": DeviceStatus.RELEASED,
    "PAUSED": DeviceStatus.FAILED,
    "FAILED": DeviceStatus.FAILED,
}


def device_record_to_response(record: DeviceRecord | None) -> DeviceResponse:
    """Convert DeviceRecord to DeviceResponse."""
    if record is None:
        raise RuntimeError("Device record is None")
    # Deserialize extra_config dict to DeviceConfig
    extra_config = None
    if record.extra_config:
        extra_config = DeviceConfig.model_validate(record.extra_config)
    else:
        extra_config = DeviceConfig()
    return DeviceResponse(
        id=record.id,
        device_uuid=record.device_uuid,
        tenant=record.tenant,
        env=record.env,
        domain=record.domain,
        status=record.status,
        provider_type=record.provider_type,
        provider_device_id=record.provider_device_id,
        provider_device_props=record.provider_device_props,
        extra_config=extra_config,
        err_msg=(v if isinstance(v := getattr(record, "err_msg", None), str) else None),
        creator=record.creator,
        modifier=record.modifier,
        gmt_create=record.gmt_create,
        gmt_modified=record.gmt_modified,
    )


class DefaultDeviceService(DeviceService):
    """Device lifecycle management service.

    This service manages device lifecycle including creation, starting,
    restarting, and destruction. It uses dependency injection for the
    PaasServiceFacade to enable better testability and avoid multiple
    logger instances.
    """

    def __init__(
        self,
        paas_facade: PaasServiceFacade,
        repository: DeviceRepository,
        device_template_service: DeviceTemplateManageService,
        secret_plugin: SecretStorePlugin,
    ) -> None:
        """Initialize DefaultDeviceService with injected dependencies.

        All three arguments are required per R-14 wiring principle.
        """
        self._paas_facade = paas_facade
        self._secret_plugin = secret_plugin
        self._repository = repository
        self._device_template_service = device_template_service

    def create_device(
        self,
        tenant: str,
        data: DeviceCreate,
    ) -> DeviceResponse:
        """
        Create device: inserts PENDING device record with generated UUID.

        Flow:
        1. Generate device_uuid with format: DEVICE-{uuid4().hex}
        2. Serialize extra_config (including optional template_uuid) to JSON
        3. Insert PENDING device record
        4. Return DeviceResponse

        Note: PaaS container provisioning happens in start() method.
        PaaS allocation deferred — call start() to provision and activate.

        Args:
            tenant: Tenant name for isolation
            data: Device creation parameters
        """
        env = get_current_env()
        logger.info(f"Creating device: tenant={tenant}, env={env}")

        repo = self._repository

        # Generate device_uuid with format: DEVICE-{uuid.uuid4().hex}
        device_uuid = f"DEVICE-{uuid.uuid4().hex}"
        logger.info(
            f"[create_device] generated device_uuid={device_uuid} tenant={tenant}"
        )

        # provider_device_props stores PaaS runtime data (no startup config)
        provider_device_props: dict[str, Any] = {}

        # Encrypt header values if EncryptableHeaderRule with encrypt_value=True (D-13.02)
        if data.extra_config:
            _encrypt_header_rule_values(data.extra_config, self._secret_plugin)

        # Create PENDING device record first (Write-First strategy per CONTEXT.md)
        record_id = repo.insert_device(
            device_uuid=device_uuid,
            tenant=tenant,
            env=env,
            domain=data.domain,
            creator=data.operator,
            modifier=data.operator,
            status=DeviceStatus.PENDING.value,
            provider_type=None,  # Set in start() when PaaS is called
            provider_device_id=None,
            provider_device_props=provider_device_props,
            extra_config=data.extra_config.model_dump(exclude_none=True),
        )
        logger.info(f"Device record created: id={record_id}, uuid={device_uuid}")

        # Device record created with PENDING status.
        # PaaS allocation deferred — call start() to provision and activate.
        record = repo.get_by_id(record_id, tenant, env)
        if not record:
            logger.error(
                f"[create_device] DB readback failed: record_id={record_id} "
                f"device_uuid={device_uuid}"
            )
            raise ValueError(f"Device record created but not found: id={record_id}")
        logger.info(
            f"[create_device] done: device_uuid={device_uuid} record_id={record_id} "
            f"status={record.status} provider_type={record.provider_type}"
        )
        return device_record_to_response(record)

    async def start_device(
        self,
        tenant: str,
        device_uuid: str,
        modifier: str = "system",
        publish_id: int | None = None,
    ) -> DeviceResponse:
        """Start device: provision PaaS container and execute initialization hooks.

        Sync flow for PaaS creation, async dispatch for start hooks:
        1-7. Synchronous: find device, resolve template, create PaaS device, persist info
        8. If hook configured: dispatch async via asyncio.Task, device stays PENDING
        9. If no hook: set ACTIVE immediately (fast path)
        10. If PaaS creation fails: set FAILED immediately (sync error path)

        Args:
            tenant: Tenant name for isolation
            device_uuid: Device UUID (physical unique key, format: DEVICE-{hex})
            modifier: Modifier ID for audit trail
            publish_id: Optional publish ID for callback correlation

        Returns:
            DeviceResponse with updated device information

        Raises:
            ValueError: If PENDING device not found
        """
        env = get_current_env()
        logger.info(
            f"Starting device: tenant={tenant}, device_uuid={device_uuid}, env={env}"
        )

        repo = self._repository

        # Step 1: Find PENDING device record by UUID
        record = repo.get_by_device_uuid(
            device_uuid=device_uuid,
            tenant=tenant,
            env=env,
            status=DeviceStatus.PENDING.value,
        )
        if not record:
            raise ValueError(f"PENDING device not found: {device_uuid}")

        logger.info(
            f"Found PENDING device record: id={record.id}, device_uuid={device_uuid}"
        )

        # Step 2: Deserialize extra_config to DeviceConfig
        device_config = None
        if record.extra_config:
            device_config = DeviceConfig.model_validate(record.extra_config)

        template_uuid = device_config.template_uuid if device_config else None
        deploy_config = device_config.deploy_config if device_config else None

        # Initialize result fields
        provider_device_id = None
        provider_type = None
        provider_device_props = None

        try:
            # Step 3: Lookup template via DeviceTemplateService
            template = self._device_template_service.get_default_or_explicit_template(
                tenant=tenant, template_uuid=template_uuid
            )
            if not template:
                raise ValueError(
                    f"Template not found: {template_uuid}, tenant: {tenant}"
                )

            # Step 4: Determine provider_type from template config type (R-04)
            resolved_template_uuid = template.template_uuid
            if template.config:
                if isinstance(template.config, ArcaTemplateConfig):
                    provider_type = "ARCA"
                elif isinstance(template.config, LocalTemplateConfig):
                    provider_type = "LOCAL"
                elif isinstance(template.config, SigmaTemplateConfig):
                    provider_type = "SIGMA"
                elif isinstance(template.config, PoolabTemplateConfig):
                    provider_type = "POOLAB"
                elif isinstance(template.config, TeClawTemplateConfig):
                    provider_type = "TECLAW"
                elif isinstance(template.config, K8sTemplateConfig):
                    provider_type = "K8S"
                elif isinstance(template.config, DockerTemplateConfig):
                    provider_type = "DOCKER"
                else:
                    raise ValueError(
                        f"Unknown template config type: {type(template.config)}"
                    )
            else:
                raise ValueError(f"Template {resolved_template_uuid} has no config")

            logger.info(
                f"Resolved template: uuid={template.template_uuid}, provider_type={provider_type}"
            )

            # Step 4.5: Extract oss_mount_id from template config for Arca platform
            oss_mount_id = None
            if template.config and isinstance(template.config, ArcaTemplateConfig):
                oss_mount_id = template.config.oss_mount_id
                if oss_mount_id:
                    logger.info(f"Using oss_mount_id from template: {oss_mount_id}")

            # Step 5: Build detail_config based on platform type
            # Platform-specific field extraction from unified DeployConfig
            detail_config = None
            if provider_type == "ARCA":
                # Build ArcaDeviceConfig via shared helper (WR-03: deduplicated)
                detail_config, _mount_points_list = _build_arca_detail_config(
                    deploy_config=deploy_config,
                    oss_mount_id=oss_mount_id,
                    env=env,
                    device_uuid=device_uuid,
                    secret_plugin=self._secret_plugin,
                )

                # Inject publish_id, device_uuid and tenant into metadata so the sandbox
                # plugin can make the device-callback after local processes start.
                # Needed for singlebox (local_proc) mode where the wrapper
                # script's curl callback targets production URLs that don't exist.
                if detail_config.metadata is None:
                    detail_config.metadata = {}
                detail_config.metadata["publish_id"] = str(publish_id or "")
                detail_config.metadata["device_uuid"] = device_uuid
                detail_config.metadata["tenant"] = tenant
                if deploy_config and deploy_config.tc_bot_id:
                    detail_config.metadata["tc_bot_id"] = deploy_config.tc_bot_id
                # Merge DeviceConfig.metadata (from extra_config) into
                # ArcaCreateConfig.metadata so upstream metadata flows to PaaS
                if device_config and device_config.metadata:
                    detail_config.metadata.update(device_config.metadata)

            elif provider_type == "LOCAL":
                from secbaas.api.device_manage import LocalDeviceConfig

                # Extract all 4 required fields from DeployConfig per D-15.05
                machine_id = deploy_config.machine_id if deploy_config else None
                user_id = (
                    deploy_config.user_id if deploy_config else None
                )  # From DeployConfig per D-15.03
                agent_code = deploy_config.agent_code if deploy_config else None
                mount_path = deploy_config.mount_path if deploy_config else None
                credentials = deploy_config.credentials if deploy_config else None
                tc_bot_id = deploy_config.tc_bot_id if deploy_config else None
                engine_type = deploy_config.engine_type if deploy_config else None
                # Treat empty string as None for mount_path (WR-01)
                if mount_path and not mount_path.strip():
                    mount_path = None

                # Pydantic validation: all 4 fields are required per D-15.04 (NO fallbacks)
                missing_fields = []
                _validate_required_field(user_id, "user_id", missing_fields)
                _validate_required_field(machine_id, "machine_id", missing_fields)
                _validate_required_field(tc_bot_id, "tc_bot_id", missing_fields)
                _validate_required_field(agent_code, "agent_code", missing_fields)

                if missing_fields:
                    raise ValueError(
                        f"Missing required fields for Local platform device {device_uuid}: {', '.join(missing_fields)}"
                    )

                # Build LocalDeviceConfig with validated required fields
                detail_config = LocalDeviceConfig(
                    user_id=user_id,
                    machine_id=machine_id,
                    tc_bot_id=tc_bot_id,
                    agent_code=agent_code,
                    envs=deploy_config.envs if deploy_config else None,
                    mount_path=mount_path,
                    credentials=credentials,
                    engine_type=engine_type,
                )

                # Log after validation to ensure we only log valid configs (WR-03)
                log_msg = (
                    f"Built LocalDeviceConfig for device {device_uuid}: "
                    f"machine_id={machine_id}, tc_bot_id={tc_bot_id}, user_id={user_id}, "
                    f"agent_code={agent_code}, mount_path={'<set>' if mount_path else '<not set>'}, "
                    f"credentials={'<set>' if credentials else '<not set>'}, "
                    f"engine_type={'<set>' if engine_type else '<not set>'}"
                )
                logger.info(log_msg)

            elif provider_type == "SIGMA":
                from secbaas.api.device_manage import SigmaDeviceConfig

                # Build SigmaDeviceConfig from unified DeployConfig fields
                # Note: SigmaDeviceConfig requires credentials (endpoint, access_key, secret_key)
                # These are determined by template, not from deploy_config
                detail_config = SigmaDeviceConfig(
                    endpoint="",  # Will be set by PaaS Service Facade from template credentials
                    access_key="",  # Will be set by PaaS Service Facade from template credentials
                    secret_key="",  # Will be set by PaaS Service Facade from template credentials
                    region="default",  # Will be overridden by template credentials if available
                )

                if deploy_config:
                    if deploy_config.zone is not None:
                        detail_config.zone = deploy_config.zone
                    if deploy_config.vpc_config is not None:
                        detail_config.vpc_config = deploy_config.vpc_config
                    if deploy_config.sigma_metadata is not None:
                        detail_config.metadata = deploy_config.sigma_metadata
                    if deploy_config.resource_spec is not None:
                        detail_config.resource_spec = deploy_config.resource_spec
                    if deploy_config.envs is not None:
                        detail_config.envs = deploy_config.envs

                # Log which fields are being passed
                fields_info = []
                if deploy_config:
                    if deploy_config.zone is not None:
                        fields_info.append(f"zone={deploy_config.zone}")
                    if deploy_config.vpc_config is not None:
                        fields_info.append("vpc_config=yes")
                    if deploy_config.sigma_metadata is not None:
                        fields_info.append(
                            f"sigma_metadata={len(deploy_config.sigma_metadata)} keys"
                        )
                    if deploy_config.resource_spec is not None:
                        fields_info.append("resource_spec=yes")
                    if deploy_config.envs is not None:
                        fields_info.append(f"envs={len(deploy_config.envs)} vars")

                if fields_info:
                    logger.info(
                        f"Built SigmaDeviceConfig with: {', '.join(fields_info)}"
                    )
                else:
                    logger.info(
                        "Built SigmaDeviceConfig with defaults (no override fields)"
                    )

            elif provider_type == "POOLAB":
                from secbaas.api.device_manage import PoolabDeviceConfig

                poolab_user_id = deploy_config.poolab_user_id if deploy_config else None
                poolab_envs = deploy_config.poolab_envs if deploy_config else None
                poolab_spec = deploy_config.poolab_spec if deploy_config else None

                # Required field validation (pattern from LOCAL, per D-05)
                missing_fields = []
                _validate_required_field(
                    poolab_user_id, "poolab_user_id", missing_fields
                )
                if missing_fields:
                    raise ValueError(
                        f"Missing required fields for Poolab platform device {device_uuid}: {', '.join(missing_fields)}"
                    )

                # poolab_tenant_id: DeployConfig > template (per D-04)
                # Use is not None (not truthy) to distinguish "not set" from ""
                poolab_tenant_id = (
                    deploy_config.poolab_tenant_id
                    if (deploy_config and deploy_config.poolab_tenant_id is not None)
                    else template.config.poolab_tenant_id
                )

                # poolab_image_id: DeployConfig > template per environment (per D-04)
                # Use is not None (not truthy) to distinguish "not set" from ""
                poolab_image_id = (
                    deploy_config.poolab_image_id
                    if (deploy_config and deploy_config.poolab_image_id is not None)
                    else template.config.get_effective_image_id(env)
                )

                # Validate required identifiers after resolution (per WR-01)
                # Fail fast at the validation boundary instead of deep in the PaaS layer.
                # Note: poolab_image_id is environment-dependent (only "pre"/"prod" return
                # a value from get_effective_image_id), so it is not required here.
                missing_ids = []
                if not poolab_tenant_id:
                    missing_ids.append("poolab_tenant_id")
                if missing_ids:
                    raise ValueError(
                        f"Missing required identifiers for Poolab device {device_uuid}: "
                        f"{', '.join(missing_ids)}. "
                        f"Not found in DeployConfig or template {resolved_template_uuid}"
                    )

                # Build PoolabDeviceConfig with extracted and validated fields
                detail_config = PoolabDeviceConfig(
                    poolab_user_id=poolab_user_id,
                    poolab_tenant_id=poolab_tenant_id,
                    poolab_image_id=poolab_image_id,
                    poolab_envs=poolab_envs,
                    poolab_spec=poolab_spec,
                )

                # Log per existing platform pattern
                logger.info(
                    f"Built PoolabDeviceConfig for device {device_uuid}: "
                    f"poolab_user_id={poolab_user_id}, "
                    f"poolab_tenant_id={'<set>' if poolab_tenant_id is not None else '<not set>'}, "
                    f"poolab_image_id={'<set>' if poolab_image_id is not None else '<not set>'}, "
                    f"poolab_envs={'<set>' if poolab_envs is not None else '<not set>'}, "
                    f"poolab_spec={'<set>' if poolab_spec is not None else '<not set>'}"
                )

            elif provider_type == "TECLAW":
                from secbaas.api.device_manage import TeClawDeviceConfig

                teclaw_bot_config = (
                    deploy_config.teclaw_bot_config if deploy_config else None
                )
                detail_config = TeClawDeviceConfig(
                    teclaw_bot_config=teclaw_bot_config,
                )
                logger.info(
                    f"Built TeClawDeviceConfig for device {device_uuid}: "
                    f"teclaw_bot_config={'<set>' if teclaw_bot_config is not None else '<not set>'}"
                )

            elif provider_type == "K8S":
                from secbaas.api.device_manage import K8sDeviceConfig

                detail_config = K8sDeviceConfig()
                logger.info(f"Built K8sDeviceConfig for device {device_uuid}")
            elif provider_type == "DOCKER":
                from secbaas.api.device_manage import DockerDeviceConfig

                docker_image = deploy_config.docker_image if deploy_config else None
                docker_container_port = (
                    deploy_config.docker_container_port if deploy_config else None
                )
                docker_envs = deploy_config.envs if deploy_config else None
                detail_config = DockerDeviceConfig.model_construct(
                    image=docker_image,
                    container_port=docker_container_port,
                    envs=docker_envs,
                    cpu_limit=None,
                    memory_limit=None,
                    name=getattr(device_config, "name", None)
                    if device_config
                    else None,
                    description=getattr(device_config, "description", None)
                    if device_config
                    else None,
                )
                logger.info(
                    f"Built DockerDeviceConfig for device {device_uuid}: "
                    f"image={'<set>' if docker_image else '<not set>'}, "
                    f"container_port={'<set>' if docker_container_port is not None else '<not set>'}, "
                    f"envs={'<set>' if docker_envs else '<not set>'}"
                )

            else:
                raise RuntimeError(
                    f"Unhandled provider_type in detail_config construction: "
                    f"{provider_type}"
                )

            # Step 6: Create PaaS device via Facade
            creation_result = await self._paas_facade.create_device(
                tenant_name=tenant,
                device_template_uuid=template.template_uuid,
                detail_config=detail_config,
            )

            # Extract provider_device_id and full result data
            # Per facade pattern: paas_device_id format is "{platform_device_id}@{template_id}"
            logger.info(
                f"[start_device] creation_result type={type(creation_result).__name__}, "
                f"content={creation_result}"
            )
            from secbaas.api.device_manage import (
                ArcaCreationResult,
                DockerCreationResult,
                K8sCreationResult,
                LocalCreationResult,
                PoolabCreationResult,
                SigmaCreationResult,
                TeClawCreationResult,
            )

            if isinstance(creation_result, ArcaCreationResult):
                # Arca result
                provider_device_id = creation_result.sandbox_id
            elif isinstance(creation_result, LocalCreationResult):
                # Local result
                provider_device_id = creation_result.container_id
            elif isinstance(creation_result, SigmaCreationResult):
                # Sigma result (future)
                provider_device_id = creation_result.instance_id
            elif isinstance(creation_result, PoolabCreationResult):
                # Poolab result
                provider_device_id = creation_result.poolab_id
            elif isinstance(creation_result, TeClawCreationResult):
                provider_device_id = creation_result.teclaw_bot_id
            elif isinstance(creation_result, K8sCreationResult):
                provider_device_id = creation_result.device_id
            elif isinstance(creation_result, DockerCreationResult):
                provider_device_id = creation_result.container_id
            else:
                raise ValueError(
                    f"Unknown PaaS result type: {type(creation_result).__name__}. "
                    f"Expected Arca (sandbox_id), Local (container_id), Sigma (instance_id), "
                    f"Poolab (poolab_id), TeClaw (teclaw_bot_id), "
                    f"or Docker (container_id) result."
                )

            # Store full result as dict
            provider_device_props = (
                creation_result.model_dump()
                if hasattr(creation_result, "model_dump")
                else {}
            )

            logger.info(
                f"PaaS device created: provider_device_id={provider_device_id}, "
                f"provider_type={provider_type}, device_uuid={device_uuid}, "
                f"tenant={tenant}, template_uuid={resolved_template_uuid}"
            )

            # Step 7: Persist PaaS provider info immediately (before potentially long hook execution)
            repo.update_device(
                device_id=record.id,
                tenant=tenant,
                env=env,
                modifier=modifier,
                status=DeviceStatus.PENDING.value,
                provider_type=provider_type,
                provider_device_id=provider_device_id,
                provider_device_props=provider_device_props,
            )

        except Exception as e:
            # PaaS creation failure fast path: set FAILED immediately, no callback
            err_msg = f"Device start failed: {str(e)}"
            logger.error(f"Failed to start device {device_uuid}: {e}")

            repo.update_device(
                device_id=record.id,
                tenant=tenant,
                env=env,
                modifier=modifier,
                status=DeviceStatus.FAILED.value,
                err_msg=err_msg,
            )

            updated_record = repo.get_by_id(record.id, tenant, env)
            if not updated_record:
                raise ValueError(
                    f"Device record not found after update: id={record.id}"
                )
            return device_record_to_response(updated_record)

        # Step 8: Check for after_create_cmd_hook
        if deploy_config and deploy_config.after_create_cmd_hook and provider_device_id:
            hook_timeout = (
                deploy_config.after_create_hook_wait_seconds if deploy_config else 300
            )

            # D-17: Platform-specific hook handling
            if provider_type == "LOCAL":
                # LOCAL platform: skip hook execution, wait for container_ready callback
                # _handle_container_ready in local_paas_service will process the callback
                logger.info(
                    f"Local platform: skipping after_create_cmd_hook for device {device_uuid}, "
                    f"waiting for container_ready callback from mng daemon"
                )

                # Return with PENDING status - container_ready callback will update to ACTIVE
                updated_record = repo.get_by_id(record.id, tenant, env)
                if not updated_record:
                    raise ValueError(
                        f"Device record not found after start: id={record.id}"
                    )
                return device_record_to_response(updated_record)
            elif provider_type == "TECLAW":
                logger.info(
                    f"TeClaw platform: skipping after_create_cmd_hook for device {device_uuid}, "
                    f"no hook support — setting ACTIVE immediately"
                )
                # Falls through to no-hook fast path -> ACTIVE
            else:
                # Arca / Sigma / Poolab / DOCKER: dispatch hook to container
                # DOCKER uses StandalonePaasService.execute_command() (docker exec)
                hook_script = deploy_config.after_create_cmd_hook
                rendered_hook = _safe_format_hook(
                    hook_script, token=device_uuid, client_id=device_uuid
                )
                logger.info(
                    f"Dispatching async start hook for device {device_uuid}: "
                    f"script={rendered_hook[:1024]}{'...' if len(rendered_hook) > 1024 else ''}"
                )

                dispatch_start_hook(
                    tenant=tenant,
                    device_uuid=device_uuid,
                    provider_device_id=provider_device_id,
                    rendered_hook=rendered_hook,
                    hook_timeout=hook_timeout,
                    publish_id=publish_id,
                    facade=self._paas_facade,
                )

                # Return with PENDING status — callback will update to ACTIVE/FAILED
                updated_record = repo.get_by_id(record.id, tenant, env)
                if not updated_record:
                    raise ValueError(
                        f"Device record not found after hook dispatch: id={record.id}"
                    )
                return device_record_to_response(updated_record)

        # No-hook fast path: set ACTIVE immediately
        repo.update_device(
            device_id=record.id,
            tenant=tenant,
            env=env,
            modifier=modifier,
            status=DeviceStatus.ACTIVE.value,
        )
        logger.info(f"Device started and activated (no hook): {device_uuid}")

        updated_record = repo.get_by_id(record.id, tenant, env)
        if not updated_record:
            raise ValueError(
                f"Device record not found after activation: id={record.id}"
            )
        return device_record_to_response(updated_record)

    def _resolve_device_for_operation(
        self,
        tenant: str,
        device_uuid: str,
        operation_name: str,
    ) -> tuple[Any, Any | None, Any, str]:
        """Resolve device record, config, template and provider_type.

        Shared preamble for restart_device and update_device. Handles device
        lookup, status validation, template resolution, and provider_type
        determination.
        """
        env = get_current_env()
        logger.info(
            f"[{operation_name}_device] starting: tenant={tenant}, "
            f"device_uuid={device_uuid}, env={env}"
        )

        repo = self._repository

        record = repo.get_by_device_uuid(
            device_uuid=device_uuid,
            tenant=tenant,
            env=env,
            status=None,
        )
        if not record:
            logger.warning(
                f"[{operation_name}_device] device not found: device_uuid={device_uuid}"
            )
            raise ValueError(f"Device not found: {device_uuid}")

        if record.status == DeviceStatus.RELEASED.value:
            logger.warning(
                f"[{operation_name}_device] device is RELEASED: "
                f"device_uuid={device_uuid}"
            )
            raise ValueError(
                f"Cannot {operation_name} device in RELEASED status: {device_uuid}"
            )

        if record.status not in (
            DeviceStatus.PENDING.value,
            DeviceStatus.ACTIVE.value,
            DeviceStatus.FAILED.value,
            DeviceStatus.UPDATING.value,
        ):
            logger.warning(
                f"[{operation_name}_device] invalid status: device_uuid={device_uuid} "
                f"status={record.status}"
            )
            raise ValueError(
                f"Cannot {operation_name} device in status {record.status}: {device_uuid}"
            )

        logger.info(
            f"[{operation_name}_device] record found: id={record.id} "
            f"provider_device_id={record.provider_device_id} "
            f"provider_type={record.provider_type} status={record.status}"
        )

        device_config = None
        if record.extra_config:
            device_config = DeviceConfig.model_validate(record.extra_config)

        template_uuid = device_config.template_uuid if device_config else None
        template = self._device_template_service.get_default_or_explicit_template(
            tenant=tenant, template_uuid=template_uuid
        )
        if not template:
            raise ValueError(f"Template not found: {template_uuid}, tenant: {tenant}")

        provider_type = None
        if template.config:
            if isinstance(template.config, ArcaTemplateConfig):
                provider_type = "ARCA"
            elif isinstance(template.config, LocalTemplateConfig):
                provider_type = "LOCAL"
            elif isinstance(template.config, SigmaTemplateConfig):
                provider_type = "SIGMA"
            elif isinstance(template.config, PoolabTemplateConfig):
                provider_type = "POOLAB"
            elif isinstance(template.config, TeClawTemplateConfig):
                provider_type = "TECLAW"
            elif isinstance(template.config, K8sTemplateConfig):
                provider_type = "K8S"
            elif isinstance(template.config, DockerTemplateConfig):
                provider_type = "DOCKER"
            else:
                raise ValueError(
                    f"Unknown template config type: {type(template.config)}"
                )
        else:
            raise ValueError(f"Template {template.template_uuid} has no config")

        logger.info(
            f"[{operation_name}_device] Resolved template: "
            f"uuid={template.template_uuid}, provider_type={provider_type}"
        )

        return record, device_config, template, provider_type

    async def restart_device(
        self,
        tenant: str,
        device_uuid: str,
        modifier: str = "system",
        publish_id: int | None = None,
    ) -> DeviceResponse:
        """Restart device: destroy and recreate PaaS container.

        Inline implementation per D-06 (does NOT call destroy_device_by_uuid
        or start_device to avoid fragmented logs and status race conditions).

        Platform-specific behavior:
        - LOCAL / POOLAB / K8S / DOCKER: Single-step native restart via facade.restart_device()
        - TECLAW: No-op (warn log + return current state) — platform has no real restart
        - SIGMA: Not yet implemented (raises ValueError)
        - TECLAW: Not yet implemented (raises ValueError)
        - ARCA: Two-phase destroy + create with hook dispatch

        ARCA two-phase flow:
        Destroy phase:
        1. Find device by UUID (any status)
        2. Validate status (RELEASED forbidden)
        3. Resolve provider_type from template, build deploy_config from extra_config
        4. Execute before_destroy_cmd_hook if configured
        5. Call PaaS destroy via facade (NOT_FOUND as success per D-05)
        6. Collect errors but preserve DB state

        Start phase:
        7. Extract oss_mount_id from template config
        8. Build detail_config via _build_arca_detail_config helper
        9. Call PaaS create_device via facade
        10.Update DB with new provider_device_id and PENDING status
        11.Execute after_create_cmd_hook if configured (async dispatch)
        12.Return DeviceResponse

        Args:
            tenant: Tenant name for isolation
            device_uuid: Device UUID (physical unique key)
            modifier: Modifier ID for audit trail
            publish_id: Optional publish ID for callback correlation

        Returns:
            DeviceResponse with restarted device information

        Raises:
            ValueError: If device not found or in RELEASED status
        """
        record, device_config, template, provider_type = (
            self._resolve_device_for_operation(tenant, device_uuid, "restart")
        )
        env = get_current_env()
        repo = self._repository

        # ===================================================================
        # Platform-specific restart handling: three independent branches
        # ===================================================================

        if provider_type == "SIGMA":
            # SIGMA: Not implemented
            err_msg = "Sigma platform restart is not yet implemented"
            logger.error(f"[restart_device] {err_msg}: device_uuid={device_uuid}")
            raise ValueError(err_msg)

        elif provider_type == "LOCAL":
            return await _native_restart_device(
                facade=self._paas_facade,
                repo=repo,
                record=record,
                platform_name="LOCAL",
                device_uuid=device_uuid,
                tenant=tenant,
                env=env,
                modifier=modifier,
                has_async_callback=True,
            )

        elif provider_type == "POOLAB":
            return await _native_restart_device(
                facade=self._paas_facade,
                repo=repo,
                record=record,
                platform_name="POOLAB",
                device_uuid=device_uuid,
                tenant=tenant,
                env=env,
                modifier=modifier,
                has_async_callback=False,
            )

        elif provider_type == "TECLAW":
            # TECLAW platform does not support real restart.
            # Return success with current device state (no-op) to avoid
            # breaking upstream callers that dispatch restart at the bot level.
            logger.warning(
                "[restart_device] TECLAW platform does not support real restart, "
                "returning current device state as no-op: device_uuid=%s",
                device_uuid,
            )
            return device_record_to_response(record)

        elif provider_type == "K8S":
            return await _native_restart_device(
                facade=self._paas_facade,
                repo=repo,
                record=record,
                platform_name="K8S",
                device_uuid=device_uuid,
                tenant=tenant,
                env=env,
                modifier=modifier,
                has_async_callback=False,
            )
        elif provider_type == "DOCKER":
            return await _native_restart_device(
                facade=self._paas_facade,
                repo=repo,
                record=record,
                platform_name="DOCKER",
                device_uuid=device_uuid,
                tenant=tenant,
                env=env,
                modifier=modifier,
                has_async_callback=False,
            )

        else:
            # ===============================================================
            # ARCA Platform: Delegate to update_device (same destroy+create)
            # ===============================================================
            # Arca has no native restart API, so both restart and update
            # are implemented as destroy + create at the PaaS level.
            # See update_device() for the full two-phase implementation.
            return await self.update_device(
                tenant=tenant,
                device_uuid=device_uuid,
                modifier=modifier,
                publish_id=publish_id,
            )

    async def update_device(
        self,
        tenant: str,
        device_uuid: str,
        modifier: str = "system",
        publish_id: int | None = None,
    ) -> DeviceResponse:
        """Update device configuration and apply changes.

        Semantically distinct from restart_device: this applies configuration
        changes (envs, mount_path, resource_spec, deploy_config, etc.) to the
        device container.

        Platform-specific behavior:
        - LOCAL / POOLAB / TECLAW: Single-step native update via facade.update_device()
        - DOCKER: Two-phase destroy + create (no lifecycle hooks, no async callback)
        - SIGMA: Not yet implemented (raises ValueError)
        - ARCA: Two-phase destroy + create with hooks

        ARCA two-phase flow (moved from restart_device):
        Destroy phase:
        1. Find device by UUID (any status)
        2. Validate status (RELEASED forbidden)
        3. Resolve provider_type from template, build deploy_config from extra_config
        4. Execute before_destroy_cmd_hook if configured
        5. Call PaaS destroy via facade (NOT_FOUND as success per D-05)
        6. Collect errors but preserve DB state

        Create phase:
        7. Extract oss_mount_id from template config
        8. Build detail_config via _build_arca_detail_config helper
        9. Call PaaS create_device via facade
        10.Update DB with new provider_device_id and PENDING status
        11.Execute after_create_cmd_hook if configured (async dispatch)
        12.Return DeviceResponse

        Args:
            tenant: Tenant name for isolation
            device_uuid: Device UUID (physical unique key)
            modifier: Modifier ID for audit trail
            publish_id: Optional publish ID for callback correlation

        Returns:
            DeviceResponse with updated device information

        Raises:
            ValueError: If device not found or in RELEASED status
        """
        record, device_config, template, provider_type = (
            self._resolve_device_for_operation(tenant, device_uuid, "update")
        )
        deploy_config = device_config.deploy_config if device_config else None
        env = get_current_env()
        repo = self._repository

        # ===================================================================
        # Platform-specific update handling
        # ===================================================================

        if provider_type == "SIGMA":
            err_msg = "Sigma platform update is not yet implemented"
            logger.error(f"[update_device] {err_msg}: device_uuid={device_uuid}")
            raise ValueError(err_msg)

        elif provider_type == "LOCAL":
            return await _native_update_device(
                facade=self._paas_facade,
                repo=repo,
                record=record,
                platform_name="LOCAL",
                device_uuid=device_uuid,
                tenant=tenant,
                env=env,
                modifier=modifier,
                has_async_callback=True,
            )

        elif provider_type == "POOLAB":
            return await _native_update_device(
                facade=self._paas_facade,
                repo=repo,
                record=record,
                platform_name="POOLAB",
                device_uuid=device_uuid,
                tenant=tenant,
                env=env,
                modifier=modifier,
                has_async_callback=False,
            )

        elif provider_type == "TECLAW":
            config = (
                TeClawCreateConfig(
                    teclaw_bot_config=deploy_config.teclaw_bot_config
                    if deploy_config
                    else None
                )
                if deploy_config
                else None
            )
            return await _native_update_device(
                facade=self._paas_facade,
                repo=repo,
                record=record,
                platform_name="TECLAW",
                device_uuid=device_uuid,
                tenant=tenant,
                env=env,
                modifier=modifier,
                has_async_callback=False,
                config=config,
            )

        elif provider_type == "K8S":
            return await _native_update_device(
                facade=self._paas_facade,
                repo=repo,
                record=record,
                platform_name="K8S",
                device_uuid=device_uuid,
                tenant=tenant,
                env=env,
                modifier=modifier,
                has_async_callback=False,
                config=None,
            )

        elif provider_type == "DOCKER":
            from secbaas.api.device_manage import (
                DockerCreationResult,
                DockerDeviceConfig,
            )

            logger.info(
                f"[update_device] DOCKER platform: using destroy+create update "
                f"for device {device_uuid}"
            )

            # Destroy phase
            if record.provider_device_id:
                try:
                    await self._paas_facade.destroy_device(record.provider_device_id)
                    logger.info(
                        f"[update_device] DOCKER destroy succeeded: device_uuid={device_uuid}"
                    )
                except DeviceFacadeException as e:
                    original_error = getattr(e, "original_error", None)
                    error_code = (
                        getattr(original_error, "code", None)
                        if original_error
                        else None
                    )
                    if error_code == ErrorCode.DEVICE_NOT_FOUND:
                        logger.warning(
                            f"[update_device] DOCKER device already destroyed: "
                            f"device_uuid={device_uuid}"
                        )
                    else:
                        raise  # Re-raise unrecoverable PaaS errors
            else:
                logger.info(
                    f"[update_device] DOCKER: no provider_device_id, skipping destroy: "
                    f"device_uuid={device_uuid}"
                )

            # Build DockerDeviceConfig from current deploy_config
            docker_image = deploy_config.docker_image if deploy_config else None
            docker_container_port = (
                deploy_config.docker_container_port if deploy_config else None
            )
            docker_envs = deploy_config.envs if deploy_config else None
            detail_config = DockerDeviceConfig.model_construct(
                image=docker_image,
                container_port=docker_container_port,
                envs=docker_envs,
                cpu_limit=None,
                memory_limit=None,
                name=getattr(device_config, "name", None) if device_config else None,
                description=getattr(device_config, "description", None)
                if device_config
                else None,
            )

            # Create phase
            try:
                creation_result = await self._paas_facade.create_device(
                    tenant_name=tenant,
                    device_template_uuid=template.template_uuid,
                    detail_config=detail_config,
                )

                if isinstance(creation_result, DockerCreationResult):
                    new_provider_device_id = creation_result.container_id
                else:
                    raise ValueError(
                        f"Unexpected PaaS result type in DOCKER update: "
                        f"{type(creation_result).__name__}. Expected DockerCreationResult."
                    )

                provider_device_props = creation_result.model_dump()

                logger.info(
                    f"[update_device] DOCKER device created: "
                    f"provider_device_id={new_provider_device_id}, "
                    f"device_uuid={device_uuid}"
                )

                # Update DB with new provider_device_id
                repo.update_device(
                    device_id=record.id,
                    tenant=tenant,
                    env=env,
                    modifier=modifier,
                    status=DeviceStatus.PENDING.value,
                    provider_type=provider_type,
                    provider_device_id=new_provider_device_id,
                    provider_device_props=provider_device_props,
                )

            except Exception as e:
                err_msg = f"DOCKER device update failed during creation: {str(e)}"
                logger.error(
                    f"[update_device] Failed to create DOCKER device {device_uuid}: {e}"
                )
                repo.update_device(
                    device_id=record.id,
                    tenant=tenant,
                    env=env,
                    modifier=modifier,
                    status=DeviceStatus.FAILED.value,
                    err_msg=err_msg,
                )
                updated_record = repo.get_by_id(record.id, tenant, env)
                if not updated_record:
                    raise ValueError(
                        f"Device record not found after failed DOCKER update: id={record.id}"
                    )
                return device_record_to_response(updated_record)

            # Return updated device
            updated_record = repo.get_by_id(record.id, tenant, env)
            if not updated_record:
                raise ValueError(
                    f"Device record not found after DOCKER update: id={record.id}"
                )
            return device_record_to_response(updated_record)

        else:
            if provider_type != "ARCA":
                raise RuntimeError(
                    f"Unhandled provider_type in update_device: {provider_type}"
                )
            # ===================================================================
            # ARCA: Two-phase destroy + create with hooks
            # ===================================================================
            logger.info(
                f"[update_device] ARCA platform: using destroy+create update "
                f"for device {device_uuid}"
            )

            # Accumulated destroy-phase warnings (ARCA-specific)
            error_parts: list[str] = []

            # ARCA: Destroy phase
            if record.provider_device_id:
                # Execute before_destroy_cmd_hook if configured
                if deploy_config and deploy_config.before_destroy_cmd_hook:
                    hook_script = deploy_config.before_destroy_cmd_hook
                    rendered_hook = _safe_format_hook(
                        hook_script, client_id=device_uuid
                    )

                    logger.info(
                        f"[update_device] executing before_destroy_cmd_hook: "
                        f"device_uuid={device_uuid} "
                        f"script={rendered_hook[:1024]}{'...' if len(rendered_hook) > 1024 else ''}"
                    )

                    try:
                        hook_timeout = deploy_config.before_destroy_hook_wait_seconds

                        hook_result = await self._paas_facade.execute_command(
                            paas_device_id=record.provider_device_id,
                            cmd=rendered_hook,
                            env=None,
                            timeout_seconds=hook_timeout,
                        )

                        logger.info(
                            f"[update_device] before_destroy_cmd_hook executed: "
                            f"device_uuid={device_uuid} exit_code={hook_result.exit_code} "
                            f"execution_time_ms={hook_result.execution_time_ms}"
                        )

                        if hook_result.exit_code != 0:
                            error_parts.append(
                                f"before_destroy_cmd_hook failed: exit_code={hook_result.exit_code}, "
                                f"stderr={hook_result.stderr[:200]}"
                            )
                            logger.warning(
                                f"[update_device] before_destroy_cmd_hook failed, continuing: "
                                f"device_uuid={device_uuid} "
                                f"stderr={hook_result.stderr[:200]}"
                            )
                        elif hook_result.stderr:
                            error_parts.append(
                                f"before_destroy_cmd_hook warning: stderr={hook_result.stderr[:200]}"
                            )
                            logger.warning(
                                f"[update_device] before_destroy_cmd_hook warning: "
                                f"device_uuid={device_uuid} stderr={hook_result.stderr[:200]}"
                            )

                    except Exception as e:
                        error_parts.append(
                            f"before_destroy_cmd_hook execution error: {e}"
                        )
                        logger.warning(
                            f"[update_device] before_destroy_cmd_hook error, continuing: "
                            f"device_uuid={device_uuid} error={e}"
                        )

                # Call PaaS destroy (NOT_FOUND as success per D-05)
                try:
                    logger.info(
                        f"[update_device] calling PaaS destroy_device: "
                        f"device_uuid={device_uuid} "
                        f"provider_device_id={record.provider_device_id}"
                    )
                    await self._paas_facade.destroy_device(record.provider_device_id)
                    logger.info(
                        f"[update_device] PaaS destroy_device succeeded: device_uuid={device_uuid}"
                    )
                except DeviceFacadeException as e:
                    original_error = getattr(e, "original_error", None)
                    error_code = (
                        getattr(original_error, "code", None)
                        if original_error
                        else None
                    )
                    if error_code == ErrorCode.DEVICE_NOT_FOUND:
                        logger.warning(
                            f"[update_device] device already destroyed in PaaS: "
                            f"device_uuid={device_uuid}"
                        )
                    else:
                        code_str = error_code.value if error_code else "UNKNOWN"
                        message = getattr(
                            getattr(e, "original_error", None), "message", str(e)
                        )
                        error_msg = f"{code_str} - {message}"
                        error_parts.append(f"PaaS destroy failed: {error_msg}")
                        logger.error(
                            f"[update_device] PaaS destroy failed: "
                            f"device_uuid={device_uuid} error={error_msg}"
                        )

                logger.info(
                    f"[update_device] ARCA destroy phase complete, DB state preserved: "
                    f"device_uuid={device_uuid} status={record.status}"
                )
            else:
                logger.info(
                    f"[update_device] no provider_device_id, skipping destroy: "
                    f"device_uuid={device_uuid}"
                )

            if error_parts:
                logger.warning(
                    f"[update_device] destroy phase completed with errors: "
                    f"device_uuid={device_uuid} errors={' ; '.join(error_parts)}"
                )

            # ===================================================================
            # WAVE 2: Build detail_config for ARCA platform
            # ===================================================================

            # Step 7: Extract oss_mount_id from template config for Arca platform
            oss_mount_id = None
            if template.config and isinstance(template.config, ArcaTemplateConfig):
                oss_mount_id = template.config.oss_mount_id
                if oss_mount_id:
                    logger.info(
                        f"[update_device] Using oss_mount_id from template: {oss_mount_id}"
                    )

            # Step 8: Build detail_config for ARCA platform (shared helper, WR-03)
            detail_config, _mount_points_list = _build_arca_detail_config(
                deploy_config=deploy_config,
                oss_mount_id=oss_mount_id,
                env=env,
                device_uuid=device_uuid,
                secret_plugin=self._secret_plugin,
                log_prefix="[update_device] ",
            )

            # Merge DeviceConfig.metadata into ArcaCreateConfig.metadata
            if device_config and device_config.metadata:
                if detail_config.metadata is None:
                    detail_config.metadata = {}
                detail_config.metadata.update(device_config.metadata)

            # ===================================================================
            # WAVE 3: Create device and update DB
            # ===================================================================

            try:
                # Step 9: Create PaaS device via Facade
                creation_result = await self._paas_facade.create_device(
                    tenant_name=tenant,
                    device_template_uuid=template.template_uuid,
                    detail_config=detail_config,
                )

                # Extract provider_device_id and full result data
                logger.info(
                    f"[update_device] creation_result type={type(creation_result).__name__}, "
                    f"content={creation_result}"
                )
                from secbaas.api.device_manage import ArcaCreationResult

                if isinstance(creation_result, ArcaCreationResult):
                    provider_device_id = creation_result.sandbox_id
                else:
                    raise ValueError(
                        f"Unexpected PaaS result type in ARCA update: "
                        f"{type(creation_result).__name__}. Expected ArcaCreationResult "
                        f"(sandbox_id). Local, Sigma, and Poolab are handled in their own "
                        f"branches above."
                    )

                provider_device_props = (
                    creation_result.model_dump()
                    if hasattr(creation_result, "model_dump")
                    else {}
                )

                logger.info(
                    f"[update_device] PaaS device created: provider_device_id={provider_device_id}, "
                    f"provider_type={provider_type}, device_uuid={device_uuid}, "
                    f"tenant={tenant}, template_uuid={template.template_uuid}"
                )

                # Step 10: Persist PaaS provider info (set PENDING after create)
                repo.update_device(
                    device_id=record.id,
                    tenant=tenant,
                    env=env,
                    modifier=modifier,
                    status=DeviceStatus.PENDING.value,
                    provider_type=provider_type,
                    provider_device_id=provider_device_id,
                    provider_device_props=provider_device_props,
                )

            except Exception as e:
                # PaaS creation failure: set FAILED and return DeviceResponse with errors
                err_msg = f"Device update failed during PaaS creation: {str(e)}"
                logger.error(
                    f"[update_device] Failed to create device {device_uuid}: {e}"
                )

                repo.update_device(
                    device_id=record.id,
                    tenant=tenant,
                    env=env,
                    modifier=modifier,
                    status=DeviceStatus.FAILED.value,
                    err_msg=err_msg,
                )

                updated_record = repo.get_by_id(record.id, tenant, env)
                if not updated_record:
                    destroy_warnings = "; ".join(error_parts) if error_parts else "none"
                    raise ValueError(
                        f"Device record not found after failed update: id={record.id}. "
                        f"Destroy-phase warnings: {destroy_warnings}"
                    )

                # Preserve destroy-phase error_parts in the response
                if error_parts:
                    destroy_warnings = "; ".join(error_parts)
                    updated_record.err_msg = (
                        f"{updated_record.err_msg or 'Unknown error'}"
                        f" | Destroy-phase warnings: {destroy_warnings}"
                    )
                return device_record_to_response(updated_record)

            # Step 11: Check for after_create_cmd_hook
            if (
                deploy_config
                and deploy_config.after_create_cmd_hook
                and provider_device_id
            ):
                hook_script = deploy_config.after_create_cmd_hook
                rendered_hook = _safe_format_hook(
                    hook_script, token=device_uuid, client_id=device_uuid
                )
                hook_timeout = (
                    deploy_config.after_create_hook_wait_seconds
                    if deploy_config
                    else 300
                )

                # D-17: Platform-specific hook handling (ARCA only - LOCAL returns early)
                # Arca: dispatch hook to container
                logger.info(
                    f"Dispatching async start hook for device {device_uuid}: "
                    f"script={rendered_hook[:1024]}{'...' if len(rendered_hook) > 1024 else ''}"
                )

                dispatch_start_hook(
                    tenant=tenant,
                    device_uuid=device_uuid,
                    provider_device_id=provider_device_id,
                    rendered_hook=rendered_hook,
                    hook_timeout=hook_timeout,
                    publish_id=publish_id,
                    facade=self._paas_facade,
                )

                # Return with PENDING status - callback will update to ACTIVE/FAILED
                updated_record = repo.get_by_id(record.id, tenant, env)
                if not updated_record:
                    raise ValueError(
                        f"Device record not found after hook dispatch: id={record.id}"
                    )
                if error_parts:
                    destroy_warnings = "; ".join(error_parts)
                    updated_record.err_msg = f"Update succeeded with destroy-phase warnings: {destroy_warnings}"
                else:
                    updated_record.err_msg = None  # Clear stale err_msg on success
                final_status = updated_record.status
                logger.info(
                    f"[update_device] done: device_uuid={device_uuid} status={final_status}"
                )
                return device_record_to_response(updated_record)

            # No-hook fast path: set ACTIVE immediately
            repo.update_device(
                device_id=record.id,
                tenant=tenant,
                env=env,
                modifier=modifier,
                status=DeviceStatus.ACTIVE.value,
                err_msg=None,  # Clear any prior error on successful update
            )
            logger.info(f"Device updated and activated (no hook): {device_uuid}")

            updated_record = repo.get_by_id(record.id, tenant, env)
            if not updated_record:
                raise ValueError(
                    f"Device record not found after activation: id={record.id}"
                )
            if error_parts:
                destroy_warnings = "; ".join(error_parts)
                updated_record.err_msg = (
                    f"Update succeeded with destroy-phase warnings: {destroy_warnings}"
                )
            else:
                updated_record.err_msg = None  # Clear stale err_msg on success
            final_status = updated_record.status
            logger.info(
                f"[update_device] done: device_uuid={device_uuid} status={final_status}"
            )
            return device_record_to_response(updated_record)

    async def destroy_device_by_uuid(
        self,
        tenant: str,
        device_uuid: str,
        modifier: str,
        for_restart: bool = False,
    ) -> DestroyDeviceResponse:
        """
        Destroy device by UUID: terminates PaaS container, soft-deletes DB record.

        Two-phase status flow per D-02: ACTIVE/FAILED -> RELEASED -> soft-delete
        NOT_FOUND error handled as success per D-05 (idempotent destroy)

        Flow:
        1. Get ACTIVE device record by device_uuid
        2. Execute before_destroy_cmd_hook if configured (capture result)
        3. Call PaasServiceFacade.destroy_device (NOT_FOUND as success)
        4. Update status to RELEASED
        5. Soft-delete device record
        6. Return DestroyDeviceResponse with success, errors, hook_result

        Args:
            tenant: Tenant name for isolation
            device_uuid: Device UUID (physical unique key)
            modifier: Modifier ID for audit trail
            for_restart: If True, skip soft-delete (used by restart_device)

        Returns:
            DestroyDeviceResponse with success status, aggregated errors, and hook result
        """
        env = get_current_env()
        logger.info(
            f"Destroying device by uuid: tenant={tenant}, device_uuid={device_uuid}, env={env}"
        )

        repo = self._repository
        error_parts = []
        hook_result = None
        paas_destroy_success = True

        # Get ACTIVE or UPDATING device record by UUID with tenant+env isolation
        # (UPDATING: restart_device/update_batch set device to UPDATING before calling destroy)
        record = repo.get_active_or_updating_by_device_uuid(
            device_uuid, tenant=tenant, env=env
        )
        if not record:
            logger.warning(f"Device not found: {device_uuid}")
            return DestroyDeviceResponse(
                success=True,  # Per D-05: already destroyed is success
                error_message=None,
                hook_result=None,
            )

        logger.info(
            f"[destroy_device] found record: id={record.id} device_uuid={device_uuid} "
            f"provider_device_id={record.provider_device_id} "
            f"provider_type={record.provider_type}"
        )

        # Execute before_destroy_cmd_hook if configured (before PaaS destroy)
        if record.provider_device_id:
            # Deserialize DeviceConfig from extra_config
            device_config = None
            if record.extra_config:
                device_config = DeviceConfig.model_validate(record.extra_config)

            deploy_config = device_config.deploy_config if device_config else None

            # Check for hook configuration
            if (
                deploy_config
                and deploy_config.before_destroy_cmd_hook
                and record.provider_type != "TECLAW"
            ):
                hook_script = deploy_config.before_destroy_cmd_hook

                # Render template with placeholders: {client_id}
                rendered_hook = _safe_format_hook(hook_script, client_id=device_uuid)

                logger.info(
                    f"Executing before_destroy_cmd_hook for device {device_uuid}: "
                    f"script={rendered_hook[:1024]}{'...' if len(rendered_hook) > 1024 else ''}"
                )

                try:
                    # Get timeout from deploy_config (default 300s)
                    # deploy_config is guaranteed non-None here (checked at line 856)
                    hook_timeout = deploy_config.before_destroy_hook_wait_seconds

                    hook_result = await self._paas_facade.execute_command(
                        paas_device_id=record.provider_device_id,
                        cmd=rendered_hook,
                        env=None,
                        timeout_seconds=hook_timeout,
                    )

                    logger.info(
                        f"before_destroy_cmd_hook executed: exit_code={hook_result.exit_code}, "
                        f"execution_time_ms={hook_result.execution_time_ms}"
                    )

                    # Capture errors but do NOT block destroy (non-blocking per D-03)
                    if hook_result.exit_code != 0:
                        error_parts.append(
                            f"before_destroy_cmd_hook failed: exit_code={hook_result.exit_code}, "
                            f"stderr={hook_result.stderr[:200]}"
                        )
                        logger.warning(
                            f"before_destroy_cmd_hook failed with exit_code={hook_result.exit_code}, "
                            f"continuing with destroy: {hook_result.stderr[:200]}"
                        )
                    elif hook_result.stderr:
                        error_parts.append(
                            f"before_destroy_cmd_hook warning: stderr={hook_result.stderr[:200]}"
                        )
                        logger.warning(
                            f"before_destroy_cmd_hook returned stderr: {hook_result.stderr[:200]}"
                        )

                except Exception as e:
                    error_parts.append(f"before_destroy_cmd_hook execution error: {e}")
                    logger.warning(
                        f"before_destroy_cmd_hook execution error, continuing with destroy: {e}"
                    )

            # Call PaaS destroy
            try:
                logger.info(
                    f"[destroy_device] Calling PaaS destroy_device: "
                    f"provider_device_id={record.provider_device_id}, "
                    f"raw_format={'--' in (record.provider_device_id or '')}, "
                    f"device_uuid={device_uuid}"
                )
                await self._paas_facade.destroy_device(record.provider_device_id)
                logger.info(f"Device destroyed: {record.provider_device_id}")
            except DeviceFacadeException as e:
                # Handle NOT_FOUND as success per D-04 (idempotent destroy)
                # Use getattr with defaults for safer attribute access
                original_error = getattr(e, "original_error", None)
                error_code = (
                    getattr(original_error, "code", None) if original_error else None
                )
                if error_code == ErrorCode.DEVICE_NOT_FOUND:
                    logger.warning(f"Device already destroyed in PaaS: {device_uuid}")
                else:
                    # Capture error but continue with DB cleanup per D-04
                    paas_destroy_success = False
                    code_str = error_code.value if error_code else "UNKNOWN"
                    message = getattr(
                        getattr(e, "original_error", None), "message", str(e)
                    )
                    error_msg = f"{code_str} - {message}"
                    error_parts.append(f"PaaS destroy failed: {error_msg}")
                    logger.error(
                        f"PaaS destroy failed for device {device_uuid}: {error_msg}"
                    )

        # Update status to RELEASED per D-02 (two-phase status flow)
        # Skip status update for restart flow - status should remain PENDING
        if not for_restart:
            repo.update_status_by_device_uuid(
                device_uuid=device_uuid,
                tenant=tenant,
                env=env,
                status=DeviceStatus.RELEASED.value,
            )
            logger.info(f"Device {device_uuid} status updated to RELEASED")

            # Soft-delete device record by UUID (only for normal destroy)
            repo.soft_delete_by_device_uuid(
                device_uuid=device_uuid, tenant=tenant, env=env, modifier=modifier
            )
            logger.info(f"Device {device_uuid} soft deleted successfully")
        else:
            logger.info(
                f"Device {device_uuid} destroyed for restart (status unchanged, no soft-delete)"
            )

        # Build aggregated error message per D-02
        error_message = "; ".join(error_parts) if error_parts else None

        logger.info(
            f"[destroy_device] done: device_uuid={device_uuid} "
            f"success={paas_destroy_success} error={error_message} "
            f"for_restart={for_restart}"
        )
        return DestroyDeviceResponse(
            success=paas_destroy_success,
            error_message=error_message,
            hook_result=hook_result,
        )

    async def stop_device_by_uuid(
        self,
        tenant: str,
        device_uuid: str,
        modifier: str,
    ) -> DestroyDeviceResponse:
        env = get_current_env()
        logger.info(
            f"Stopping device by uuid: tenant={tenant}, device_uuid={device_uuid}, env={env}"
        )

        repo = self._repository
        error_parts = []
        hook_result = None
        paas_destroy_success = True

        record = repo.get_active_or_updating_by_device_uuid(
            device_uuid, tenant=tenant, env=env
        )
        if not record:
            record = repo.get_by_device_uuid_only(
                device_uuid=device_uuid,
            )
            if not record or record.status not in ("ACTIVE", "FAILED", "UPDATING"):
                logger.warning(f"Device not found or not stoppable: {device_uuid}")
                return DestroyDeviceResponse(
                    success=True,
                    error_message=None,
                    hook_result=None,
                )

        logger.info(
            f"[stop_device] found record: id={record.id} device_uuid={device_uuid} "
            f"provider_device_id={record.provider_device_id} "
            f"provider_type={record.provider_type}"
        )

        if record.provider_device_id:
            device_config = None
            if record.extra_config:
                device_config = DeviceConfig.model_validate(record.extra_config)

            deploy_config = device_config.deploy_config if device_config else None

            if (
                deploy_config
                and deploy_config.before_destroy_cmd_hook
                and record.provider_type != "TECLAW"
            ):
                hook_script = deploy_config.before_destroy_cmd_hook
                rendered_hook = _safe_format_hook(hook_script, client_id=device_uuid)

                logger.info(
                    f"Executing before_destroy_cmd_hook for device {device_uuid}: "
                    f"script={rendered_hook[:1024]}{'...' if len(rendered_hook) > 1024 else ''}"
                )

                try:
                    hook_timeout = deploy_config.before_destroy_hook_wait_seconds
                    hook_result = await self._paas_facade.execute_command(
                        paas_device_id=record.provider_device_id,
                        cmd=rendered_hook,
                        env=None,
                        timeout_seconds=hook_timeout,
                    )

                    logger.info(
                        f"before_destroy_cmd_hook executed: exit_code={hook_result.exit_code}, "
                        f"execution_time_ms={hook_result.execution_time_ms}"
                    )

                    if hook_result.exit_code != 0:
                        error_parts.append(
                            f"before_destroy_cmd_hook failed: exit_code={hook_result.exit_code}, "
                            f"stderr={hook_result.stderr[:200]}"
                        )
                        logger.warning(
                            f"before_destroy_cmd_hook failed with exit_code={hook_result.exit_code}, "
                            f"continuing with stop: {hook_result.stderr[:200]}"
                        )
                    elif hook_result.stderr:
                        error_parts.append(
                            f"before_destroy_cmd_hook warning: stderr={hook_result.stderr[:200]}"
                        )
                        logger.warning(
                            f"before_destroy_cmd_hook returned stderr: {hook_result.stderr[:200]}"
                        )

                except Exception as e:
                    error_parts.append(f"before_destroy_cmd_hook execution error: {e}")
                    logger.warning(
                        f"before_destroy_cmd_hook execution error, continuing with stop: {e}"
                    )

            try:
                logger.info(
                    f"[stop_device] Calling PaaS destroy_device: "
                    f"provider_device_id={record.provider_device_id}, "
                    f"device_uuid={device_uuid}"
                )
                await self._paas_facade.destroy_device(record.provider_device_id)
                logger.info(f"Device destroyed for stop: {record.provider_device_id}")
            except DeviceFacadeException as e:
                original_error = getattr(e, "original_error", None)
                error_code = (
                    getattr(original_error, "code", None) if original_error else None
                )
                if error_code == ErrorCode.DEVICE_NOT_FOUND:
                    logger.warning(f"Device already destroyed in PaaS: {device_uuid}")
                else:
                    paas_destroy_success = False
                    code_str = error_code.value if error_code else "UNKNOWN"
                    message = getattr(
                        getattr(e, "original_error", None), "message", str(e)
                    )
                    error_msg = f"{code_str} - {message}"
                    error_parts.append(f"PaaS destroy failed: {error_msg}")
                    logger.error(
                        f"PaaS destroy failed for device {device_uuid}: {error_msg}"
                    )

        repo.update_status_by_device_uuid(
            device_uuid=device_uuid,
            tenant=tenant,
            env=env,
            status=DeviceStatus.STOPPED.value,
        )
        logger.info(f"Device {device_uuid} status updated to STOPPED")

        error_message = "; ".join(error_parts) if error_parts else None

        logger.info(
            f"[stop_device] done: device_uuid={device_uuid} "
            f"success={paas_destroy_success} error={error_message}"
        )
        return DestroyDeviceResponse(
            success=paas_destroy_success,
            error_message=error_message,
            hook_result=hook_result,
        )

    def get_device_info(
        self,
        device_uuid: str,
    ) -> DeviceResponse | None:
        """Get device information by UUID.

        Queries device record by device_uuid (global unique key).
        Returns None if device not found or has been soft-deleted.

        Per D-01: device_uuid is a physical unique key, equivalent to id column.

        Per D-02: Returns DeviceResponse to be consistent with other DeviceService methods.

        Per D-03: Returns None for non-existent or deleted devices.

        Args:
            device_uuid: Device UUID (physical unique key, format: DEVICE-{hex})

        Returns:
            DeviceResponse with full device information, or None if not found/deleted
        """
        logger.info(f"Getting device info: device_uuid={device_uuid}")

        repo = self._repository

        # Get device record by UUID (global unique key, filters is_deleted=0)
        record = repo.get_by_device_uuid_only(device_uuid=device_uuid)

        if not record:
            logger.info(f"Device not found: {device_uuid}")
            return None

        logger.info(
            f"Device info retrieved: device_uuid={device_uuid}, status={record.status}, "
            f"tenant={record.tenant}, env={record.env}"
        )
        return device_record_to_response(record)
