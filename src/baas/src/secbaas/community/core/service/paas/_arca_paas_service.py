"""Arca platform PaaS adapter implementation using Arca SDK directly."""

from __future__ import annotations

import asyncio
import json
import shlex
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

from secbaas.community.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.community.api.device_manage import (
    ArcaCreateConfig,
    ArcaCreationResult,
    ArcaCredentials,
    ArcaDeviceInfo,
    CommandResult,
    DeviceCreateConfig,
    ErrorCode,
    MountPermission,
    MountPoint,
    OutBoundOperationRule,
    OutBoundOperationRuleUpdatedMode,
    PaasError,
)
from secbaas.community.api.tenant_manage import TenantType
from secbaas.community.logger import get_logger
from secbaas.community.spi.sandbox.arca import (
    ArcaProvisioningRegistry,
    ArcaSandboxError,
    ArcaSandboxNotFoundError,
    ArcaSandboxPlugin,
    ArcaSandboxTimeoutError,
)

from ._paas_service import PaasService

if TYPE_CHECKING:
    from secbaas.community.api.health_check.bot import TTLInfo
    from secbaas.community.spi.sandbox.arca import ArcaSandbox


_SENSITIVE_LOG_KEYS = frozenset(
    {
        "extraproperties",
        "apikey",
        "authorization",
        "token",
    }
)


def _redact_sensitive_log_value(value: Any) -> Any:
    """Return a recursively redacted copy suitable only for logging."""
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).replace("_", "").replace("-", "").lower()
            redacted[key] = (
                "<redacted>"
                if normalized_key in _SENSITIVE_LOG_KEYS
                else _redact_sensitive_log_value(item)
            )
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact_sensitive_log_value(item) for item in value]
    return value


def _log_safe_model(value: Any) -> Any:
    """Convert a model/object to a log-safe structure without affecting runtime data."""
    try:
        if hasattr(value, "model_dump"):
            raw = value.model_dump(exclude_none=True)
        elif isinstance(value, dict):
            raw = value
        elif hasattr(value, "__dict__"):
            raw = vars(value)
        else:
            raw = _safe_repr(value)
        return _redact_sensitive_log_value(raw)
    except Exception as exc:
        return {
            "value_type": type(value).__name__,
            "dump_error_type": type(exc).__name__,
        }


def _safe_repr(obj: object, max_len: int = 4096) -> str:
    """Safely repr() an object, truncating to max_len characters.

    Returns "<repr failed: {error}>" if repr() raises an exception.
    """
    try:
        s = repr(obj)
        if len(s) > max_len:
            s = s[: max_len - 3] + "..."
        return s
    except Exception as e:
        return f"<repr failed: {e}>"


class ArcaPaasService(PaasService):
    """Arca platform PaaS adapter using direct SDK calls.

    Uses SandboxFactory and SyncSandbox to provide unified
    PaasService interface for Arca sandbox platform.

    Credentials are pre-resolved by PaasServiceFactory and passed
    during initialization. This service is a pure executor with
    no internal tenant/config resolution logic.
    """

    def __init__(
        self,
        credentials: ArcaCredentials,
        arca_sandbox_plugin: ArcaSandboxPlugin,
        arca_provisioning_registry: ArcaProvisioningRegistry | None = None,
    ):
        """Initialize ArcaPaasService with pre-resolved credentials and plugin.

        Args:
            credentials: Pre-resolved ArcaCredentials from factory.
                Must have base_url and api_key populated.
            arca_sandbox_plugin: ArcaSandboxPlugin for sandbox lifecycle operations.

        Raises:
            ValueError: If credentials is None or missing required fields.
        """
        if credentials is None:
            raise ValueError("credentials is required")
        if not credentials.base_url:
            raise ValueError("credentials.base_url is required")
        if not credentials.api_key:
            raise ValueError("credentials.api_key is required")

        self._credentials = credentials
        self._arca_sandbox_plugin = arca_sandbox_plugin
        self._arca_provisioning_registry = arca_provisioning_registry
        self._logger = get_logger("core-service")

    async def get_credentials(self) -> ArcaCredentials:
        """Get the credentials used by this service instance.

        Returns:
            ArcaCredentials instance containing template_id and Arca platform credentials.
        """
        return self._credentials

    async def get_platform_type(self) -> TenantType:
        """Return Arca platform type."""
        return TenantType.ARCA

    def _translate_error(self, error: Exception, default_code: ErrorCode) -> PaasError:
        if isinstance(error, ArcaSandboxNotFoundError):
            return PaasError(
                ErrorCode.DEVICE_NOT_FOUND,
                f"Device not found: {error}",
                error,
            )
        if isinstance(error, ArcaSandboxTimeoutError):
            if default_code == ErrorCode.COMMAND_FAILED:
                return PaasError(
                    ErrorCode.COMMAND_TIMEOUT,
                    f"Command execution timed out: {error}",
                    error,
                )
            return PaasError(
                ErrorCode.DEVICE_CREATION_FAILED,
                f"Operation timed out: {error}",
                error,
            )
        if isinstance(error, ArcaSandboxError):
            error_str = str(error).lower()
            if "connection" in error_str or "unavailable" in error_str:
                return PaasError(
                    ErrorCode.PLATFORM_UNAVAILABLE,
                    f"Arca platform unavailable: {error}",
                    error,
                )
            return PaasError(default_code, str(error), error)

        return PaasError(default_code, str(error), error)

    def _check_sandbox_ready(
        self,
        sandbox: ArcaSandbox,
        sandbox_id: str,
        elapsed: float,
        timeout_seconds: int,
    ) -> bool:
        """Check if sandbox is ready and log/raise if timeout exceeded.

        Args:
            sandbox: The SyncSandbox to check
            sandbox_id: The sandbox ID for logging
            elapsed: Time elapsed since start
            timeout_seconds: Maximum time to wait

        Returns:
            True if sandbox is ready, False to continue waiting

        Raises:
            PaasError: If timeout exceeded without sandbox being ready
        """
        if sandbox.is_ready:
            self._logger.info(f"Sandbox {sandbox_id} is ready after {elapsed:.1f}s")
            return True
        if elapsed >= timeout_seconds:
            info = sandbox.get_info()
            error_msg = (
                f"Sandbox {info.sandbox_id} did not become ready within {timeout_seconds}s. "
                f"Current status: {info.status}"
            )
            self._logger.error(error_msg)
            raise PaasError(
                ErrorCode.DEVICE_NOT_READY,
                error_msg,
            )
        return False

    async def _wait_for_sandbox_ready(
        self,
        sandbox: ArcaSandbox,
        timeout_seconds: int = 300,
        poll_interval: float = 2.0,
    ) -> None:
        """Wait for sandbox to become ready.

        Blocks the calling thread until sandbox.is_ready is True or timeout.

        Args:
            sandbox: The ArcaSandbox to wait for
            timeout_seconds: Maximum time to wait (default: 300s = 5min)
            poll_interval: Seconds between checks (default: 2s)

        Raises:
            PaasError: With DEVICE_NOT_READY if sandbox doesn't become ready
        """
        import time

        start_time = time.time()
        sandbox_info = sandbox.get_info()
        sandbox_id = sandbox_info.sandbox_id

        self._logger.info(
            f"Waiting for sandbox {sandbox_id} to become ready "
            f"(max wait: {timeout_seconds}s, poll interval: {poll_interval}s)"
        )

        while True:
            elapsed = time.time() - start_time
            if self._check_sandbox_ready(sandbox, sandbox_id, elapsed, timeout_seconds):
                return
            await asyncio.sleep(poll_interval)

    def _wait_for_sandbox_ready_sync(
        self,
        sandbox: ArcaSandbox,
        timeout_seconds: int = 300,
        poll_interval: float = 2.0,
    ) -> None:
        """Synchronous version of _wait_for_sandbox_ready for use in to_thread()."""
        import time

        start_time = time.time()
        sandbox_info = sandbox.get_info()
        sandbox_id = sandbox_info.sandbox_id

        self._logger.info(
            f"Waiting for sandbox {sandbox_id} to become ready "
            f"(max wait: {timeout_seconds}s, poll interval: {poll_interval}s)"
        )

        while True:
            elapsed = time.time() - start_time
            if self._check_sandbox_ready(sandbox, sandbox_id, elapsed, timeout_seconds):
                return
            time.sleep(poll_interval)

    def _build_mount_points(
        self,
        mount_config: list[Any] | None,
    ) -> list[MountPoint]:
        """Build Arca SDK MountPoint list from config.

        Handles both MountPoint objects (from ArcaCreateConfig) and
        dict configs for backward compatibility.
        """
        if not mount_config:
            return []
        mount_points = []
        for mp in mount_config:
            # If already a MountPoint, use it directly
            if isinstance(mp, MountPoint):
                mount_points.append(mp)
                continue
            # Otherwise, convert from dict
            permission = MountPermission.READ_ONLY
            if mp.get("permission", "READ_ONLY").upper() == "READ_WRITE":
                permission = MountPermission.READ_WRITE
            mount_points.append(
                MountPoint(
                    id=mp.get("id", "default"),
                    remote_dir=mp.get("remote_dir", ""),
                    local_dir=mp.get("local_dir", ""),
                    permission=permission,
                )
            )
        return mount_points

    def _resolve_request_api_key(self, config: ArcaCreateConfig) -> str | None:
        """Resolve an optional request credential through the registry seam.

        Fail-open is owned by the registry: a missing/illegal payload or an
        extension exception both yield ``None`` so the legacy fixed credential
        path is preserved. This service stays engine-agnostic.
        """
        registry = self._arca_provisioning_registry
        if registry is None:
            return None
        return registry.resolve_request_api_key(config.extra_properties)

    async def create_device(  # type: ignore[override]
        self,
        config: ArcaCreateConfig,
    ) -> ArcaCreationResult:
        """Create Arca sandbox device using SDK directly.

        Args:
            config: ArcaCreateConfig containing:
                - template_id: str (required) - Arca template ID
                - ttl_in_minutes: int (default: 1440)
                - name: str | None - device name
                - description: str | None - device description
                - mount_points: list[MountPoint] | None - OSS mount points
                - envs: dict[str, str] | None - environment variables
                - outbound_operation_rule: Any | None
                - resource_spec: ResourceSpecification | None - CPU/memory specification
                - metadata: dict[str, str] | None - passthrough metadata
                - storage: Storage | None - NAS storage binding

        Returns:
            ArcaCreationResult with full sandbox details.

        Raises:
            PaasError: With DEVICE_CREATION_FAILED on creation error.
                       With DEVICE_NOT_READY if sandbox doesn't become ready within timeout.
        """
        return await asyncio.to_thread(self._create_device_sync, config)

    def _create_device_sync(
        self,
        config: ArcaCreateConfig,
    ) -> ArcaCreationResult:
        """Synchronous implementation of create_device for use in to_thread()."""
        sandbox = None
        template_id = None
        log_config = _log_safe_model(config)
        try:
            # Log detailed config parameters at info level using a redacted copy.
            self._logger.info(
                "[create_device] ArcaCreateConfig: %s",
                json.dumps(log_config, default=str),
            )

            # Use config template_id or fall back to credentials arca_template_id
            template_id = config.template_id or self._credentials.arca_template_id
            if not template_id:
                raise PaasError(
                    ErrorCode.DEVICE_CREATION_FAILED,
                    "Missing required config field: template_id",
                )

            # Build mount points from config
            mount_points = self._build_mount_points(config.mount_points)

            timeout = 60

            create_params = {
                "template_id": template_id,
                "ttl_in_minutes": config.ttl_in_minutes,
                "envs": config.envs,
                "mount_points": mount_points if mount_points else None,
                "resource_spec": config.resource_spec,
                "metadata": config.metadata,
                "outbound_operation_rule": config.outbound_operation_rule,
                "storage": config.storage,
                "image": config.docker_image,
                "timeout_in_millis": timeout * 1000,
                "ready_timeout_in_seconds": timeout,
            }
            request_api_key = self._resolve_request_api_key(config)
            if request_api_key is not None:
                create_params["api_key"] = request_api_key
            # Build log-safe params dict with storage and image converted to dict if present
            log_params = {
                k: (v.model_dump() if k == "storage" and v is not None else v)
                for k, v in create_params.items()
                if k != "api_key"
            }
            self._logger.info(
                f"Creating sandbox with params: {json.dumps(log_params, default=str)}"
            )
            if config.docker_image:
                self._logger.info(
                    "[create_device] docker_image=%s (overriding template default)",
                    config.docker_image,
                )
            sandbox = self._arca_sandbox_plugin.create_sync_sandbox(**create_params)

            # Wait for sandbox to become ready (blocking with timeout)
            self._wait_for_sandbox_ready_sync(sandbox)

            # Get final sandbox info (after ready)
            info = sandbox.get_info()

            # Log a redacted copy; never emit raw SDK responses because future
            # versions may add credential-bearing fields.
            self._logger.info(
                "[create_device] Arca sandbox info: %s",
                json.dumps(_log_safe_model(info), default=str),
            )

            result_outbound_rule = None
            if info.outbound_operation_rule is not None:
                result_outbound_rule = info.outbound_operation_rule

            # Build ArcaCreationResult with flattened fields (D-03, D-05)
            status = (
                info.status.value if hasattr(info.status, "value") else str(info.status)
            )

            return ArcaCreationResult(
                platform="arca",
                status=status,
                template_id=info.template_id,
                sandbox_id=info.sandbox_id,
                resources=info.resources,
                ttl_in_minutes=info.ttl_in_minutes,
                envs=info.envs,
                snapshot_id=info.snapshot_id,
                metadata=info.metadata,
                outbound_operation_rule=result_outbound_rule,
            )

        except PaasError as exc:
            sandbox_id = sandbox.sandbox_id if sandbox is not None else None
            self._logger.error(
                "[create_device] PaasError template_id=%s sandbox_id=%s "
                "error_type=%s config=%s",
                template_id,
                sandbox_id,
                type(exc).__name__,
                json.dumps(log_config, default=str),
            )
            raise
        except Exception as exc:
            sandbox_id = sandbox.sandbox_id if sandbox is not None else None
            self._logger.error(
                "[create_device] failed template_id=%s sandbox_id=%s "
                "error_type=%s config=%s",
                template_id,
                sandbox_id,
                type(exc).__name__,
                json.dumps(log_config, default=str),
            )
            raise self._translate_error(exc, ErrorCode.DEVICE_CREATION_FAILED)

    async def destroy_device(self, paas_device_id: str) -> bool:
        """Destroy Arca sandbox using SDK directly.

        Args:
            paas_device_id: Arca sandbox_id to destroy.

        Returns:
            True if successful.

        Raises:
            PaasError: With DEVICE_DESTROY_FAILED or DEVICE_NOT_FOUND.
        """
        return await asyncio.to_thread(self._destroy_device_sync, paas_device_id)

    def _destroy_device_sync(self, paas_device_id: str) -> bool:
        """Synchronous implementation of destroy_device for use in to_thread().

        Extracts storage_id from sandbox info before destroying, then
        attempts best-effort storage cleanup regardless of whether the
        sandbox was already destroyed (idempotent destroy must still
        clean up any associated NAS storage).
        """
        # Step 0: Extract storage_id BEFORE destroy
        # sandbox.get_info() is unavailable after sandbox.destroy()
        sandbox = None
        storage_id = None
        try:
            sandbox = self._arca_sandbox_plugin.connect_sync_sandbox(paas_device_id)
            try:
                info = sandbox.get_info()
                storage = getattr(info, "storage", None)
                if storage is None:
                    self._logger.warning(
                        f"Storage cleanup skipped: storage attribute missing, "
                        f"paas_device_id={paas_device_id}"
                    )
                else:
                    # Compatible with both dict and object types
                    raw_storage_id = None
                    _storage_type_ok = False
                    if isinstance(storage, dict):
                        raw_storage_id = storage.get("storage_id")
                        _storage_type_ok = True
                    elif hasattr(storage, "storage_id"):
                        raw_storage_id = getattr(storage, "storage_id", None)
                        _storage_type_ok = True
                    else:
                        self._logger.warning(
                            f"Storage cleanup skipped: unrecognized storage type, "
                            f"paas_device_id={paas_device_id}, "
                            f"storage_type={type(storage).__name__}"
                        )

                    if _storage_type_ok:
                        # Final validation: must be a non-empty string
                        if raw_storage_id is None:
                            self._logger.warning(
                                f"Storage cleanup skipped: storage_id missing, "
                                f"paas_device_id={paas_device_id}, "
                                f"get_info={_safe_repr(info)}"
                            )
                        elif not isinstance(raw_storage_id, str):
                            self._logger.warning(
                                f"Storage cleanup skipped: storage_id is not a string, "
                                f"paas_device_id={paas_device_id}, "
                                f"storage_id={_safe_repr(raw_storage_id)}"
                            )
                        elif raw_storage_id == "":
                            self._logger.warning(
                                f"Storage cleanup skipped: storage_id is empty string, "
                                f"paas_device_id={paas_device_id}"
                            )
                        else:
                            storage_id = raw_storage_id
            except Exception:
                self._logger.warning(
                    f"Storage cleanup skipped: get_info failed, "
                    f"paas_device_id={paas_device_id}",
                    exc_info=True,
                )
        except ArcaSandboxNotFoundError:
            # Sandbox already gone — normal idempotent destroy.
            # No traceback needed; Step 1 will confirm and fall through
            # to storage cleanup.
            pass
        except Exception:
            self._logger.warning(
                "Failed to connect to sandbox before destroy for %s",
                paas_device_id,
                exc_info=True,
            )

        # Step 1: Destroy (or confirm already destroyed)
        # Default to True — if sandbox is already gone that's a successful
        # idempotent destroy.  Only set to False when destroy() explicitly fails.
        success = True
        try:
            if sandbox is None:
                self._logger.info(
                    f"Destroying sandbox with paas_device_id: {paas_device_id}"
                )
                sandbox = self._arca_sandbox_plugin.connect_sync_sandbox(paas_device_id)
            result = sandbox.destroy()
            success = (
                result if isinstance(result, bool) else getattr(result, "success", True)
            )
        except ArcaSandboxNotFoundError:
            # Already destroyed — idempotent.  Fall through to storage cleanup.
            self._logger.warning(
                "Sandbox %s not found during destroy, "
                "treating as successful (already destroyed)",
                paas_device_id,
            )
        except ArcaSandboxError as e:
            error_str = str(e).lower()
            # ARCA reports an externally reclaimed sandbox as either "not
            # found", "does not exist", or "sandbox destroyed". All three
            # mean there is no resource left to destroy. Other connection
            # failures remain non-idempotent because the sandbox may still be
            # running.
            if "sandbox" in error_str and (
                "not found" in error_str
                or "does not exist" in error_str
                or "sandbox destroyed" in error_str
            ):
                self._logger.warning(
                    "Sandbox %s not found during destroy, "
                    "treating as successful (already destroyed)",
                    paas_device_id,
                )
                # Fall through to storage cleanup below
            else:
                raise self._translate_error(e, ErrorCode.DEVICE_DESTROY_FAILED)
        except Exception as e:
            raise self._translate_error(e, ErrorCode.DEVICE_DESTROY_FAILED)

        # Step 2: Best-effort storage cleanup (always reached, even on
        # idempotent destroy paths — NAS volumes must be cleaned up
        # regardless of whether the sandbox was already gone).
        if storage_id is not None:
            tenant_name = self._credentials.tenant_name
            if tenant_name:
                try:
                    deleted = self._arca_sandbox_plugin.delete_storage(
                        storage_id, tenant_name
                    )
                    if deleted:
                        self._logger.info("Storage deleted: %s", storage_id)
                    else:
                        self._logger.warning("Storage deletion failed: %s", storage_id)
                except Exception:
                    self._logger.warning(
                        "Storage deletion exception for %s",
                        storage_id,
                        exc_info=True,
                    )

        return success

    async def execute_command(
        self,
        paas_device_id: str,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> CommandResult:
        """Execute command using SDK terminal.

        Args:
            paas_device_id: Arca sandbox_id.
            cmd: Command string to execute.
            env: Command execution context (environment variables), optional.
            timeout_seconds: Maximum execution time in seconds (default: 30).

        Returns:
            Unified CommandResult with execution output (includes env echo back).

        Raises:
            PaasError: With COMMAND_FAILED, COMMAND_TIMEOUT, or DEVICE_UNAVAILABLE.
        """
        return await asyncio.to_thread(
            self._execute_command_sync, paas_device_id, cmd, env, timeout_seconds
        )

    def _execute_command_sync(
        self,
        paas_device_id: str,
        cmd: str,
        env: dict[str, str] | None,
        timeout_seconds: int,
    ) -> CommandResult:
        """Synchronous implementation of execute_command for use in to_thread()."""
        try:
            sandbox = self._arca_sandbox_plugin.connect_sync_sandbox(paas_device_id)
            result = sandbox.exec_command(
                cmd=cmd,
                timeout_in_millis=timeout_seconds * 1000,
                envs=env,
            )
            return CommandResult(
                exit_code=result.exit_code,
                stdout=result.stdout if hasattr(result, "stdout") else "",
                stderr=result.stderr if hasattr(result, "stderr") else "",
                execution_time_ms=int(result.elapsed_time)
                if hasattr(result, "elapsed_time")
                else 0,
                command=cmd,
                env=env,
            )
        except ArcaSandboxTimeoutError as e:
            raise PaasError(ErrorCode.COMMAND_TIMEOUT, str(e), e)
        except Exception as e:
            raise self._translate_error(e, ErrorCode.COMMAND_FAILED)

    async def get_device_info(self, paas_device_id: str) -> ArcaDeviceInfo:
        """Get Arca device info by sandbox_id.

        Args:
            paas_device_id: Arca sandbox_id to query.

        Returns:
            ArcaDeviceInfo with full sandbox details from SDK.

        Raises:
            PaasError: With DEVICE_NOT_FOUND if sandbox doesn't exist.
        """
        return await asyncio.to_thread(self._get_device_info_sync, paas_device_id)

    def _get_device_info_sync(self, paas_device_id: str) -> ArcaDeviceInfo:
        """Synchronous implementation of get_device_info for use in to_thread()."""
        try:
            self._logger.info(f"Getting device info: {paas_device_id}")
            sandbox = self._arca_sandbox_plugin.connect_sync_sandbox(paas_device_id)
            info = sandbox.get_info()

            # Convert SDK status enum to string if needed
            status = (
                info.status.value if hasattr(info.status, "value") else str(info.status)
            )

            return ArcaDeviceInfo(
                platform="arca",
                status=status,
                sandbox_id=info.sandbox_id,
                template_id=info.template_id,
                ip_address=getattr(info, "ip_address", None),
                ttl_seconds=getattr(info, "ttl_seconds", 0),
                ttl_timestamp=getattr(info, "ttl_timestamp", None),
                created_at=getattr(info, "created_at", datetime.now()),
                name=getattr(info, "name", None),
                description=getattr(info, "description", None),
                resource_spec=getattr(info, "resource_spec", None),
                mount_points=getattr(info, "mount_points", None),
                envs=getattr(info, "envs", None),
                storage=getattr(info, "storage", None),
            )
        except ArcaSandboxNotFoundError:
            raise PaasError(
                ErrorCode.DEVICE_NOT_FOUND,
                f"Device {paas_device_id} not found",
            )
        except Exception as e:
            raise self._translate_error(e, ErrorCode.DEVICE_NOT_FOUND)

    async def update_outbound_operation_rule(
        self,
        paas_device_id: str,
        outbound_operation_rule: OutBoundOperationRule,
        mode: OutBoundOperationRuleUpdatedMode | None = None,
    ) -> bool:
        """Update outbound operation rule for Arca sandbox.

        Args:
            paas_device_id: Arca sandbox_id to update.
            outbound_operation_rule: New outbound operation rule to apply.
            mode: Update mode (REPLACE or APPEND). Defaults to REPLACE if not provided.

        Returns:
            True if successful.

        Raises:
            PaasError: With DEVICE_NOT_FOUND if sandbox doesn't exist.
                       With DEVICE_UNAVAILABLE if update fails.
        """
        return await asyncio.to_thread(
            self._update_outbound_operation_rule_sync,
            paas_device_id,
            outbound_operation_rule,
            mode,
        )

    def _update_outbound_operation_rule_sync(
        self,
        paas_device_id: str,
        outbound_operation_rule: OutBoundOperationRule,
        mode: OutBoundOperationRuleUpdatedMode | None = None,
    ) -> bool:
        """Synchronous implementation of update_outbound_operation_rule for use in to_thread()."""
        try:
            self._logger.info(
                f"Updating outbound operation rule for sandbox: {paas_device_id}"
            )
            sandbox = self._arca_sandbox_plugin.connect_sync_sandbox(paas_device_id)
            update_mode = OutBoundOperationRuleUpdatedMode.REPLACE
            if mode and mode == OutBoundOperationRuleUpdatedMode.APPEND:
                update_mode = OutBoundOperationRuleUpdatedMode.APPEND

            result = sandbox.update_outbound_rule(
                rule=outbound_operation_rule,
                updated_mode=update_mode,
            )
            self._logger.info(
                f"Outbound operation rule updated successfully: {paas_device_id}, result={result}"
            )
            return bool(result)
        except ArcaSandboxNotFoundError:
            raise PaasError(
                ErrorCode.DEVICE_NOT_FOUND,
                f"Device {paas_device_id} not found",
            )
        except Exception as e:
            self._logger.error(
                f"Failed to update outbound operation rule for {paas_device_id}: {e}"
            )
            raise self._translate_error(e, ErrorCode.DEVICE_UNAVAILABLE)

    async def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
        ws_conn_mode: str | None = None,
    ) -> WsConnectionInfo:
        """Resolve WebSocket connection info for an Arca device.

        Delegates to the underlying ArcaSandboxPlugin for constructing
        the proxypass URL, JWT token, and expiry.

        Args:
            paas_device_id: Arca sandbox_id (raw platform device ID).
            port: Target port on the device.
            path: WebSocket path (e.g., /api/openclaw/ws).

        Returns:
            WsConnectionInfo with wss:// URL, JWT token, target, and expiry.
        """
        return self._arca_sandbox_plugin.resolve_ws_conn_info(
            paas_device_id=paas_device_id,
            port=port,
            path=path,
            template_id=self._credentials.template_id,
        )

    async def resolve_invoke_http_info(
        self, paas_device_id: str, port: int, path: str | None = None
    ) -> HttpConnectionInfo:
        """Resolve HTTP connection info for an Arca device via proxypass proxy.

        Delegates to the underlying ArcaSandboxPlugin for constructing
        the proxypass URL and JWT token.

        Args:
            paas_device_id: Arca sandbox_id (raw platform device ID).
            port: Target port on the device.
            path: HTTP path (defaults to "/" when None).

        Returns:
            HttpConnectionInfo with https:// URL and JWT auth token.
        """
        return self._arca_sandbox_plugin.resolve_http_connection_info(
            paas_device_id=paas_device_id,
            port=port,
            path=path if path is not None else "/",
            template_id=self._credentials.template_id,
        )

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
        """STUB: Invoke HTTP request on an Arca device.

        Args:
            paas_device_id: Arca sandbox_id.
            method: HTTP method (GET, POST, PUT, DELETE, etc.).
            port: Target port on the device.
            path: Request path.
            query_string: Query string or None.
            headers: HTTP headers dict.
            body: Raw request body bytes.

        Raises:
            NotImplementedError: Arca platform does not support HTTP invocation.
                Only Local platform supports direct HTTP proxy to containers.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support HTTP invocation. "
            "Only Local platform supports direct HTTP proxy to containers."
        )

    async def restart_device(self, paas_device_id: str) -> bool:
        """STUB: Restart Arca device.

        Args:
            paas_device_id: Device ID to restart (Arca sandbox_id).

        Raises:
            NotImplementedError: Arca platform restart not yet implemented.
        """
        raise NotImplementedError("Arca platform restart_device not yet implemented")

    async def update_device(
        self,
        paas_device_id: str,
        config: DeviceCreateConfig | None = None,
    ) -> bool:
        """STUB: Update Arca device configuration.

        Arca has no native update API. Config updates are handled at the
        DeviceService layer via destroy + create (rebuild container with
        new config).

        Raises:
            NotImplementedError: Arca platform update not supported at PaasService level.
        """
        raise NotImplementedError("Arca platform update_device not yet implemented")

    async def get_info(self, sandbox_id: str) -> SandboxInfo:
        """Get sandbox information.

        .. deprecated::
            请使用 `get_device_info()` 替代。
            该方法将在后续版本中移除。

        Args:
            sandbox_id: Arca sandbox ID (without @tenant_id suffix).

        Returns:
            SandboxInfo with sandbox details including status and ttl_timestamp.

        Raises:
            PaasError: With DEVICE_NOT_FOUND if sandbox doesn't exist.
        """
        return await asyncio.to_thread(self._get_info_sync, sandbox_id)

    def _get_info_sync(self, sandbox_id: str) -> SandboxInfo:
        """Synchronous implementation of get_info for use in to_thread()."""
        try:
            self._logger.info(f"Getting info for sandbox: {sandbox_id}")
            sandbox = self._arca_sandbox_plugin.connect_sync_sandbox(sandbox_id)
            info = sandbox.get_info()
            return SandboxInfo(
                sandbox_id=info.sandbox_id,
                status=str(info.status.value)
                if hasattr(info.status, "value")
                else str(info.status),
                ttl_timestamp=int(float(info.ttl_timestamp))
                if hasattr(info, "ttl_timestamp") and info.ttl_timestamp is not None
                else None,
            )
        except ArcaSandboxNotFoundError as e:
            raise PaasError(
                ErrorCode.DEVICE_NOT_FOUND, f"Sandbox {sandbox_id} not found", e
            )
        except Exception as e:
            raise self._translate_error(e, ErrorCode.DEVICE_UNAVAILABLE)

    async def extend_ttl(self, sandbox_id: str, ttl_minutes: int) -> bool:
        """Extend sandbox TTL.

        Args:
            sandbox_id: Arca sandbox ID (without @tenant_id suffix).
            ttl_minutes: Additional TTL minutes to add.

        Returns:
            True if successful.

        Raises:
            PaasError: With DEVICE_NOT_FOUND or DEVICE_UNAVAILABLE.
        """
        return await asyncio.to_thread(self._extend_ttl_sync, sandbox_id, ttl_minutes)

    def _extend_ttl_sync(self, sandbox_id: str, ttl_minutes: int) -> bool:
        """Synchronous implementation of extend_ttl for use in to_thread()."""
        try:
            self._logger.info(
                f"Extending TTL for sandbox {sandbox_id} by {ttl_minutes} minutes"
            )
            sandbox = self._arca_sandbox_plugin.connect_sync_sandbox(sandbox_id)
            result = sandbox.extend_ttl(ttl_minutes)
            if isinstance(result, bool):
                return result
            return getattr(result, "success", True)
        except ArcaSandboxNotFoundError as e:
            raise PaasError(
                ErrorCode.DEVICE_NOT_FOUND, f"Sandbox {sandbox_id} not found", e
            )
        except Exception as e:
            raise self._translate_error(e, ErrorCode.DEVICE_UNAVAILABLE)

    async def update_device_ttl(self, paas_device_id: str) -> TTLInfo:
        """Extend device TTL - target is now() + 24 hours (Arca platform limit).

        Args:
            paas_device_id: Arca sandbox ID (without @tenant_id suffix).

        Returns:
            TTLInfo with old and new expiration times.

        Raises:
            PaasError: With DEVICE_NOT_FOUND if device doesn't exist.
        """
        return await asyncio.to_thread(self._update_device_ttl_sync, paas_device_id)

    def _update_device_ttl_sync(self, paas_device_id: str) -> TTLInfo:
        """Synchronous implementation of update_device_ttl for use in to_thread()."""
        from secbaas.community.api.health_check.bot import TTLInfo

        # Strip @template_id suffix if present
        sandbox_id = (
            paas_device_id.split("@")[0] if "@" in paas_device_id else paas_device_id
        )

        now = datetime.now()
        target_expiration = now + timedelta(hours=24)  # Arca platform limit

        self._logger.info(
            f"[update_device_ttl] Extending TTL for {sandbox_id}, target={target_expiration}"
        )

        try:
            # Get current TTL (call sync version directly)
            info = self._get_device_info_sync(sandbox_id)

            if info.ttl_timestamp is None:
                self._logger.warning(
                    f"[update_device_ttl] No TTL info for {sandbox_id}, cannot extend"
                )
                return TTLInfo(
                    paas_device_id=paas_device_id,
                    old_expiration_time=None,
                    new_expiration_time=None,
                    success=False,
                    skipped=False,
                    error="No TTL info available",
                )

            old_expiration = datetime.fromtimestamp(info.ttl_timestamp / 1000)

            # Calculate minutes to add
            ttl_minutes = int((target_expiration - old_expiration).total_seconds() / 60)

            if ttl_minutes <= 0:
                self._logger.info(
                    f"[update_device_ttl] {sandbox_id} already at or past target, skipping"
                )
                return TTLInfo(
                    paas_device_id=paas_device_id,
                    old_expiration_time=old_expiration,
                    new_expiration_time=old_expiration,
                    success=False,
                    skipped=False,
                    error="Already at or past target expiration",
                )

            # Execute TTL extension (call sync version directly)
            success = self._extend_ttl_sync(sandbox_id, ttl_minutes)

            if success:
                # Use target_expiration as new expiration (avoids extra API call)
                new_expiration = target_expiration
                self._logger.info(
                    f"[update_device_ttl] {sandbox_id} TTL extended to {new_expiration}"
                )
            else:
                new_expiration = old_expiration
                self._logger.warning(
                    f"[update_device_ttl] {sandbox_id} TTL extension failed"
                )

            return TTLInfo(
                paas_device_id=paas_device_id,
                old_expiration_time=old_expiration,
                new_expiration_time=new_expiration,
                success=success,
                skipped=False,
                error=None if success else "TTL extension failed",
            )

        except ArcaSandboxNotFoundError as e:
            raise PaasError(
                ErrorCode.DEVICE_NOT_FOUND, f"Device {sandbox_id} not found", e
            )
        except Exception as e:
            raise self._translate_error(e, ErrorCode.DEVICE_UNAVAILABLE)

    # ------------------------------------------------------------------
    # File transfer — Arca curl-based implementation (v1.4 launch platform)
    # ------------------------------------------------------------------

    async def pull_file_from_url(
        self,
        paas_device_id: str,
        source_url: str,
        device_path: str,
        timeout_seconds: int = 300,
    ) -> None:
        """Download file from URL to device path via curl in Arca sandbox.

        Args:
            paas_device_id: Arca sandbox_id.
            source_url: URL to download from (e.g. OSS pre-signed GET URL).
            device_path: Absolute path on device to save the file to.
            timeout_seconds: Maximum download time in seconds (default: 300).

        Raises:
            PaasError: With FILE_TRANSFER_FAILED if download fails or times out.
        """
        return await asyncio.to_thread(
            self._pull_file_sync,
            paas_device_id,
            source_url,
            device_path,
            timeout_seconds,
        )

    def _pull_file_sync(
        self,
        paas_device_id: str,
        source_url: str,
        device_path: str,
        timeout_seconds: int,
    ) -> None:
        """Synchronous implementation of pull_file_from_url for use in to_thread()."""
        try:
            sandbox = self._arca_sandbox_plugin.connect_sync_sandbox(paas_device_id)
            cmd = (
                f"curl -fSL --create-dirs -o {shlex.quote(device_path)} "
                f"{shlex.quote(source_url)}"
            )
            result = sandbox.exec_command(
                cmd=cmd,
                timeout_in_millis=timeout_seconds * 1000,
                envs=None,
            )
            if result.exit_code != 0:
                raise PaasError(
                    ErrorCode.FILE_TRANSFER_FAILED,
                    f"curl pull failed with exit code {result.exit_code}: {result.stderr}",
                )
            safe_url = urlunparse(urlparse(source_url)._replace(query="", fragment=""))
            self._logger.info(
                f"File pulled: {safe_url} -> {device_path} (sandbox={paas_device_id})"
            )
        except ArcaSandboxTimeoutError as e:
            raise PaasError(
                ErrorCode.FILE_TRANSFER_FAILED,
                f"File pull timed out after {timeout_seconds}s: {e}",
                e,
            )
        except Exception as e:
            raise self._translate_error(e, ErrorCode.FILE_TRANSFER_FAILED)

    async def push_file_to_url(
        self,
        paas_device_id: str,
        device_path: str,
        target_url: str,
        timeout_seconds: int = 300,
    ) -> None:
        """Upload file from device path to URL via curl in Arca sandbox.

        Args:
            paas_device_id: Arca sandbox_id.
            device_path: Absolute path on device of the file to upload.
            target_url: URL to upload to (e.g. OSS pre-signed PUT URL).
            timeout_seconds: Maximum upload time in seconds (default: 300).

        Raises:
            PaasError: With FILE_TRANSFER_FAILED if upload fails or times out.
        """
        return await asyncio.to_thread(
            self._push_file_sync,
            paas_device_id,
            device_path,
            target_url,
            timeout_seconds,
        )

    def _push_file_sync(
        self,
        paas_device_id: str,
        device_path: str,
        target_url: str,
        timeout_seconds: int,
    ) -> None:
        """Synchronous implementation of push_file_to_url for use in to_thread()."""
        try:
            sandbox = self._arca_sandbox_plugin.connect_sync_sandbox(paas_device_id)
            cmd = (
                f"curl -fSL -X PUT -T {shlex.quote(device_path)} "
                f"{shlex.quote(target_url)}"
            )
            result = sandbox.exec_command(
                cmd=cmd,
                timeout_in_millis=timeout_seconds * 1000,
                envs=None,
            )
            if result.exit_code != 0:
                raise PaasError(
                    ErrorCode.FILE_TRANSFER_FAILED,
                    f"curl push failed with exit code {result.exit_code}: {result.stderr}",
                )
            safe_url = urlunparse(urlparse(target_url)._replace(query="", fragment=""))
            self._logger.info(
                f"File pushed: {device_path} -> {safe_url} (sandbox={paas_device_id})"
            )
        except ArcaSandboxTimeoutError as e:
            raise PaasError(
                ErrorCode.FILE_TRANSFER_FAILED,
                f"File push timed out after {timeout_seconds}s: {e}",
                e,
            )
        except Exception as e:
            raise self._translate_error(e, ErrorCode.FILE_TRANSFER_FAILED)


class SandboxInfo:
    """Sandbox information container."""

    def __init__(
        self,
        sandbox_id: str,
        status: str,
        ttl_timestamp: int | None,
    ):
        self.sandbox_id = sandbox_id
        self.status = status
        self.ttl_timestamp = ttl_timestamp
