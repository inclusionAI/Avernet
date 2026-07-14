"""
Bot Management Service Protocol.

Defines the SPI interface for bot lifecycle management operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from secbaas.community.api.device_manage import DeviceListResponse
from secbaas.community.api.publish_manage import RestartScope

if TYPE_CHECKING:
    from ._models import BotClusterCreate, BotRecord, BotStatus

from ._models import (
    BotConfig,
    BotDeviceStatusResponse,
    BotListResponse,
    BotResponse,
    CreateBotResponse,
    DestroyBotResponse,
    RestartBotResponse,
    ScaleBotResponse,
    StopBotResponse,
    UpdateBotResponse,
    UpdateDevicesResponse,
)


@runtime_checkable
class BotCrudService(Protocol):
    """Protocol for bot CRUD operations.

    Low-level bot record and device management:
    creating Bot records, managing Bot-Device relationships,
    and calculating Bot status from device states.
    """

    async def create_bot(
        self,
        tenant: str,
        data: BotClusterCreate,
    ) -> BotResponse:
        """Create Bot with associated Device cluster."""
        ...

    async def get_bot(
        self,
        tenant: str,
        bot_id: int,
        include_status: bool = True,
    ) -> BotResponse | None:
        """Get Bot by ID with optional calculated status."""
        ...

    async def list_bots(
        self,
        tenant: str,
        status: BotStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> BotListResponse:
        """List Bots with pagination and status filtering."""
        ...

    def _calculate_bot_status(self, bot: BotRecord, tenant: str) -> BotStatus:
        """Calculate Bot status from its associated Devices."""
        ...


@runtime_checkable
class BotManageService(Protocol):
    """Protocol for bot lifecycle management service."""

    async def create_bot(
        self,
        tenant: str,
        name: str,
        template_uuid: str,
        device_count: int,
        operator: str,
        request_id: str,
        description: str | None = None,
        config: BotConfig | None = None,
    ) -> CreateBotResponse:
        """Create Bot through publish workflow."""
        ...

    async def destroy_bot(
        self,
        tenant: str,
        bot_uuid: str,
        operator: str,
        request_id: str,
        auto_approve_publish: bool = False,
    ) -> DestroyBotResponse | None:
        """Destroy Bot through publish workflow."""
        ...

    async def stop_bot(
        self,
        tenant: str,
        bot_uuid: str,
        operator: str,
        request_id: str,
        auto_approve_publish: bool = False,
    ) -> StopBotResponse:
        """Stop Bot through publish workflow.

        Creates a STOP publish that destroys provider devices and marks
        bot as STOPPED. Bot and device records are preserved for restart.
        """
        ...

    async def get_bot(
        self,
        tenant: str,
        bot_uuid: str,
        health_check: bool = False,
        engine_type: str | None = None,
    ) -> BotResponse | None:
        """Get Bot details with current status.

        Args:
            tenant: Tenant name for isolation
            bot_uuid: Bot UUID to retrieve
            health_check: When True, perform real-time device health checks
            engine_type: Optional engine override for health check strategy resolution
        """
        ...

    async def list_bots(
        self,
        tenant: str,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> BotListResponse:
        """List Bots with pagination."""
        ...

    async def scale_bot(
        self,
        tenant: str,
        bot_uuid: str,
        target_count: int,
        operator: str,
        request_id: str,
        auto_approve_publish: bool = False,
    ) -> ScaleBotResponse:
        """Scale Bot to target device count (SCALE_UP or SCALE_DOWN)."""
        ...

    async def update_bot(
        self,
        tenant: str,
        bot_uuid: str,
        operator: str,
        bot_name: str | None = None,
        bot_desc: str | None = None,
        bot_config: BotConfig | None = None,
        request_id: str | None = None,
    ) -> UpdateBotResponse:
        """Update Bot metadata and config."""
        ...

    async def restart_bot(
        self,
        tenant: str,
        bot_uuid: str,
        operator: str,
        request_id: str,
        scope: RestartScope = RestartScope.ALL,
        auto_approve_publish: bool = False,
    ) -> RestartBotResponse:
        """Create RESTART publish for Bot device recycling."""
        ...

    async def get_bot_with_devices(
        self,
        tenant: str,
        bot_id: int,
    ) -> BotResponse | None:
        """Get bot details with associated device list by internal bot_id."""
        ...

    async def list_bots_with_devices(
        self,
        tenant: str,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> BotListResponse:
        """List bots with associated device lists."""
        ...

    async def list_devices_by_bot_uuid(
        self,
        tenant: str,
        bot_uuid: str,
    ) -> list[DeviceListResponse]:
        """List devices for all bot records matching a bot_uuid."""
        ...

    async def list_devices_by_bot_id(
        self,
        tenant: str,
        bot_id: int,
        page: int = 1,
        page_size: int = 20,
    ) -> DeviceListResponse:
        """List devices for a bot by unique bot_id with pagination."""
        ...

    async def update_devices(
        self,
        tenant: str,
        bot_uuid: str,
        operator: str,
        request_id: str | None,
        device_uuids: list[str],
        auto_approve_publish: bool = False,
        config: BotConfig | None = None,
    ) -> UpdateDevicesResponse:
        """Create UPDATE_DEVICE publish for targeted device update."""
        ...

    async def get_bot_device_status(
        self,
        tenant: str,
        bot_uuid: str,
    ) -> BotDeviceStatusResponse:
        """Get aggregate device status for a bot.

        Queries all devices attached to the bot and computes whether all
        are online, all offline, or a partial mix.

        Args:
            tenant: Tenant name for isolation
            bot_uuid: Bot UUID (business UUID, not internal id)

        Returns:
            BotDeviceStatusResponse with aggregate status and device counts

        Raises:
            BotNotFoundError: If no bot record is found for the UUID
        """
        ...
