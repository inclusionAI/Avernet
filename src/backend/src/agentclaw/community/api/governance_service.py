"""Service API Protocols for economy/governance endpoints.

These Protocols decouple the HTTP adapter layer from concrete core service
classes, following the project's adapter→api→core layering rule (Rule 14).
Routers inject ``Injected(XProtocol)`` instead of importing service classes
from ``core/`` directly.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


if TYPE_CHECKING:
    from datetime import datetime

    from agentclaw.community.core.economy.governance.domain.enums import (
        GovernanceStatus,
    )
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


@runtime_checkable
class GovernanceLifecycleServiceProtocol(Protocol):
    """Protocol for the governance ticket state-machine driver service (Rule 14).

    This service is the **sole driver** of the ticket main state machine
    (open / scheduled / waiting_review / closed). Three entry channels
    (offline-batch / cron tick / ticket review) cease to mutate
    ``governance_status`` directly and instead express *intent* by calling
    these intent-named methods. The driver loads the domain model, invokes
    the white-list-guarded state-machine methods on ``GovernanceTicket``,
    persists via ``apply_to``/``to_orm``, and orchestrates side effects
    (cancel pending notifications / add whitelist / write audit).

    Boundary with the notify-delivery state machine
    (pending → sending → sent/failed/cancelled): the ticket machine is the
    *cause*; on ticket lifecycle change the driver *orchestrates* a
    one-way ``cancel_pending_by_ticket`` side effect on the notify side.
    The notify delivery machine itself is not converged here.

    Note: ``GovernanceLifecycleServiceProtocol`` is a *service* Protocol
    (lives in ``api/``), not a ``plugin_api`` ``Plugin`` subclass nor a
    repository Protocol — hence it is NOT scanned by
    ``test_protocol_contracts.py``; conformance is pinned by the contract
    suite + the grep guard (see ``test_governance_lifecycle.py``).
    """

    # ── Entry: offline-batch (record_process_service) ──────────────────

    def open_ticket(self, *, ticket: GovernanceTicket) -> str:
        """New ticket → OPEN (already OPEN on create): persist + audit.

        Args:
            ticket: ``GovernanceTicket`` domain model built by the caller
                (create path migrated from scalar kwargs to domain-model
                construction — see Task 5 done-when).

        Returns:
            The persisted ``ticket_id``.
        """
        ...

    def refresh_snapshot(self, ticket_id: str, **snapshot_fields: Any) -> bool:
        """Refresh an active ticket's mutable snapshot (non-state-transition).

        Owned by the driver so the snapshot write is unified with the
        ticket-lifecycle orchestration surface; ``governance_status`` is
        unchanged. Returns True if the ticket was found and updated.
        """
        ...

    def close_for_whitelist_hit(
        self, ticket_id: str, *, now: datetime,
    ) -> bool:
        """Whitelist hit → CLOSED(whitelist_filtered) + cancel pending + audit.

        Returns True if the ticket was found and closed, False if not found.
        """
        ...

    # ── Entry: cron tick (scan_service) ─────────────────────────────────

    def transition_schedule_due(
        self, ticket_id: str, *, now: datetime,
    ) -> bool:
        """SCHEDULED → WAITING_REVIEW + clear remind_at + cancel pending + audit.

        Returns True if the ticket was found and transitioned, False if not found.
        """
        ...

    def auto_silence_close(
        self, ticket_id: str, *, now: datetime,
    ) -> bool:
        """OPEN → CLOSED(auto_silenced_normal) on consecutive-normal convergence.

        Returns True if the ticket was found and closed, False if not found.
        """
        ...

    def advance_reminder(
        self,
        ticket_id: str,
        *,
        remind_at: datetime | None,
        is_reminder: bool = False,
        remind_count_delta: int = 0,
    ) -> bool:
        """Advance the reminder chain on a ticket (non-state-transition).

        Returns True if the ticket was found and updated, False if not found.
        """
        ...

    # ── Entry: ticket review (feedback_service / admin_service) ─────────

    def accept_feedback(
        self,
        ticket_id: str,
        *,
        user_feedback: str,
        feedback_at: datetime,
        feedback_source: str,
        target_status: GovernanceStatus,
        feedback_remark: str | None = None,
        repair_deadline: datetime | None = None,
        resume_at: datetime | None = None,
        review_reason: str | None = None,
        actor_id: str | None = None,
        feedback_payload: str | None = None,
    ) -> bool:
        """Accept user feedback → OPEN → WAITING_REVIEW/SCHEDULED + cancel pending
        + (whitelist) add whitelist + audit. Returns True if found and updated.
        """
        ...

    def pause_ticket(
        self, ticket_id: str, *, review_reason: str,
    ) -> bool:
        """OPEN/SCHEDULED → WAITING_REVIEW + clear remind_at. Returns True if found."""
        ...

    def resume_ticket(self, ticket_id: str) -> bool:
        """WAITING_REVIEW → OPEN.  # no caller yet — kept for symmetry."""
        ...

    def review_ticket(
        self,
        ticket_id: str,
        *,
        review_decision: str,
        reviewed_by: str,
        reviewed_at: datetime | None = None,
        review_remark: str | None = None,
        close_reason: str | None = None,
        cooldown_until: datetime | None = None,
    ) -> bool:
        """WAITING_REVIEW → CLOSED (three-branch: approve_close /
        approve_whitelist / reject_for_reopen) + clear remind_at +
        release active_worker + cancel pending + audit. Returns True if found.
        """
        ...

    def emergency_close(
        self, ticket_id: str, *, now: datetime,
    ) -> bool:
        """Any non-CLOSED → CLOSED(emergency_closed) + cancel pending.

        The caller (admin_service) owns the audit row (carries the reason +
        actor_id=admin_id) — the driver does not write a duplicate audit,
        matching the audit-ownership convention of its siblings.

        Returns True if the ticket was found and closed, False if not found
        (or already closed — idempotent no-op).
        """
        ...

    def bulk_close_open(
        self, *, close_reason: str, now: datetime,
    ) -> int:
        """Bulk emergency-close all open/scheduled tickets — joint orchestration:
        land ``task_record`` subject CLOSED (ticket machine) + cancel pending
        notifications (notify-delivery machine, one-way side effect). Ticket is
        cause, notify is effect. Returns the number of tickets closed.
        """
        ...

    def bulk_close_by_ticket_ids(
        self, ticket_ids: list[str], *, now: datetime,
    ) -> int:
        """Per-ticket emergency-close by ``ticket_id`` set — Task 8 uses this
        after cancel_pending / bulk_whitelist cancel notify delivery, to close
        the matching ``task_record`` subjects (口径对齐通知侧). Per-ticket
        find→guard→save→cancel chain (domain guard active); does NOT bare-use
        the full bulk_close_open (would over-close responded scheduled tickets).
        Idempotent. Returns the number of tickets actually closed.
        """
        ...

