"""Production Desktop device plugin — wraps ConnectionManager for mng daemon communication.

Provides:
- RealDesktopSandboxPlugin: factory that creates/connects desktop Docker containers
- RealDesktopSandbox: DesktopSandbox protocol wrapper for a created container
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from secbaas.community.spi.sandbox.desktop import DesktopSandbox, DesktopSandboxPlugin

if TYPE_CHECKING:
    from secbaas.community.api.bot_runtime import WsConnectionInfo
    from secbaas.community.api.paas import ConnectionManager


class RealDesktopSandbox(DesktopSandbox):
    """DesktopSandbox wrapper for a container managed via mng daemon WebSocket.

    All operations delegate to ConnectionManager.send_command() with the
    appropriate action payload. The connection_manager lifecycle is NOT
    owned by this device — it belongs to RealDesktopSandboxPlugin.
    """

    def __init__(
        self,
        connection_manager: ConnectionManager,
        container_id: str,
        machine_id: str,
        user_id: str,
        template_id: int = 0,
    ) -> None:
        """Initialize a RealDesktopSandbox wrapper.

        Args:
            connection_manager: Shared ConnectionManagerProtocol for WebSocket transport.
            container_id: Container ID assigned by the mng daemon.
            machine_id: Machine ID for routing commands.
            user_id: User ID.
            template_id: Template ID for target construction (default 0 for backward
                compatibility with ``connect_device()`` which doesn't pass template_id).
        """
        self._cm = connection_manager
        self._container_id = container_id
        self._machine_id = machine_id
        self._user_id = user_id
        self._template_id = template_id

    @property
    def container_id(self) -> str:
        return self._container_id

    @property
    def machine_id(self) -> str:
        return self._machine_id

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def is_running(self) -> bool:
        try:
            info = self.get_info()
            return info.get("status", "").upper() in ("RUNNING", "ACTIVE")
        except Exception:
            return False

    def get_info(self) -> Any:
        result = self._cm.send_command(
            self._machine_id,
            {
                "action": "get_device_info",
                "params": {"container_id": self._container_id},
            },
        )
        if result.get("status") == "error":
            raise RuntimeError(f"get_device_info failed: {result}")
        return result.get("data", result)

    def exec_shell(
        self,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> Any:
        result = self._cm.send_command(
            self._machine_id,
            {
                "action": "exec_shell",
                "params": {
                    "container_id": self._container_id,
                    "cmd": cmd,
                    "env": env or {},
                    "timeout_seconds": max(0, min(timeout_seconds, 30)),
                },
            },
        )
        if result.get("status") == "error":
            raise RuntimeError(f"exec_shell failed: {result}")
        return result.get("data", result)

    def http_proxy(
        self,
        method: str,
        port: int,
        path: str,
        headers: dict[str, str],
        body: bytes,
        query_string: str | None = None,
    ) -> dict[str, Any]:
        result = self._cm.send_command(
            self._machine_id,
            {
                "action": "invoke_http",
                "params": {
                    "container_id": self._container_id,
                    "method": method,
                    "port": port,
                    "path": path,
                    "headers": headers,
                    "body": body,
                    "query_string": query_string,
                },
            },
        )
        if result.get("status") == "error":
            raise RuntimeError(f"invoke_http failed: {result}")
        return result.get("data", result)

    def destroy(self) -> bool:
        result = self._cm.send_command(
            self._machine_id,
            {
                "action": "destroy_device",
                "params": {"container_id": self._container_id},
            },
        )
        if result.get("status") == "error":
            raise RuntimeError(f"destroy_device failed: {result}")
        return True

    def restart(self) -> bool:
        result = self._cm.send_command(
            self._machine_id,
            {
                "action": "restart",
                "params": {"container_id": self._container_id},
            },
        )
        if result.get("status") == "error":
            raise RuntimeError(f"restart failed: {result}")
        return True


class RealDesktopSandboxPlugin(DesktopSandboxPlugin):
    """Production Desktop device plugin using ConnectionManager for WebSocket transport.

    Delegates all device operations to the mng daemon over WebSocket via
    ConnectionManager.send_command(). Each command is an async request-response
    cycle correlated by request_id.

    Args:
        connection_manager: Shared ConnectionManager instance for WebSocket transport.
    """

    def __init__(
        self,
        connection_manager: ConnectionManager,
    ) -> None:
        self._cm = connection_manager

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
        template_id: int = 0,
    ) -> DesktopSandbox:
        result = self._cm.send_command(
            machine_id,
            {
                "action": "create_device",
                "params": {
                    "bot_id": bot_uuid,
                    "agent_code": agent_code,
                    "user_id": user_id,
                    "envs": envs or {},
                    "mount_path": mount_path,
                    "name": name,
                    "description": description,
                },
            },
        )
        if result.get("status") == "error":
            raise RuntimeError(f"create_device failed: {result}")

        data = result.get("data", result)
        container_id = data.get("container_id", "")
        return RealDesktopSandbox(
            connection_manager=self._cm,
            container_id=container_id,
            machine_id=machine_id,
            user_id=user_id,
            template_id=template_id,
        )

    def connect_device(
        self,
        container_id: str,
        machine_id: str,
        user_id: str,
    ) -> DesktopSandbox:
        """Reconnect to an existing device by container_id.

        Args:
            container_id: Container ID of the existing device.
            machine_id: Machine ID for routing commands.
            user_id: User ID.

        Returns:
            RealDesktopSandbox wrapping the reconnected device.

        Note:
            ``template_id`` is **not** recoverable from the mng daemon
            ``get_device_info`` response. Reconnected devices default to
            ``template_id=0``. Callers that need the correct template_id for
            relay target construction should store and inject it via
            :meth:`create_device` instead.
        """
        result = self._cm.send_command(
            machine_id,
            {
                "action": "get_device_info",
                "params": {"container_id": container_id},
            },
        )
        if result.get("status") == "error":
            raise RuntimeError(f"connect_device failed: {result}")

        return RealDesktopSandbox(
            connection_manager=self._cm,
            container_id=container_id,
            machine_id=machine_id,
            user_id=user_id,
        )

    def get_machine_info(self, machine_id: str) -> dict[str, Any]:
        result = self._cm.send_command(
            machine_id,
            {
                "action": "get_machine_info",
                "params": {"machine_id": machine_id},
            },
        )
        if result.get("status") == "error":
            raise RuntimeError(f"get_machine_info failed: {result}")
        return result.get("data", result)

    def get_machine_res_dirs(
        self, machine_id: str, dir: str = "~/Desktop"
    ) -> dict[str, Any]:
        result = self._cm.send_command(
            machine_id,
            {
                "action": "get_machine_res_dirs",
                "params": {"machine_id": machine_id, "dir": dir},
            },
        )
        if result.get("status") == "error":
            raise RuntimeError(f"get_machine_res_dirs failed: {result}")
        return result.get("data", result)

    def resolve_ws_conn_info(
        self,
        session_id: str,
        container_id: str,
        machine_id: str,
        user_id: str,
        port: int,
        path: str,
        template_id: int,
    ) -> WsConnectionInfo:
        """Construct relay WebSocket connection info for a LOCAL device.

        Pure communication construction (per D-02 mixed mode): generates
        token/target/ws_url/expires_at. Does NOT perform DB operations
        (insert_init), cross-instance routing (_route_command), or machine
        status queries — those are owned by the Service layer.

        Args:
            session_id: Pre-generated session_id from Service layer
                (Service handles insert_init before calling this method).
            container_id: Container ID from LocalDeviceId parse.
            machine_id: Machine ID from LocalDeviceId parse.
            user_id: User ID from LocalDeviceId parse.
            port: Target port on the device container.
            path: WebSocket path (interface compatibility; not used in
                the relay URL).
            template_id: Template ID for target construction.

        Returns:
            WsConnectionInfo with ws_url, token, target, and 120s expires_at.
        """
        # Deferred imports: _generate_proxypass_jwt and get_current_env reach
        # into the DI container at call time. Importing them at module level
        # would break test environments without a configured container.
        from secbaas.community.api.bot_runtime import WsConnectionInfo
        from secbaas.community.core.utils.env_utils import (
            get_current_env,  # noqa: PLC0415
        )
        from secbaas.community.plugins.sandbox.utils.arca_utils import (
            _generate_proxypass_jwt,  # noqa: PLC0415
        )

        # Step 1: Construct target string (D-02, D-07)
        target = (
            f"LOCAL_{container_id}--{machine_id}--{user_id}"
            f"@{template_id}:{port}:{session_id}"
        )

        # Step 2: Generate HS256 JWT token (D-07, ttl=120s for WS relay)
        token = _generate_proxypass_jwt(target, ttl=120)

        # Step 3: Construct ws_url with env-based host selection (D-06)
        from secbaas.community.config import ConfigPath, get_config, get_config_by_path

        env = get_current_env()
        host_map = {
            "dev": ConfigPath.AGENTCLAW_PROXY_HOST_DEV,
            "pre": ConfigPath.AGENTCLAW_PROXY_HOST_PRE,
            "prod": ConfigPath.AGENTCLAW_PROXY_HOST_PROD,
        }
        host = get_config_by_path(
            get_config(), host_map.get(env, ConfigPath.AGENTCLAW_PROXY_HOST_DEV)
        )
        ws_url = f"wss://{host}/wsrelay/{session_id}"

        # Step 4: Compute expires_at (D-08: 120s TTL)
        expires_at = datetime.now(UTC) + timedelta(seconds=120)

        return WsConnectionInfo(
            ws_url=ws_url,
            token=token,
            target=target,
            expires_at=expires_at,
        )

    def close(self) -> None:
        pass
