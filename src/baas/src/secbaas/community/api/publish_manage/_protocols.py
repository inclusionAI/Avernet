"""
Publish Workflow Service Protocol.

Defines the SPI interface for publish operation lifecycle management.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ._enums import PublishType
from ._models import (
    BotPublishSummary,
    DeviceCallbackRequest,
    DrainResult,
    ForceSuccessResult,
    PublishConfig,
    PublishProgressResponse,
    PublishResponse,
    UpdateDeviceStatusResult,
)


@runtime_checkable
class PublishAdminService(Protocol):
    """Protocol for admin-level publish operations (test/development)."""

    async def force_success(
        self,
        *,
        publish_id: int,
        tenant: str,
        modifier: str,
    ) -> ForceSuccessResult: ...

    async def update_device_status(
        self,
        *,
        device_uuid: str,
        tenant: str,
        status: str,
        operator: str,
    ) -> UpdateDeviceStatusResult: ...


@runtime_checkable
class PublishService(Protocol):
    """Protocol for bot publish workflow orchestration."""

    async def create_publish(
        self,
        *,
        tenant: str,
        bot_id: int,
        publish_type: PublishType,
        operator: str,
        request_id: str,
        config: PublishConfig | None = None,
    ) -> PublishResponse: ...

    async def get_publish(
        self,
        *,
        tenant: str,
        publish_id: int,
    ) -> PublishResponse | None: ...

    async def get_publish_progress(
        self,
        *,
        tenant: str,
        publish_id: int,
        include_devices: bool = False,
    ) -> PublishProgressResponse | None: ...

    async def approve_stage(
        self,
        *,
        tenant: str,
        publish_id: int,
        operator: str,
        comment: str | None = None,
    ) -> PublishResponse: ...

    async def reject_publish(
        self,
        *,
        tenant: str,
        publish_id: int,
        operator: str,
        reason: str,
    ) -> PublishResponse: ...

    async def revoke_publish(
        self,
        *,
        tenant: str,
        publish_id: int,
        operator: str,
        reason: str | None = None,
    ) -> PublishResponse: ...

    async def retry_publish(
        self,
        *,
        tenant: str,
        publish_id: int,
        operator: str,
        request_id: str,
        config: PublishConfig | None = None,
    ) -> PublishResponse: ...

    async def execute_stage(
        self,
        *,
        tenant: str,
        publish_id: int,
        operator: str,
    ) -> DrainResult: ...

    async def complete_publish(
        self,
        *,
        tenant: str,
        publish_id: int,
        operator: str,
    ) -> PublishResponse: ...

    async def get_publish_bot_uuid(
        self,
        *,
        tenant: str,
        publish_id: int,
    ) -> str: ...

    async def list_publishes_by_bot_uuid(
        self,
        tenant: str,
        bot_uuid: str,
    ) -> list[BotPublishSummary]: ...

    async def handle_device_callback(
        self,
        callback: DeviceCallbackRequest,
    ) -> dict[str, str]: ...
