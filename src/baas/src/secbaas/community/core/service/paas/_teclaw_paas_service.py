"""TeClaw platform PaaS adapter.

Thin delegation layer that converts domain-level PaaS service calls
into TeClawBotPlugin primitive operations (per D-03: domain -> primitive
conversion pattern). All HTTP/aiohttp logic lives in RealTeClawBotPlugin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from secbaas.community.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.community.api.device_manage import (
    ErrorCode,
    PaasError,
    TeClawCreateConfig,
    TeClawCreationResult,
    TeClawCredentials,
    TeClawDeviceInfo,
)
from secbaas.community.api.tenant_manage import TenantType
from secbaas.community.logger import get_logger
from secbaas.community.spi.bot.teclaw import TeClawBotPlugin

from ._paas_service import PaasService

if TYPE_CHECKING:
    from secbaas.community.api.device_manage import OutBoundOperationRule
    from secbaas.community.api.health_check.bot import TTLInfo


class TeClawPaasService(PaasService):
    """TeClaw platform PaaS adapter — thin delegation layer.

    Delegates all core operations to a TeClawBotPlugin instance.
    Domain types (TeClawCreateConfig, TeClawCreationResult, etc.) are
    converted to/from plugin-level dataclass primitives (_BotCreateResult,
    _BotInfo, etc.) at the boundary per D-03.
    """

    def __init__(self, plugin: TeClawBotPlugin, credentials: TeClawCredentials):
        if plugin is None:
            raise ValueError("plugin is required")
        if credentials is None:
            raise ValueError("credentials is required")
        self._plugin = plugin
        self._credentials = credentials
        self._logger = get_logger("core-service")

    # ------------------------------------------------------------------
    # ABC metadata methods
    # ------------------------------------------------------------------

    async def get_credentials(self) -> TeClawCredentials:
        return self._credentials

    async def get_platform_type(self) -> TenantType:
        return TenantType.TECLAW

    # ------------------------------------------------------------------
    # Core methods — delegate to plugin with domain<->primitive conversion
    # ------------------------------------------------------------------

    async def create_device(self, config: TeClawCreateConfig) -> TeClawCreationResult:
        """Create a TeClaw bot via plugin.create_bot.

        Args:
            config: TeClawCreateConfig with teclaw_bot_config for bot setup.

        Returns:
            TeClawCreationResult with teclaw_bot_id from the plugin response.
        """
        result = await self._plugin.create_bot(
            bot_config=config.teclaw_bot_config or {},
        )
        self._logger.info(
            "TeClaw device created: teclaw_bot_id=%s status=%s",
            result.teclaw_bot_id,
            result.status,
        )
        return TeClawCreationResult(
            teclaw_bot_id=result.teclaw_bot_id,
            platform="teclaw",
            teclaw_bot_config=result.teclaw_bot_config,
            status=result.status,
        )

    async def destroy_device(self, paas_device_id: str) -> bool:
        """Destroy a TeClaw bot via plugin.destroy_bot.

        Args:
            paas_device_id: The teclaw_bot_id to destroy
                (facade strips @template_id suffix).

        Returns:
            True when the plugin returns status="DELETED".
        """
        result = await self._plugin.destroy_bot(bot_id=paas_device_id)
        deleted = result.status == "DELETED"
        self._logger.info(
            "TeClaw device destroyed: teclaw_bot_id=%s status=%s deleted=%s",
            paas_device_id,
            result.status,
            deleted,
        )
        return deleted

    async def update_device(  # type: ignore[override]  # narrows config from DeviceCreateConfig to TeClawCreateConfig per D-03
        self, paas_device_id: str, config: TeClawCreateConfig | None = None
    ) -> bool:
        """Update a TeClaw bot config via plugin.update_bot.

        Args:
            paas_device_id: The teclaw_bot_id to update.
            config: TeClawCreateConfig with teclaw_bot_config.

        Returns:
            True on success.

        Raises:
            PaasError: If config is not TeClawCreateConfig (CONFIG_INVALID).
        """
        if not isinstance(config, TeClawCreateConfig):
            raise PaasError(
                ErrorCode.CONFIG_INVALID,
                "TeClaw update_device requires TeClawCreateConfig",
            )
        await self._plugin.update_bot(
            bot_id=paas_device_id,
            bot_config=config.teclaw_bot_config or {},
        )
        self._logger.info(
            "TeClaw device updated: teclaw_bot_id=%s",
            paas_device_id,
        )
        return True

    async def restart_device(self, paas_device_id: str) -> bool:
        """Restart a TeClaw bot via plugin.restart_bot.

        The plugin handles the restart semantics internally (may proxy
        to update_bot with cached config).

        Args:
            paas_device_id: The teclaw_bot_id to restart.

        Returns:
            True when the plugin returns status="ONLINE".
        """
        result = await self._plugin.restart_bot(bot_id=paas_device_id)
        return result.status == "ONLINE"

    async def get_device_info(self, paas_device_id: str) -> TeClawDeviceInfo:
        """Get TeClaw bot info via plugin.get_bot.

        Args:
            paas_device_id: The teclaw_bot_id to query.

        Returns:
            TeClawDeviceInfo with platform, teclaw_bot_id, status, and config.
        """
        result = await self._plugin.get_bot(bot_id=paas_device_id)
        return TeClawDeviceInfo(
            platform="teclaw",
            teclaw_bot_id=result.teclaw_bot_id,
            status=result.status,
            online_teclaw_bot_config=result.teclaw_bot_config,
            gray_teclaw_bot_config=None,
            gray_strategy=None,
        )

    async def resolve_invoke_http_info(
        self, paas_device_id: str, port: int, path: str | None = None
    ) -> HttpConnectionInfo:
        """Resolve HTTP invoke URL via plugin.resolve_http_conn_info.

        Args:
            paas_device_id: The teclaw_bot_id for the target device.
            port: Target port on the device.
            path: HTTP path (defaults to "/" if None).

        Returns:
            HttpConnectionInfo with http_url and token.
        """
        resolved_path = path if path is not None else "/"
        return await self._plugin.resolve_http_conn_info(
            bot_id=paas_device_id,
            port=port,
            path=resolved_path,
            template_id=self._credentials.template_id,
        )

    async def resolve_ws_conn_info(
        self, paas_device_id: str, port: int, path: str, ws_conn_mode: str | None = None
    ) -> WsConnectionInfo:
        """Resolve WebSocket URL via plugin.resolve_ws_conn_info.

        Args:
            paas_device_id: The teclaw_bot_id for the target device.
            port: Target port on the device.
            path: WebSocket path (e.g., "/api/openclaw/ws").

        Returns:
            WsConnectionInfo with ws_url, token, target, and expires_at.
        """
        return await self._plugin.resolve_ws_conn_info(
            bot_id=paas_device_id,
            port=port,
            path=path,
            template_id=self._credentials.template_id,
        )

    async def close(self) -> None:
        """Release resources via plugin.close()."""
        await self._plugin.close()

    # ------------------------------------------------------------------
    # Unsupported operations (raise NotImplementedError)
    # ------------------------------------------------------------------

    async def execute_command(
        self,
        paas_device_id: str,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> Any:
        raise NotImplementedError("TeClaw platform does not support execute_command")

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
        raise NotImplementedError("TeClaw platform does not support HTTP invocation")

    async def update_outbound_operation_rule(
        self,
        paas_device_id: str,
        outbound_operation_rule: OutBoundOperationRule,
    ) -> bool:
        """Update outbound operation rule via plugin.update_outbound_rule.

        Converts Arca SDK OutBoundOperationRule -> dict at the service boundary
        per D-03 (domain->primitive conversion pattern), then delegates to the
        plugin for HTTP execution.
        """
        rules_list: list[dict[str, Any]] = []
        if (
            outbound_operation_rule is not None
            and outbound_operation_rule.header_operation_rules is not None
        ):
            rules_list = [
                r.model_dump() for r in outbound_operation_rule.header_operation_rules
            ]
        rules_dict: dict[str, Any] = {"header_operation_rules": rules_list}
        result = await self._plugin.update_outbound_rule(paas_device_id, rules_dict)
        self._logger.info(
            "TeClaw outbound rule updated: teclaw_bot_id=%s result=%s",
            paas_device_id,
            result,
        )
        return result

    async def update_device_ttl(self, paas_device_id: str) -> TTLInfo:
        raise NotImplementedError("TeClaw platform does not support TTL renewal")

    async def open_folder(
        self, paas_device_id: str, folder_path: str | None = None
    ) -> bool:
        raise NotImplementedError("TeClaw platform does not support open_folder")

    async def list_instances(self, params: dict[str, Any]) -> list[Any]:
        raise NotImplementedError("TeClaw does not support instance listing")

    async def pull_file_from_url(
        self,
        paas_device_id: str,
        source_url: str,
        device_path: str,
        timeout_seconds: int = 300,
    ) -> None:
        """Not supported: TeClaw platform does not support file transfer.

        Args:
            paas_device_id: TeClaw device ID.
            source_url: URL to download from.
            device_path: Destination path on device.
            timeout_seconds: Maximum download time (unused).

        Raises:
            NotImplementedError: Always — file transfer not supported on TeClaw.
        """
        raise NotImplementedError("File transfer not supported on TeClaw platform")

    async def push_file_to_url(
        self,
        paas_device_id: str,
        device_path: str,
        target_url: str,
        timeout_seconds: int = 300,
    ) -> None:
        """Not supported: TeClaw platform does not support file transfer.

        Args:
            paas_device_id: TeClaw device ID.
            device_path: Source path on device.
            target_url: URL to upload to.
            timeout_seconds: Maximum upload time (unused).

        Raises:
            NotImplementedError: Always — file transfer not supported on TeClaw.
        """
        raise NotImplementedError("File transfer not supported on TeClaw platform")
