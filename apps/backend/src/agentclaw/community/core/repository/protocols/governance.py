"""Repository contracts owned by the ``governance`` domain.

Moved here by the ``core/repository`` consolidation. Every member is
``@abstractmethod``: an implementation that omits one fails at construction
naming the missing member, instead of raising ``AttributeError`` at the call
site. Domain imports are ``TYPE_CHECKING``-only — see the module docstring in
``core/repository/README.md`` for why that direction is load-bearing.
"""
from __future__ import annotations

from abc import abstractmethod
from datetime import datetime
from typing import Any, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.economy.governance.domain.notification import GovernanceNotification
    from agentclaw.community.core.economy.governance.domain.ticket import GovernanceTicket
    from agentclaw.community.core.economy.governance.domain.whitelist import WhitelistEntry


@runtime_checkable
class TaskRecordRepositoryProtocol(Protocol):
    """工单仓储核心契约 — 查、存、command 方法。

    14 个方法: 4 查 + 1 存 + 9 command。
    不含 scan 内部优化查询 / 管理诊断 / 批量 upsert / 测试播种。
    每个 command 方法表达一个业务操作, 调用方一看方法名就知道在做什么。
    """

    # ── 查 ──
    @abstractmethod
    def find_by_ticket_id(self, ticket_id: str) -> GovernanceTicket | None:
        """Find a ticket by its stable UUID."""
        ...

    @abstractmethod
    def find_active_ticket(self, active_worker: str) -> GovernanceTicket | None:
        """Find the active ticket for a worker (owner_id:bot_id)."""
        ...

    @abstractmethod
    def find_ticket_by_notification_id(self, notification_id: str) -> GovernanceTicket | None:
        """Find a ticket via its notify_log's notification_id."""
        ...

    @abstractmethod
    def find_latest_closed_by_worker(self, worker_id: str) -> GovernanceTicket | None:
        """Find most recently closed ticket for a worker (cooldown check)."""
        ...

    @abstractmethod
    def find_observed_ticket(self, worker_id: str) -> GovernanceTicket | None:
        """Find the active OBSERVED ticket for a worker (whitelist observation)."""
        ...

    @abstractmethod
    def find_latest_tickets_by_worker_keys(
        self, worker_keys: list[str],
    ) -> dict[str, GovernanceTicket]:
        """Batch: most-recent ticket per worker_key (any status/close_reason)."""
        ...

    # ── 存 ──
    @abstractmethod
    def add_ticket(self, row: Any) -> str:
        """Insert a new ticket row. Returns ticket_id."""
        ...

    # ── 列表查询 ──
    @abstractmethod
    def list_tickets_by_owner_and_statuses(
        self,
        owner_id: str,
        statuses: list[str],
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[GovernanceTicket]:
        """Owner's tickets in the given statuses, newest first, paged."""
        ...

    @abstractmethod
    def list_tickets_by_statuses(
        self,
        statuses: list[str],
        *,
        offset: int = 0,
        limit: int = 50,
        delivery_statuses: list[str] | None = None,
    ) -> list[GovernanceTicket]:
        """All tickets in given statuses (cross-owner), newest first, paged.

        评审场景按治理状态过滤工单,跨 owner;可选按投递状态过滤。
        """
        ...

    @abstractmethod
    def count_tickets_by_statuses(
        self,
        statuses: list[str],
        delivery_statuses: list[str] | None = None,
    ) -> int:
        """Count tickets in given statuses (cross-owner; list 配套统计)。"""
        ...

    @abstractmethod
    def list_remindable_tickets(self, now: datetime) -> list[GovernanceTicket]:
        """Find tickets eligible for reminder creation (§7.3.2)."""
        ...

    @abstractmethod
    def list_scheduled_due(self, now: datetime) -> list[GovernanceTicket]:
        """Find scheduled tickets where mute_until <= now."""
        ...

    @abstractmethod
    def list_auto_silence_eligible(
        self,
        *,
        min_consecutive_days: int,
    ) -> list[GovernanceTicket]:
        """Find open tickets eligible for auto-silence convergence (§7.2.6)."""
        ...

    @abstractmethod
    def count_active_open(self) -> int:
        """Count all active open tickets."""
        ...

    # ── 持久化原语(方案 A):repo 退化为 find/save/bulk,无状态机推进 ──
    # 状态机推进(close/accept/pause/review/advance/refresh 等)全部上移到
    # GovernanceLifecycleService(find→领域守卫→save),入口服务只调驱动服务。
    # 下方 9 个语义 command 的定义已在 Task 9 删除;双 grep 守卫(repo 无
    # 状态机推进入口 / 除豁免外无 governance_status= 字面量)锁住。

    @abstractmethod
    def save_ticket(self, ticket: GovernanceTicket) -> bool:
        """持久化(已改生命周期态的)领域模型回库(find→apply_to→commit)。

        调用方(驱动服务)在调用前已 invoke 模型守卫方法;本原语只写回
        生命周期字段(快照不动)。找不到返 False。
        """
        ...

    @abstractmethod
    def _save_ticket_with_snapshot(self, ticket: GovernanceTicket) -> bool:
        """持久化快照变更的模型(to_orm 全量写,含快照 + 生命周期)。

        专供驱动服务 refresh_snapshot 路径(快照刷新是唯一改快照的转移类
        操作,governance_status 不变)。
        """
        ...

    @abstractmethod
    def bulk_close_open(
        self,
        *,
        close_reason: str,
        closed_at: datetime,
        cooldown_until: datetime | None = None,
    ) -> int:
        """批量关闭所有活跃工单(全量原语,方案 A 唯一豁免)。

        SQL ``WHERE governance_status IN ('open','scheduled')`` 与
        ``active_worker IS NOT NULL`` 是状态合法性守卫。调用方收敛到驱动服务
        ``bulk_close_open``。返回受影响行数。
        """
        ...


@runtime_checkable
class NotifyLogRepositoryProtocol(Protocol):
    """通知仓储核心契约 — 查找、投递操作、批量关闭。

    不包含 admin delete（诊断用）和 legacy governance_status 查询。
    """

    # ── 单行查找 ──
    @abstractmethod
    def get_by_notification_id(self, notification_id: str) -> GovernanceNotification | None:
        """Fetch a notify_log by notification_id."""
        ...

    @abstractmethod
    def get_by_notification_id_and_owner(
        self, notification_id: str, owner_id: str,
    ) -> GovernanceNotification | None:
        """Fetch a notify_log by notification_id scoped to an owner."""
        ...

    # ── 列表查询 ──
    @abstractmethod
    def list_pending_for_cron(self) -> list[GovernanceNotification]:
        """All pending notifies for cron to pick up and send."""
        ...

    @abstractmethod
    def list_pending_by_worker(
        self, worker_id: str,
    ) -> list[GovernanceNotification]:
        """Pending notifies scoped to one worker (owner_id:bot_id) — for deliver_by_worker."""
        ...

    @abstractmethod
    def list_by_owner_and_statuses(
        self,
        owner_id: str,
        statuses: list[str],
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[GovernanceNotification]:
        """Owner's notify_log rows in the given statuses, newest first, paged."""
        ...

    @abstractmethod
    def has_pending_or_sending_reminder(self, ticket_id: str) -> bool:
        """Check if a ticket already has a pending/sending reminder (dedup)."""
        ...

    @abstractmethod
    def list_distinct_bot_owner(
        self, bot_ids: list[str],
    ) -> list[tuple[str, str]]:
        """Distinct (bot_id, owner_id) pairs for the given bot_ids."""
        ...

    @abstractmethod
    def count_pending(self) -> int:
        """Count open/muted notifications awaiting a response."""
        ...

    @abstractmethod
    def count_open_muted(self) -> int:
        """Count all open/muted records (regardless of response)."""
        ...

    # ── 投递操作 ──
    @abstractmethod
    def add_notification(self, row: Any) -> str:
        """Insert a new notify_log row. Returns notification_id."""
        ...

    @abstractmethod
    def save_notification(self, notification: GovernanceNotification) -> bool:
        """Persist a (mutated) domain notification back (领域往返写回原语)。

        调用方(driver)已 invoke 领域守卫 方法;本原语只写回投递态。
        Returns True if found+updated, False if not found.
        """
        ...

    @abstractmethod
    def claim_pending(self, notification_id: str, now: datetime) -> bool:
        """Atomic claim: UPDATE pending → sending."""
        ...

    @abstractmethod
    def mark_sent(
        self,
        notification_id: str,
        external_message_id: str | None,
        sent_at: datetime,
    ) -> bool:
        """Mark a sending notify as sent after successful delivery."""
        ...

    @abstractmethod
    def mark_send_failed(
        self,
        notification_id: str,
        error_msg: str,
        is_terminal: bool,
    ) -> bool:
        """Mark a sending notify as failed or revert to pending."""
        ...

    @abstractmethod
    def cancel_pending_by_ticket(self, ticket_id: str) -> int:
        """Cancel all pending notifies for a ticket. Returns rows cancelled."""
        ...

    # ── 批量关闭 (替代 Service 中 orm_session 直查) ──

    @abstractmethod
    def bulk_close_open_muted(
        self,
        *,
        close_reason: str,
        closed_at: datetime,
        cooldown_until: datetime | None = None,
        only_unresponded: bool = False,
    ) -> int:
        """批量关闭 open/muted 通知。返回受影响行数。"""
        ...

    @abstractmethod
    def bulk_cancel_by_bots(
        self,
        bot_ids: list[str],
        *,
        close_reason: str,
        closed_at: datetime,
        cooldown_until: datetime | None = None,
    ) -> int:
        """批量取消指定 bot 的 open/muted 通知。返回受影响行数。"""
        ...

    @abstractmethod
    def update_delivery_status(
        self,
        notification_id: str,
        *,
        status: Any,  # NotifyStatus
        external_id: str | None = None,
        error: str | None = None,
        at: datetime | None = None,
        increment_attempt: bool = False,
        channel: str | None = None,
    ) -> bool:
        """投递状态变更 — 合并 claim / mark_sent / mark_failed。

        claim:       update_delivery_status(id, status=SENDING, at=now, increment_attempt=True)
        mark_sent:   update_delivery_status(id, status=SENT, external_id=ext_id, at=now)
        mark_failed: update_delivery_status(id, status=FAILED, error=msg)
        """
        ...


@runtime_checkable
class AuditRepositoryProtocol(Protocol):
    """审计仓储 — append-only 写入 + 最新 scan 时间查询。"""

    @abstractmethod
    def add_audit(
        self,
        run_id: str,
        bot_id: str | None = None,
        owner_id: str | None = None,
        *,
        notification_id: str | None = None,
        actor_id: str | None = None,
        check_result: str | None = None,
        governance_decision: str | None = None,
        hit_dimensions: str | None = None,
        expected_token_saving: int | None = None,
        saving_ratio: float | None = None,
        action_taken: str | None = None,
        source: str = "daily_scan",
        error_msg: str | None = None,
        dry_run: int = 0,
    ) -> None:
        """Best-effort audit write (self-managed session).

        Failures are caught and logged; this method never raises.
        """
        ...


@runtime_checkable
class WhitelistRepositoryProtocol(Protocol):
    """白名单仓储 — 点查、单条增删、按 owner 分页。"""

    @abstractmethod
    def is_whitelisted(
        self,
        bot_id: str,
        owner_id: str,
        *,
        whitelist_type: str = "governance",
    ) -> bool:
        """点查: (bot_id, owner_id) 是否在有效白名单中。"""
        ...

    @abstractmethod
    def add(
        self,
        *,
        bot_id: str,
        owner_id: str,
        created_by: str,
        whitelist_type: str = "governance",
        source: str = "manual",
        reason: str = "",
        expires_at: datetime | None = None,
    ) -> WhitelistEntry:
        """添加单条白名单。幂等: UK 冲突时返回已有条目。"""
        ...

    @abstractmethod
    def remove(
        self,
        *,
        bot_id: str,
        owner_id: str,
        whitelist_type: str = "governance",
    ) -> bool:
        """删除单条白名单。True=已删除, False=不存在。"""
        ...

    @abstractmethod
    def list_by_owner(
        self,
        owner_id: str,
        *,
        whitelist_type: str = "governance",
        limit: int = 100,
        offset: int = 0,
    ) -> list[WhitelistEntry]:
        """按 owner_id 分页查询白名单条目。"""
        ...

    @abstractmethod
    def count_by_type(
        self,
        *,
        whitelist_type: str = "governance",
    ) -> int:
        """Count whitelist entries of a given type."""
        ...

    @abstractmethod
    def list_all(
        self,
        *,
        whitelist_type: str = "governance",
        owner_id: str | None = None,
        bot_id: str | None = None,
        include_expired: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[WhitelistEntry], int]:
        """全量分页查询白名单(可选 owner/bot 筛选 + 过期开关 + total)。"""
        ...
