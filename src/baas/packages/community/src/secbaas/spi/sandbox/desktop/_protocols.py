"""Desktop device plugin Protocol — contract for desktop/local Docker container lifecycle."""

from __future__ import annotations

from typing import Any, Protocol


class DesktopSandbox(Protocol):
    """A Docker container running on a desktop/local machine via mng daemon.

    Mirrors the mng daemon WebSocket API as a Protocol, allowing
    mock and Docker-based implementations for testing and local dev.
    """

    container_id: str
    machine_id: str
    user_id: str
    is_running: bool

    def get_info(self) -> Any:
        """Get container status from mng daemon.

        Returns:
            A LocalDeviceInfo-like object with at minimum:
            container_id, machine_id, user_id, status, platform, port.
        """
        ...

    def exec_shell(
        self,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> Any:
        """Execute a shell command in the container via mng daemon.

        Args:
            cmd: Command string to execute.
            env: Environment variables for the command context.
            timeout_seconds: Maximum execution time in seconds (max 30).

        Returns:
            A CommandResult-like object with at minimum:
            exit_code, stdout, stderr, execution_time_ms.
        """
        ...

    def http_proxy(
        self,
        method: str,
        port: int,
        path: str,
        headers: dict[str, str],
        body: bytes,
        query_string: str | None = None,
    ) -> dict[str, Any]:
        """Proxy an HTTP request to a service inside the container.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.).
            port: Target port on the container.
            path: Request path (e.g., /api/v1/users).
            headers: HTTP headers dict.
            body: Raw request body bytes.
            query_string: Optional query string.

        Returns:
            Dict with keys: status_code (int), headers (dict), body (base64 str).
        """
        ...

    def destroy(self) -> bool:
        """Destroy the container via mng daemon (idempotent).

        Returns:
            True if destroyed or already gone.
        """
        ...

    def restart(self) -> bool:
        """Restart the container via mng daemon.

        Returns:
            True if restart initiated successfully.
        """
        ...


class DesktopSandboxPlugin(Protocol):
    """Factory protocol for creating and connecting to desktop Docker containers.

    Abstracts the mng daemon WebSocket communication layer
    (ConnectionManager + InstanceRouter) so that LocalPaasService
    can operate without a direct dependency on WebSocket internals.

    Implementations:
    - RealDesktopSandboxPlugin: wraps ConnectionManager for production
    - StubDesktopSandboxPlugin: in-memory mock for unit tests
    - DockerDesktopSandboxPlugin: local Docker with mng-compatible API

    Devices use a three-part composite ID: container_id--machine_id--user_id.
    """

    def create_device(
        self,
        machine_id: str,
        bot_uuid: str,
        agent_code: str,
        user_id: str,
        envs: dict[str, str] | None = None,
        mount_path: str | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> DesktopSandbox:
        """Create a Docker container on a desktop machine via mng daemon.

        Args:
            machine_id: Target machine identifier.
            bot_uuid: Bot UUID that owns this device (mapped to bot_id in API).
            agent_code: Agent/daemon code to deploy.
            user_id: User identifier.
            envs: Environment variables for the container.
            mount_path: Host mount path for container storage.
            name: Optional container name.
            description: Optional container description.

        Returns:
            A DesktopSandbox representing the created container.

        Raises:
            RuntimeError: On creation failure, machine offline, or timeout.
        """
        ...

    def connect_device(
        self, container_id: str, machine_id: str, user_id: str
    ) -> DesktopSandbox:
        """Connect to an existing desktop device by its three-part ID.

        Args:
            container_id: Container identifier from mng daemon.
            machine_id: Machine identifier from LocalUserMachineRecord.
            user_id: User identifier.

        Returns:
            A DesktopSandbox for the existing container.

        Raises:
            RuntimeError: If the container is not found or unreachable.
        """
        ...

    def get_machine_info(self, machine_id: str) -> dict[str, Any]:
        """Get machine resource information from mng daemon.

        Args:
            machine_id: The machine identifier to query.

        Returns:
            Dict with cpu_cores, memory_gb, disk_gb, etc.

        Raises:
            RuntimeError: If machine not found or query fails.
        """
        ...

    def get_machine_res_dirs(
        self, machine_id: str, dir: str = "~/Desktop"
    ) -> dict[str, Any]:
        """Get directory tree structure on a machine via mng daemon.

        Args:
            machine_id: The machine identifier to query.
            dir: Directory path to query (default: ~/Desktop).

        Returns:
            Dict with directory tree: {name: str, children?: [...]}.
        """
        ...

    def resolve_ws_conn_info(
        self,
        session_id: str,
        container_id: str,
        machine_id: str,
        user_id: str,
        port: int,
        path: str,
        template_id: int,
    ) -> Any:
        """Construct relay WebSocket connection info (session_id -> ws_url + token + target + expires_at).

        This is a pure communication construction method with zero DB operations.
        The Service layer is responsible for relay session lifecycle (insert_init,
        send_command), while this Plugin method only constructs the connection
        parameters (ws_url, token, target, expires_at) used by the caller.

        This is distinct from the old DesktopSandbox.resolve_ws_conn_info, which
        was a full relay flow (insert_init + send_command). The Plugin-layer
        method is a thin communication constructor.

        Args:
            session_id: Relay session identifier (generated and managed by Service layer).
            container_id: Container identifier from the three-part LocalDeviceId.
            machine_id: Machine identifier from the three-part LocalDeviceId.
            user_id: User identifier from the three-part LocalDeviceId.
            port: Target port for the WebSocket connection.
            path: WebSocket path (retained for interface compatibility; current implementation does not use).
            template_id: Template ID used in the @{template_id} target suffix.

        Returns:
            A WsConnectionInfo-like object with ws_url, token, target, and expires_at.
        """
        ...

    def close(self) -> None:
        """Release any resources held by the plugin."""
        ...
