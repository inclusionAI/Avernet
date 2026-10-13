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
    RestoreBotResult,
    UpdateBotStatusResult,
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

    async def update_bot_status(
        self,
        *,
        bot_id: int,
        status: str,
        operator: str,
    ) -> UpdateBotStatusResult: ...

    async def restore_bot(
        self,
        *,
        bot_id: int,
        operator: str,
    ) -> RestoreBotResult:
        """Restore a soft-deleted bot record to live ACTIVE, atomically.

        Admin recovery tool that deliberately bypasses the state machine
        ("RELEASED is terminal"): revives the target record
        (``is_deleted=0``, ``status=ACTIVE``) and retires every OTHER live
        record sharing its ``bot_uuid`` (``status=RELEASED``,
        soft-deleted) in ONE transaction.

        Cross-env by design: no ``env`` parameter, no env validation, and
        no env/tenant filtering on the PK lookup or sibling matching.
        Raises ``PublishNotFoundError`` when the ``bot_id`` matches no
        record (live or soft-deleted); unique-key conflicts (concurrent
        restore or publish race) surface as ``PublishConflictError`` after
        the repository transaction rolled back — no partial state.
        """
        ...


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
