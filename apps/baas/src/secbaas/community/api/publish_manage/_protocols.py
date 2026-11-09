"""
Publish Workflow Service Protocol.

Defines the SPI interface for publish operation lifecycle management.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from secbaas.community.api.device_manage import DeviceStatus

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
        status: DeviceStatus | None,
        provider_device_id: str | None,
        operator: str,
    ) -> UpdateDeviceStatusResult:
        """Repair a device record directly, bypassing normal state machine.

        WARNING: Admin operation for test/development use only. Does not
        trigger any publish workflow or PaaS operation; the target is
        resolved by ``device_uuid`` across every env and soft-deleted
        records are not eligible (not found).

        At least ONE of ``status`` / ``provider_device_id`` must be
        supplied (the router enforces this; direct callers that violate it
        get a ValueError). ``None`` means "leave the stored value
        unchanged", so a caller may correct only the provider binding
        without restating a concurrent writer's status.

        ``operator`` is the audit identity: it is stamped as the row's
        ``modifier`` in the same UPDATE as the value changes. Raises
        ``PublishNotFoundError`` when the ``device_uuid`` matches no live
        device, in which case no row is modified.
        """
        ...

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
        bot_id: int | None = None,
        bot_uuid: str | None = None,
        operator: str,
    ) -> RestoreBotResult:
        """Restore a soft-deleted bot record to live ACTIVE, atomically.

        Exactly ONE selector must be provided: ``bot_id`` restores that
        record directly; ``bot_uuid`` resolves the target first — the
        ``MAX(bot_id)`` over the uuid's record ids that still hold live
        (non-deleted) device relations — and then restores the resolved
        record the same way. ``operator`` is the audit identity and the
        ``modifier`` stamped on every touched row.

        Admin recovery tool that deliberately bypasses the state machine
        ("RELEASED is terminal"): revives the target record
        (``is_deleted=0``, ``status=ACTIVE``) and retires every OTHER live
        record sharing its ``bot_uuid`` (``status=RELEASED``,
        soft-deleted) in ONE transaction.

        Cross-env by design: no ``env`` parameter, no env validation, and
        no env/tenant filtering on the PK lookup, the uuid-based record
        listing, the live-relation resolution, or the sibling matching.
        Raises ``PublishNotFoundError`` when the selector matches no
        record (``bot_id`` path) or when the ``bot_uuid`` has no records /
        no live device relation (``bot_uuid`` path) — each with a distinct
        message; unique-key conflicts (concurrent restore or publish race)
        surface as ``PublishConflictError`` after the repository
        transaction rolled back — no partial state.
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
