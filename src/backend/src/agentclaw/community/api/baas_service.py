"""Service API Protocol for BaaS bot lifecycle + ws_info lookup.

The public surface adapters depend on. ``BaasService`` is a plain core
service (``core/service_bot/services/baas_service.py``) over a swappable
``HttpClient[baas]`` transport — not a plugin — so this contract lives in
the ``api/`` layer alongside the other Service API Protocols, and the
concrete service conforms to it structurally (verified by
``tests/architecture/test_service_api_conformance.py``).

Signatures are kept verbatim against the concrete ``BaasService``. The
dataclass / exception types (``BotWsConnectionInfoResponse``,
``HttpConnectionInfo`` …) stay defined in the core module; this Protocol
imports them so there's exactly one source of truth.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, runtime_checkable

from agentclaw.community.core.service_bot.services.baas_service import (
    BotWsConnectionInfoResponse,
    HttpConnectionInfo,
)
from agentclaw.community.core.service_bot.types import PublishStage

# Neutral device DTO — the ``update_device_outbound_rule`` signature type.
# Vendor-agnostic (kernel), so this Protocol loads in any environment.
from agentclaw.community.kernel.device_dto import OutBoundOperationRule


@runtime_checkable
class BaasServiceProtocol(Protocol):
    """BaaS-layer service: bot publish / restart / destroy / inspection."""

    def post_bots_api(
        self,
        path: str,
        payload: Dict[str, Any],
        action: str,
    ) -> Dict[str, Any]:
        """POST to a BaaS bot endpoint, validate code, return ``data``."""
        ...

    def create_bot(
        self,
        bot: Dict[str, Any],
        owner_id: str,
        request_id: str,
        device_count: int = 1,
        migration_path: Optional[str] = None,
        agent_pass_token: str = "",
        mount_path: Optional[str] = None,
        machine_id: Optional[str] = None,
        template_uuid: Optional[str] = None,
        stage: str = PublishStage.ONLINE.value,
        version: str = "1",
    ) -> Dict[str, Any]:
        """Create a BaaS bot. Returns ``{bot_uuid, publish_id}``."""
        ...

    def destroy_bot(
        self,
        bot_uuid: str,
        operator: str,
        request_id: str,
    ) -> Dict[str, Any]:
        """Destroy a BaaS bot. Returns the destroy workflow info."""
        ...

    def restart_bot(
        self,
        bot_uuid: str,
        operator: str,
        request_id: str,
        agent_code: str = "",
    ) -> Dict[str, Any]:
        """Restart a BaaS bot. Returns the restart workflow info."""
        ...

    def open_folder_bot(
        self,
        bot_uuid: str,
        folder_path: str | None = None,
    ) -> dict[str, Any]:
        """Open a bot's working folder on the device side."""
        ...

    def exec_command_on_bot(
        self,
        *,
        bot_uuid: str,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        """Execute a shell command inside a BaaS bot container."""
        ...

    def get_publish_progress(
        self,
        publish_id: int,
        include_devices: bool = False,
    ) -> Dict[str, Any]:
        """Query publish progress for a workflow id."""
        ...

    def get_bot(
        self,
        bot_uuid: str,
    ) -> Dict[str, Any]:
        """Query BaaS bot detail / status; return fallback released status on 404."""
        ...

    def get_ws_info(
        self,
        bind_id: int,
        port: int = 20003,
        path: str = "api/openclaw/ws",
        tenant: str = "",
        device_affinity: Optional[str] = None,
        device_uuid: Optional[str] = None,
    ) -> BotWsConnectionInfoResponse:
        """Resolve WebSocket / proxypass info for a device binding.

        ``device_uuid`` (optional) locks a specific instance in a multi-instance
        service bot; omitted → BaaS auto-selects an active instance.
        """
        ...

    def get_http_info(
        self,
        *,
        bind_id: int,
        port: int,
        path: str = "",
        tenant: Optional[str] = None,
        device_affinity: Optional[str] = None,
        device_uuid: Optional[str] = None,
        timeout: float = 5.0,
    ) -> HttpConnectionInfo:
        """Resolve HTTP container info for a device binding.

        Counterpart of :meth:`get_ws_info` for HTTP-direct callers (plugin
        FS/Sync/MCPSync/HealthProbe). plan-01; BaaS endpoint commit 9d4622c1e.

        ``device_uuid`` (optional) locks a specific instance in a multi-instance
        service bot — symmetric with :meth:`get_ws_info`. Propagated as the
        ``device_uuid`` query param of BaaS ``/http-info``.
        """
        ...

    def list_bots(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
    ) -> tuple[int, list[dict]]:
        """List bots for the tenant. Returns ``(total, items)``."""
        ...

    def get_bind_id(
        self,
        bot_id: str,
        owner_id: str,
        bot_type: str,
        publish_status: Optional[str] = None,
    ) -> Optional[int]:
        """Resolve the device binding id for a bot at a publish status."""
        ...

    def approve_publish(
        self,
        publish_id: int,
        operator: str,
        request_id: str,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Approve the current stage of a publish workflow."""
        ...

    def get_device_by_uuid(self, device_uuid: str) -> dict[str, Any]:
        """Query BaaS device info by device uuid."""
        ...

    def list_devices_by_bot_uuid(
        self, bot_uuid: str, tenant: str = ""
    ) -> list[dict[str, Any]]:
        """List BaaS devices under a logical bot uuid."""
        ...

    def restart_devices(
        self,
        bot_uuid: str,
        device_uuids: list[str],
        *,
        operator: str,
        tenant: str = "",
    ) -> dict[str, Any]:
        """Restart specific devices (POST /bots/{uuid}/update-devices); returns publish_id."""
        ...

    def upgrade_bot(
        self,
        bot_uuid: str,
        bot: Dict[str, Any],
        owner_id: str,
        request_id: str,
        migration_path: str,
        device_count: int = 1,
        stage: str = PublishStage.ONLINE.value,
        version: str = "1",
    ) -> Dict[str, Any]:
        """Upgrade an existing BaaS bot (POST /bots/{uuid}/update)."""
        ...

    def update_device_outbound_rule(
        self, paas_device_id: str, outbound_rule: OutBoundOperationRule
    ) -> bool:
        """Update PaaS device outbound header rules."""
        ...

    def update_teclaw_outbound_rule_by_bot_uuid(
        self,
        bot_uuid: str,
        *,
        agent_pass_token: str = "",
    ) -> list[dict[str, Any]]:
        """按 BaaS bot_uuid 更新 Teclaw PaaS 设备的出站规则。"""
        ...

    def get_bot_start_progress(
        self,
        bot_uuid: str,
        tenant: str = "",
        device_affinity: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Query bot device startup progress from BaaS.

        Calls GET /api/v1/bots/{bot_uuid}/start-progress.
        """
        ...


__all__ = ["BaasServiceProtocol"]
