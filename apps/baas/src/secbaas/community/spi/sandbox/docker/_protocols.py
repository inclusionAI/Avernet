"""Docker sandbox plugin Protocol — contract for Docker sandbox lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from secbaas.community.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo


class DockerSandbox(Protocol):
    """A Docker sandbox device with lifecycle operations.

    Represents a single Docker container running a Bot instance.
    All methods are synchronous (def), matching the existing K8sSandbox
    and ArcaSandbox conventions. Callers wrap in asyncio.to_thread().
    """

    @property
    def is_ready(self) -> bool:
        """Check if the container is in running state."""
        ...

    @property
    def sandbox_id(self) -> str:
        """Return the sandbox container ID."""
        ...

    def get_info(self) -> Any:
        """Get sandbox info from container.attrs.

        Returns:
            An info object with at minimum: sandbox_id, status, image, host_port.
            Optional fields depend on the implementation.
        """
        ...

    def exec_command(
        self,
        cmd: str,
        timeout_in_millis: int = 30000,
        envs: dict[str, str] | None = None,
    ) -> Any:
        """Execute a command inside the sandbox container via exec_run.

        Args:
            cmd: Command string to execute.
            timeout_in_millis: Maximum execution time in milliseconds.
            envs: Environment variables for the command context.

        Returns:
            An object with at minimum: exit_code, stdout, stderr, elapsed_time.
        """
        ...

    def destroy(self) -> Any:
        """Destroy the sandbox (stop + remove container).

        Stop the container gracefully (30s timeout), then remove it.
        Idempotent: returns success if container already gone (404 ok).

        Returns:
            True/False or an object indicating success.
        """
        ...

    def restart(self) -> Any:
        """Restart the sandbox container via container.restart().

        Returns:
            True/False or an object indicating success.

        Raises:
            RuntimeError: If the container is not found.
        """
        ...


class DockerSandboxPlugin(Protocol):
    """Factory protocol for creating and connecting to Docker sandbox devices.

    Abstracts the docker-py SDK layer so that StandalonePaasService can
    operate without a direct SDK dependency.

    Implementations:
    - RealDockerSandboxPlugin: wraps the docker-py SDK
    - StubDockerSandboxPlugin: in-memory mock for unit tests

    Note:
        This is a SYNC factory (mirroring K8s/Arca SDK's sync API convention).
        Callers wrap calls in asyncio.to_thread() for async usage.
    """

    def create_device(
        self,
        template_id: int,
        template_uuid: str,
        tenant_name: str,
        container_name: str,
        image: str,
        container_port: int,
        envs: dict[str, str] | None = None,
        cpu_limit: str | None = None,
        memory_limit: str | None = None,
        image_pull_policy: str = "if_not_present",
        health_endpoint: str = "/health",
        health_timeout_seconds: int = 120,
    ) -> DockerSandbox:
        """Create a new Docker sandbox and wait for it to be ready.

        Full 5-step pipeline:
        1. Pull image (if not already present per image_pull_policy)
        2. Create container with port mapping and resource limits
        3. Start container
        4. Poll health endpoint until ready (up to health_timeout_seconds)
        5. Extract assigned host port

        Args:
            template_id: BaaS template ID.
            template_uuid: Device template UUID.
            tenant_name: Parent tenant name.
            container_name: Docker container name (unique within host).
            image: Container image for Bot runtime.
            container_port: The internal container port to expose.
            envs: Environment variables for the container.
            cpu_limit: CPU resource limit (e.g. "1.0" for 1 CPU core).
            memory_limit: Memory resource limit (e.g. "512m").
            image_pull_policy: Image pull strategy ("always", "if_not_present",
                "never").
            health_endpoint: HTTP health check path (e.g. "/health").
            health_timeout_seconds: Maximum wait for health check in seconds.

        Returns:
            A DockerSandbox ready for use.

        Raises:
            PaasError: On creation failure (platform unavailable, timeout, etc.).
        """
        ...

    def destroy_device(self, paas_device_id: str) -> bool:
        """Destroy a Docker sandbox by container ID.

        Idempotent: if the container is already gone (NotFound),
        returns True without error.

        Args:
            paas_device_id: The container ID (raw Docker container ID string).

        Returns:
            True if the container was destroyed or was already gone.
        """
        ...

    def connect_device(self, sandbox_id: str) -> DockerSandbox:
        """Connect to an existing Docker sandbox by container ID.

        Reconnects to an already-running container for operations like
        exec_command, get_info, restart, or destroy.

        Args:
            sandbox_id: The Docker container ID.

        Returns:
            A DockerSandbox for the existing container.

        Raises:
            RuntimeError: If the container is not found.
        """
        ...

    def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
    ) -> WsConnectionInfo:
        """Resolve WebSocket connection info for a sandbox device.

        Returns localhost URL since Docker containers expose ports
        on the host. No Docker daemon query needed.

        Args:
            paas_device_id: Container ID (for logging, not used in URL).
            port: Host port bound to the container.
            path: WebSocket path (e.g. /api/openclaw/ws).

        Returns:
            WsConnectionInfo with ws://127.0.0.1:{port}{path}.
        """
        ...

    def resolve_invoke_http_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
    ) -> HttpConnectionInfo:
        """Resolve HTTP connection info for invoking endpoints on a device.

        Returns localhost URL since Docker containers expose ports
        on the host. No Docker daemon query needed.

        Args:
            paas_device_id: Container ID (for logging, not used in URL).
            port: Host port bound to the container.
            path: HTTP path (e.g. /api/v1/health).

        Returns:
            HttpConnectionInfo with http://127.0.0.1:{port}{path}.
        """
        ...

    def invoke_http_in_device(
        self,
        paas_device_id: str,
        method: str,
        port: int,
        path: str,
        query_string: str | None = None,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> dict[str, Any]:
        """Invoke HTTP request directly on a device via localhost.

        Args:
            paas_device_id: Container ID (for logging, not used in URL).
            method: HTTP method (GET, POST, PUT, DELETE, etc.).
            port: Host port bound to the container.
            path: Request path (e.g. /api/v1/users).
            query_string: Optional query string.
            headers: HTTP headers dict.
            body: Raw request body bytes.

        Returns:
            Dict with keys: status_code (int), headers (dict), body (base64 str).
        """
        ...

    def close(self) -> None:
        """Release any resources held by the plugin (e.g., Docker client connections)."""
        ...
