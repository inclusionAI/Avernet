"""Service API Protocols for economy/governance endpoints.

These Protocols decouple the HTTP adapter layer from concrete core service
classes, following the project's adapter→api→core layering rule (Rule 14).
Routers inject ``Injected(XProtocol)`` instead of importing service classes
from ``core/`` directly.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.economy.governance.domain.record import GovernanceRecord
    from agentclaw.community.core.economy.governance.domain.ticket import (
        GovernanceTicket,
    )
    from agentclaw.community.core.economy.governance.services.admin_service import (
        TicketActionOutcome,
    )


@runtime_checkable
class GovernanceAdminServiceProtocol(Protocol):
    """Protocol for governance admin operations (emergency brake + review)."""

    def is_paused(self) -> bool:
        ...

    def get_state(self) -> dict:
        ...

    def pause(self, reason: str, operator: str) -> None:
        ...

    def resume(self, reason: str, operator: str) -> None:
        ...

    def bulk_whitelist(
        self,
        bot_ids: list[str],
        reason: str,
        operator: str,
    ) -> dict:
        ...

    def cancel_pending(self, reason: str, operator: str) -> dict:
        ...

    def close_all_open(self, reason: str, operator: str) -> dict:
        ...

    def pause_ticket(
        self, ticket_id: str, admin_id: str, reason: str = "",
    ) -> TicketActionOutcome:
        ...

    def list_review_tickets(
        self,
        statuses: list[str] | None,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[GovernanceTicket], int]:
        """评审工单列表(跨 owner, 按治理状态过滤, 分页)。返回领域模型 + 总数。"""
        ...

    def get_review_ticket_detail(
        self, ticket_id: str,
    ) -> GovernanceTicket | None:
        """评审工单详情(单工单领域模型)。"""
        ...

    def review_ticket(
        self, ticket_id: str, action: str, admin_id: str, remark: str = "",
    ) -> TicketActionOutcome:
        ...

    def emergency_close(
        self, ticket_id: str, admin_id: str, reason: str = "",
    ) -> TicketActionOutcome:
        ...

    def delete_records(
        self,
        body: dict,
        operator: str,
    ) -> dict:
        ...

    def delete_whitelist_entry(
        self,
        *,
        bot_id: str,
        owner_id: str,
        reason: str,
        operator: str,
    ) -> dict:
        ...

    def deliver_pending(
        self,
        *,
        scan_svc: Any,
        override_recipient: str,
        dry_run: bool,
        max_send: int,
        channel: str,
        skip_scan: bool,
        scan_dry_run: bool,
    ) -> dict:
        ...


@runtime_checkable
class GovernanceWhitelistServiceProtocol(Protocol):
    """Protocol for governance whitelist operations — single-point add/delete.

    Decouples routers and other services from the concrete
    ``GovernanceWhitelistService`` — following Rule 14 layering.
    """

    def bulk_whitelist(
        self,
        bot_ids: list[str],
        reason: str,
        operator: str,
    ) -> dict:
        ...

    def delete_whitelist_entry(
        self,
        *,
        bot_id: str,
        owner_id: str,
        reason: str,
        operator: str,
    ) -> dict:
        ...

    def add(
        self,
        *,
        bot_id: str,
        owner_id: str,
        created_by: str,
        whitelist_type: str = "governance",
        source: str = "manual",
        reason: str = "",
    ) -> Any:
        ...

    def list_by_owner(
        self,
        owner_id: str,
        *,
        whitelist_type: str = "governance",
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]:
        ...

    def is_whitelisted(
        self,
        bot_id: str,
        owner_id: str,
        *,
        whitelist_type: str = "governance",
    ) -> bool:
        ...

    def count_by_type(self, *, whitelist_type: str = "governance") -> int:
        ...


@runtime_checkable
class GovernanceFeedbackServiceProtocol(Protocol):
    """Protocol for user-facing governance feedback interactions."""

    def list_pending(
        self, owner_id: str, *, limit: int, offset: int,
    ) -> list[dict]:
        ...

    def list_history(
        self, owner_id: str, *, limit: int, offset: int,
    ) -> list[dict]:
        ...

    def get_notification(
        self, notification_id: str, owner_id: str,
    ) -> dict | None:
        ...

    def resolve(
        self,
        notification_id: str,
        response: str,
        user_id: str,
        remark: str | None,
        source: str,
        repair_deadline: Any | None = None,
        feedback_payload: dict | None = None,
    ) -> Any:
        ...


@runtime_checkable
class GovernanceBotServiceProtocol(Protocol):
    """Protocol for governance cron tick orchestrator (§7.3)."""

    def process_cron_tick(
        self,
        *,
        dry_run: bool | None = None,
    ) -> Any:
        ...


@runtime_checkable
class GovernanceWhitelistProtocol(Protocol):
    """Protocol for governance whitelist operations (single-point add + list_by_owner).

    Decouples the public router from the concrete
    ``GovernanceWhitelistRepository`` in ``core/``, following Rule 14.
    """

    def add(
        self,
        *,
        bot_id: str,
        owner_id: str,
        created_by: str,
        whitelist_type: str = "governance",
        source: str = "manual",
        reason: str = "",
        expires_at: Any | None = None,
    ) -> Any:
        ...

    def list_by_owner(
        self,
        owner_id: str,
        *,
        whitelist_type: str = "governance",
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]:
        ...

    def is_whitelisted(
        self,
        bot_id: str,
        owner_id: str,
        *,
        whitelist_type: str = "governance",
    ) -> bool:
        ...

    def count_by_type(
        self,
        *,
        whitelist_type: str = "governance",
    ) -> int:
        ...


@runtime_checkable
class GovernanceRecordProcessProtocol(Protocol):
    """Protocol for offline-batch record processing (§7.2).

    ``process_record()`` is an internal implementation detail used by
    ``process_offline_batch()``; it is not exposed via this Protocol.
    """

    def process_offline_batch(
        self,
        records: list[GovernanceRecord],
        *,
        batch_id: str,
        dt_version: str,
        total_count: int,
        dry_run: bool = False,
    ) -> Any:
        ...
