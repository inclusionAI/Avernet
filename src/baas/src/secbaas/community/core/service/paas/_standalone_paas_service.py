"""Standalone Docker platform PaaS adapter — plugin delegation pattern.

Delegates Docker container lifecycle operations to a DockerSandboxPlugin
instance. All docker-py SDK access is confined to the Plugin layer;
this service does only pre-validation and result construction.

Architecture follows K8sPaasService pattern (the reference implementation):
    StandalonePaasService  →  DockerSandboxPlugin  →  DockerSandbox
    (orchestration)           (factory protocol)       (device handle)

Lifecycle:
    create_device():   Pre-validate config → plugin.create_device() →
                       build DockerCreationResult from sandbox info.
    destroy_device():  plugin.destroy_device() — idempotent.
    restart_device():  plugin.connect_device() → sandbox.restart().
    execute_command(): plugin.connect_device() → sandbox.exec_command().
    get_device_info(): plugin.connect_device() → sandbox.get_info().
    update_device():   Pre-validate → destroy + create (container rebuild).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

from secbaas.community.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.community.api.device_manage import (
    CommandResult,
    DeviceCreateConfig,
    DeviceCreationResult,
    DockerCreateConfig,
    DockerCreationResult,
    DockerCredentials,
    DockerDeviceInfo,
    ErrorCode,
    PaasError,
)
from secbaas.community.api.tenant_manage import TenantType
from secbaas.community.logger import get_logger

from ._paas_service import PaasService

if TYPE_CHECKING:
    from secbaas.community.api.device_manage import DeviceInfo
    from secbaas.community.api.health_check.bot import TTLInfo
    from secbaas.community.spi.sandbox.docker import DockerSandboxPlugin


class StandalonePaasService(PaasService):
    """Standalone Docker platform PaaS adapter — delegates to DockerSandboxPlugin.

    All container operations are delegated to the injected DockerSandboxPlugin
    (real or stub). This service performs only pre-validation (isinstance checks,
    required-field verification) and result construction. Docker-py SDK access
    is fully confined to the Plugin layer.

    Constructor parameters:
        plugin: DockerSandboxPlugin for Docker operations (real or stub).
        credentials: DockerCredentials with template_id, template_uuid, tenant_name.
        health_endpoint: Container health check path (default "/health"),
            forwarded to plugin.create_device() per checker B1 fix.
        health_timeout_seconds: Health check timeout in seconds (default 120),
            forwarded to plugin.create_device() per checker B1 fix.
    """

    def __init__(
        self,
        plugin: DockerSandboxPlugin,
        credentials: DockerCredentials,
        health_endpoint: str = "/health",
        health_timeout_seconds: int = 120,
    ):
        if plugin is None:
            raise ValueError("plugin is required")
        if credentials is None:
            raise ValueError("credentials is required")
        self._plugin = plugin
        self._credentials = credentials
        self._health_endpoint = health_endpoint
        self._health_timeout_seconds = health_timeout_seconds
        self._logger = get_logger("standalone_paas_service")

    # ------------------------------------------------------------------
    # ABC methods: credential / platform identity
    # ------------------------------------------------------------------

    async def get_credentials(self) -> DockerCredentials:
        """Return the DockerCredentials injected at construction time."""
        return self._credentials

    async def get_platform_type(self) -> TenantType:
        """Return the Docker platform type identifier."""
        return TenantType.DOCKER

    # ------------------------------------------------------------------
    # create_device — pre-validation + plugin delegation (D-14)
    # ------------------------------------------------------------------

    async def create_device(
        self,
        config: DeviceCreateConfig,
    ) -> DeviceCreationResult:
        """Create a Docker container via plugin with pre-validation.

        Per D-14: This method performs only pre-validation (isinstance +
        required-field checks) and result construction. All container lifecycle
        operations (image pull, container create, start, health check, port
        extraction) are handled by the plugin.

        Args:
            config: DockerCreateConfig with image, container_port, envs, etc.

        Returns:
            DockerCreationResult with platform, container_id, host_port, status.

        Raises:
            PaasError(CONFIG_INVALID): If config is not a DockerCreateConfig.
            ValueError: If config.image or config.container_port is None.
        """
        if not isinstance(config, DockerCreateConfig):
            raise PaasError(
                ErrorCode.CONFIG_INVALID,
                f"StandalonePaasService requires DockerCreateConfig, "
                f"got {type(config).__name__}",
            )

        if config.image is None:
            raise ValueError("config.image is required for Docker container creation")
        if config.container_port is None:
            raise ValueError(
                "config.container_port is required for Docker container creation"
            )

        container_name = f"baas-agent-{uuid.uuid4().hex[:12]}"
        tenant_name = self._credentials.tenant_name or "unknown"

        self._logger.info(
            "Creating Docker device: image=%s, container_name=%s, tenant=%s, "
            "template_id=%s",
            config.image,
            container_name,
            tenant_name,
            self._credentials.template_id,
        )

        sandbox = await asyncio.to_thread(
            self._plugin.create_device,
            template_id=self._credentials.template_id,
            template_uuid=self._credentials.template_uuid,
            tenant_name=tenant_name,
            container_name=container_name,
            image=config.image,
            container_port=config.container_port,
            envs=config.envs or {},
            cpu_limit=config.cpu_limit,
            memory_limit=config.memory_limit,
            image_pull_policy="if_not_present",
            health_endpoint=self._health_endpoint,
            health_timeout_seconds=self._health_timeout_seconds,
        )

        info = sandbox.get_info()
        result = DockerCreationResult(
            platform="docker",
            container_id=sandbox.sandbox_id,
            host_port=info["host_port"],
            status=info.get("status", "unknown"),
        )
        self._logger.info(
            "create_device succeeded: container_id=%s, host_port=%d, status=%s",
            sandbox.sandbox_id[:12] if sandbox.sandbox_id else "?",
            info["host_port"],
            info.get("status", "unknown"),
        )
        return result

    # ------------------------------------------------------------------
    # destroy_device — plugin delegation (D-14, D-15)
    # ------------------------------------------------------------------

    async def destroy_device(self, paas_device_id: str) -> bool:
        """Destroy a Docker container via plugin (idempotent).

        Per D-15: Idempotent semantics are handled by the Plugin layer.
        If the container is already gone, returns True without error.

        Args:
            paas_device_id: Container ID (bare, without @template_id suffix).

        Returns:
            True on success or if container already does not exist.
        """
        self._logger.info("Destroying container: %s", paas_device_id)
        return await asyncio.to_thread(self._plugin.destroy_device, paas_device_id)

    # ------------------------------------------------------------------
    # restart_device — plugin.connect_device() + sandbox.restart()
    # ------------------------------------------------------------------

    async def restart_device(self, paas_device_id: str) -> bool:
        """Restart a Docker container via plugin.

        Connects to the container via plugin.connect_device(), then calls
        sandbox.restart() for container restart.

        Args:
            paas_device_id: Container ID (bare, without @template_id suffix).

        Returns:
            True on successful restart.

        Raises:
            PaasError: Propagated from plugin on failure.
        """
        self._logger.info("Restarting container: %s", paas_device_id)
        sandbox = await asyncio.to_thread(self._plugin.connect_device, paas_device_id)
        return await asyncio.to_thread(sandbox.restart)

    # ------------------------------------------------------------------
    # execute_command — plugin.connect_device() + sandbox.exec_command()
    # ------------------------------------------------------------------

    async def execute_command(
        self,
        paas_device_id: str,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> CommandResult:
        """Execute a command inside a Docker container via plugin.

        Connects to the container via plugin.connect_device(), then calls
        sandbox.exec_command() with timeout conversion (seconds to milliseconds).

        Args:
            paas_device_id: Container ID (bare, without @template_id suffix).
            cmd: Command string to execute inside the container.
            env: Environment variables dict, optional.
            timeout_seconds: Maximum execution time in seconds (default: 30).

        Returns:
            CommandResult with exit_code, stdout, stderr, execution_time_ms,
            command, and env fields.

        Raises:
            PaasError: Propagated from plugin on failure.
        """
        self._logger.info(
            "Executing command on container %s: %s",
            paas_device_id[:12],
            cmd,
        )
        sandbox = await asyncio.to_thread(self._plugin.connect_device, paas_device_id)
        result = await asyncio.to_thread(
            sandbox.exec_command,
            cmd=cmd,
            timeout_in_millis=timeout_seconds * 1000,
            envs=env,
        )
        return CommandResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            execution_time_ms=int(result.elapsed_time) if hasattr(result, "elapsed_time") else 0,
            command=cmd,
            env=env,
        )

    # ------------------------------------------------------------------
    # get_device_info — plugin.connect_device() + sandbox.get_info()
    # ------------------------------------------------------------------

    async def get_device_info(self, paas_device_id: str) -> DeviceInfo:
        """Query Docker container state via plugin and return DockerDeviceInfo.

        Connects to the container via plugin.connect_device(), then reads
        sandbox.get_info() to construct DockerDeviceInfo. The plugin handles
        all daemon interaction internally.

        Args:
            paas_device_id: Container ID (bare, without @template_id suffix).

        Returns:
            DockerDeviceInfo with platform, status, container_id, host_port,
            image fields.

        Raises:
            PaasError: Propagated from plugin on failure.
        """
        self._logger.info("Getting device info for container %s", paas_device_id[:12])
        sandbox = await asyncio.to_thread(self._plugin.connect_device, paas_device_id)
        info = sandbox.get_info()
        result = DockerDeviceInfo(
            platform="docker",
            status=info["status"],
            container_id=info["container_id"],
            host_port=info["host_port"],
            image=info.get("image", ""),
        )
        self._logger.debug(
            "Device info: status=%s, image=%s, host_port=%d, container_id=%s",
            info["status"],
            info.get("image", ""),
            info["host_port"],
            str(info["container_id"])[:12],
        )
        return result

    # ------------------------------------------------------------------
    # resolve_ws_conn_info — plugin delegation (D-01)
    # ------------------------------------------------------------------

    async def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
        ws_conn_mode: str | None = None,
    ) -> WsConnectionInfo:
        """Resolve WebSocket connection info via plugin.

        Delegates to plugin.resolve_ws_conn_info() which returns localhost
        URL. No Docker daemon query needed per D-01.

        Args:
            paas_device_id: Container ID (for logging, not used in URL).
            port: Host port bound to the container.
            path: WebSocket path (e.g. /api/openclaw/ws).

        Returns:
            WsConnectionInfo with ws_url, token, target, expires_at.
        """
        return await asyncio.to_thread(
            self._plugin.resolve_ws_conn_info,
            paas_device_id,
            port,
            path,
        )

    # ------------------------------------------------------------------
    # resolve_invoke_http_info — plugin delegation (D-01)
    # ------------------------------------------------------------------

    async def resolve_invoke_http_info(
        self,
        paas_device_id: str,
        port: int,
        path: str | None = None,
    ) -> HttpConnectionInfo:
        """Resolve HTTP connection info via plugin.

        Delegates to plugin.resolve_invoke_http_info() which returns localhost
        URL. No Docker daemon query needed per D-01.

        Args:
            paas_device_id: Container ID (for logging, not used in URL).
            port: Host port bound to the container.
            path: HTTP path (defaults to "/" if None).

        Returns:
            HttpConnectionInfo with http_url and token.
        """
        return await asyncio.to_thread(
            self._plugin.resolve_invoke_http_info,
            paas_device_id,
            port,
            path or "/",
        )

    # ------------------------------------------------------------------
    # invoke_http_in_device — plugin delegation (D-01)
    # ------------------------------------------------------------------

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
        """Invoke HTTP request on container via plugin.

        Delegates to plugin.invoke_http_in_device() which handles the
        HTTP forward over localhost.

        Args:
            paas_device_id: Container ID (for logging, not used in URL).
            method: HTTP method (GET, POST, PUT, DELETE, etc.).
            port: Host port bound to the container.
            path: Request path (e.g. /api/v1/health).
            query_string: Optional query string including leading '?'.
            headers: HTTP headers dict.
            body: Raw request body bytes.

        Returns:
            Dict with keys: status_code (int), headers (dict), body (base64 str).

        Raises:
            PaasError: Propagated from plugin on failure.
        """
        return await asyncio.to_thread(
            self._plugin.invoke_http_in_device,
            paas_device_id,
            method,
            port,
            path,
            query_string,
            headers,
            body,
        )

    # ------------------------------------------------------------------
    # update_device — destroy + create rebuild
    # ------------------------------------------------------------------

    async def update_device(
        self,
        paas_device_id: str,
        config: DeviceCreateConfig | None = None,
    ) -> bool:
        """Apply config changes via destroy+create container rebuild.

        Docker containers have immutable configuration — to apply config
        changes, the existing container is destroyed and a new one created.
        Delegates to self.destroy_device() and self.create_device() which
        in turn delegate to the plugin.

        Args:
            paas_device_id: Container ID (bare, without @template_id suffix).
            config: DockerCreateConfig with new container settings.

        Returns:
            True on success.

        Raises:
            PaasError(CONFIG_INVALID): If config is None or not a
                DockerCreateConfig.
        """
        if config is None:
            raise PaasError(
                ErrorCode.CONFIG_INVALID,
                "update_device requires a DockerCreateConfig for container "
                "rebuild. Got None.",
            )
        if not isinstance(config, DockerCreateConfig):
            raise PaasError(
                ErrorCode.CONFIG_INVALID,
                f"update_device requires DockerCreateConfig, "
                f"got {type(config).__name__}",
            )

        self._logger.info(
            "Updating container %s: destroy+create rebuild", paas_device_id
        )
        await self.destroy_device(paas_device_id)
        self._logger.info("Container %s destroyed for update rebuild", paas_device_id)
        await self.create_device(config)
        self._logger.info("Container update rebuild complete (new container created)")
        return True

    # ------------------------------------------------------------------
    # update_device_ttl — not supported
    # ------------------------------------------------------------------

    async def update_device_ttl(self, paas_device_id: str) -> TTLInfo:
        """Do not support TTL extension — Docker containers are persistent.

        StandalonePaasService manages Docker containers which are persistent
        resources with no native TTL mechanism. Containers live until explicitly
        destroyed via destroy_device.

        Args:
            paas_device_id: Container ID (bare, without @template_id suffix).

        Returns:
            Never returns — always raises NotImplementedError.

        Raises:
            NotImplementedError: TTL extension is not supported for Docker containers.
        """
        raise NotImplementedError(
            "StandalonePaasService does not support TTL extension. "
            "Docker containers are persistent resources managed via manual destroy."
        )

    # ------------------------------------------------------------------
    # update_outbound_operation_rule — not supported
    # ------------------------------------------------------------------

    async def update_outbound_operation_rule(
        self,
        paas_device_id: str,
        outbound_operation_rule: Any,
    ) -> bool:
        """Not supported: Docker platform does not support outbound operation rules.

        Args:
            paas_device_id: Container ID.
            outbound_operation_rule: Outbound operation rule configuration.

        Returns:
            Never returns — always raises NotImplementedError.

        Raises:
            NotImplementedError: Always — outbound operation rules not supported.
        """
        raise NotImplementedError(
            "update_outbound_operation_rule not yet implemented — "
            "Docker platform does not support outbound operation rules"
        )
