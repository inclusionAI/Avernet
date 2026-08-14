"""[内核] governance service 间契约 Protocol —— core 自给自足的抽象接口面。

为什么放在 core(而不是 api/):
  这些 Protocol 的**实现**在 core(``lifecycle_service``/``admin_service``/
  ``whitelist_service`` 等),**主要消费者**也是 core 内部其他 service
  (scan/record_process/feedback 等)。把它们的定义放在 core 自己家里,
  service↔service 解耦走同层 Protocol,不跨层依赖外圈 ``api/``,消除
  ``core → api`` 架构违规(见 tests/community/architecture/
  test_architecture_compliance.py::test_core_layer_does_not_import_api)。

  对外 HTTP 契约层 ``api/governance_service.py`` 的同名 Protocol 从本文件
  **re-export**,供 adapters/http router 注入(router 在 api 外圈,import core
  Protocol 合法,方向通)。两处共享同一份定义,零漂移。

  这是"用接口而非具体类"(DIP)的实践,但接口住在被依赖方(core)那一层,
  而非更外圈的 api —— 与项目单向分层 ``api → core → plugin_api ← plugins``
  一致。

Rule 14: DI binds Protocol → Concrete; Service imports Protocol only。
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.economy.governance.domain.enums import (
        AdminCloseConclusion,
        GovernanceStatus,
    )
    from agentclaw.community.core.economy.governance.domain.notification import (
        GovernanceNotification,
    )
    from agentclaw.community.core.economy.governance.domain.ticket import (
        GovernanceTicket,
    )
    from agentclaw.community.core.economy.governance.services.admin_service import (
        BulkOperationResult,
        TicketActionOutcome,
    )
    from agentclaw.community.core.economy.governance.services.delivery_service import (
        SendResult,
    )


@runtime_checkable
class GovernanceAdminServiceProtocol(Protocol):
    """Protocol for governance admin operations (brake + review)."""

    def is_paused(self) -> bool:
        ...

    def get_state(self) -> dict:
        ...

    def pause(self, reason: str, operator: str) -> None:
        ...

    def resume(self, reason: str, operator: str) -> None:
        ...

    def pause_ticket(
        self, ticket_id: str, admin_id: str, reason: str = "",
    ) -> TicketActionOutcome:
        ...

    def delete_records(
        self,
        body: dict,
        operator: str,
    ) -> dict:
        ...

    def force_renew_with_record(
        self, record: Any, operator: str,
    ) -> dict:
        """强制换新:用 record 关老(stale_replaced)+ 建新 first_send。

        无视 gmt_create 7天 + dt_version guard。Returns {ticket_id, notification_id}。
        """
        ...

    def write_brake_skip_audit(self, *, run_id: str, reason: str) -> None:
        """记录"自动定时 tick 因制动被跳过"的 best-effort 审计。

        Args:
            run_id: 调度层当次 run 标识。
            reason: 跳过原因(制动生效)。
        """
        ...


@runtime_checkable
class GovernanceDeliveryServiceProtocol(Protocol):
    """Protocol for governance delivery orchestration — 投递编排域。

    从 :class:`GovernanceAdminServiceProtocol` 按职责边界抽出的投递域:
    把 pending/scheduled 通知按 channel 规则实际投递 + 回写投递状态 + 写审计。
    admin_router 的 deliver/remind/scan-and-deliver 端点注入本 protocol
    (对齐 Rule 14:DI 绑 Protocol → Concrete,router import Protocol only)。

    deliver_pending / deliver_by_worker / create_and_send_reminder 由
    :class:`GovernanceDeliveryService` 实现(Task 2/3 从 admin_service 迁入,
    签名/行为零变化)。
    """

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

    def deliver_by_worker(
        self,
        *,
        worker_id: str,
        override_recipient: str | None = None,
        dry_run: bool = True,
        channel: str = "auto",
    ) -> dict:
        """按 worker_id 精准投递该工单 pending 通知(不重跑状态机)。"""
        ...

    def create_and_send_reminder(
        self, worker_id: str, operator: str,
    ) -> dict:
        """手动补发 reminder:按 worker_id 找 active 工单 → 创建+发送 reminder 通知。

        跳过 remind_at 等待(立即 create+send)。
        Returns {notification_id, sent}。无 active → raise ValueError。
        """
        ...

    def send_notification(
        self,
        notify: GovernanceNotification,
        *,
        override_recipient: str | None = None,
    ) -> SendResult:
        """投递域唯一单条发送出口:按 ``notify.channel`` 选 tc_card/markdown
        渲染并实际发送,tc_card 构建失败降级 markdown,title 按 notify_type
        区分。返回 :class:`SendResult`。

        三条投递路径(create_and_send_reminder / _run_delivery / scan cron)
        共用本出口,保证手动补发与首发/cron reminder 字段口径一致
        (tickets-remind-content-divergence SDD)。
        """
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

    def list_all(
        self,
        *,
        whitelist_type: str = "governance",
        owner_id: str | None = None,
        bot_id: str | None = None,
        include_expired: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Any], int]:
        """全量分页查询白名单(可选 owner/bot 筛选 + 过期开关 + total)。"""
        ...

    def list_all_with_ticket_meta(
        self,
        *,
        whitelist_type: str = "governance",
        owner_id: str | None = None,
        bot_id: str | None = None,
        include_expired: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """白单 + 最近工单维度字段叠加(bot_name/owner_name/token_baseline 等)。

        白单为主表,工单维度取每 worker 最近一条工单快照(见
        :meth:`TaskRecordRepositoryProtocol.find_latest_tickets_by_worker_keys`);
        无对应工单的白单叠加字段为 None,条目保留。
        """
        ...


@runtime_checkable
class GovernanceAuditReadServiceProtocol(Protocol):
    """Protocol for governance audit read-side (worker-scoped history query).

    治理审计表 ``ac_governance_audit`` 无 ``worker_id`` 列;"worker" 在治理领域
    = ``owner_id:bot_id`` 复合标识(同 ``ac_governance_notify_log.worker_id``)。
    本协议把"按 worker 查全部审计"翻译为按 ``owner_id``/``bot_id`` 定位审计行。
    只读、无副作用、不写审计。
    """

    def list_audit_by_worker(
        self,
        *,
        worker_id: str | None = None,
        owner_id: str | None = None,
        bot_id: str | None = None,
        action: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """按 worker 维度查治理审计,返回 ``(审计条目 dict 列表, total)``。

        ``worker_id``(冒号分隔 ``owner:bot``)优先解析并覆盖独立 ``owner_id``/
        ``bot_id``;``action`` 可选按 ``action_taken`` 过滤。解析后 owner/bot/action
        皆空抛 ``ValueError``,路由侧 400。条目按 ``gmt_create`` 倒序。
        """
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

    Note: 本 Protocol 定义在 core 自家(``services/service_protocols.py``),
    ``api/governance_service.py`` re-export 给 router 注入用;service 间消费
    也直接 import 本文件,不跨层依赖 ``api/``。
    conformance 由契约套件 + grep 守卫(见 ``test_governance_lifecycle.py``)钉住。
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

    def open_observed_ticket(self, *, ticket: GovernanceTicket) -> str:
        """New ticket → OBSERVED(白名单观察):建观察单瘦路径。

        白名单 bot offline-batch 命中、无活跃单无现存观察单时新建 OBSERVED 工单
        承载持续刷新画像。不发通知(不建 notify_log、不设 delivery_status)、
        不占治理人力(assignee=None)。审计由调用方持有。

        Returns:
            持久化的 ``ticket_id``。
        """
        ...

    def refresh_snapshot(self, ticket_id: str, **snapshot_fields: Any) -> bool:
        """Refresh an active ticket's mutable snapshot (non-state-transition).

        Owned by the driver so the snapshot write is unified with the
        ticket-lifecycle orchestration surface; ``governance_status`` is
        unchanged. Returns True if the ticket was found and updated.
        """
        ...

    def observe_for_whitelist(
        self, ticket_id: str, *, close_reason: str, now: datetime,
    ) -> bool:
        """加白→转 OBSERVED 单条语义方法(四条加白入口统一收口)。

        Returns True if the ticket was found and observed, False if not found.
        """
        ...

    def close_observed_for_removal(
        self, ticket_id: str, *, now: datetime,
    ) -> bool:
        """删白收尾:OBSERVED → CLOSED(whitelist_approved)终态 + cancel pending(best-effort)。

        不设 cooldown(等 off-batch 正常重建)。非 OBSERVED 态返 False(幂等 no-op)。
        审计由调用方(whitelist_service)持有。
        """
        ...

    def close_for_stale_replace(
        self, ticket_id: str, *, now: datetime,
    ) -> bool:
        """未回复换新 → CLOSED(stale_replaced) + cancel pending(不设 cooldown_until)。

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

    def admin_close(
        self, ticket_id: str, *, now: datetime,
        close_conclusion: str | None = None,
        close_payload: str | None = None,
    ) -> bool:
        """Any non-CLOSED → CLOSED(admin_closed) + cancel pending.

        The caller (admin_service) owns the audit row (carries the conclusion +
        actor_id=admin_id) — the driver does not write a duplicate audit,
        matching the audit-ownership convention of its siblings.

        ``close_conclusion`` / ``close_payload`` 透传领域 close() 落盘(管理员
        裁定结论 + 明细 JSON),自动关单路径不传(留 None)。

        Returns True if the ticket was found and closed, False if not found
        (or already closed — idempotent no-op).
        """
        ...

    def bulk_close_open(
        self, *, close_reason: str, now: datetime,
        close_conclusion: str | None = None,
    ) -> int:
        """Bulk admin-close all open/scheduled tickets — joint orchestration:
        land ``task_record`` subject CLOSED (ticket machine) + cancel pending
        notifications (notify-delivery machine, one-way side effect). Ticket is
        cause, notify is effect. ``close_conclusion`` 透传批量落关单结论
        (统一 ``BULK_CLOSED``)。Returns the number of tickets closed.
        """
        ...

    def bulk_close_by_ticket_ids(
        self, ticket_ids: list[str], *, now: datetime,
        close_conclusion: str | None = None,
    ) -> int:
        """Per-ticket admin-close by ``ticket_id`` set — Task 8 uses this
        after cancel_pending / bulk_whitelist cancel notify delivery, to close
        the matching ``task_record`` subjects (口径对齐通知侧). Per-ticket
        find→guard→save→cancel chain (domain guard active); does NOT bare-use
        the full bulk_close_open (would over-close responded scheduled tickets).
        ``close_conclusion`` 透传逐条落盘(批量统一 ``BULK_CLOSED``)。
        Idempotent. Returns the number of tickets actually closed.
        """
        ...

    def bulk_observe_by_ticket_ids(
        self,
        ticket_ids: list[str],
        *,
        now: datetime,
        close_reason: str = ...,
    ) -> int:
        """Per-ticket observe (→OBSERVED) by ``ticket_id`` set — 批量加白收口。

        批量加白把对应工单转 OBSERVED(加白语义),逐条走 observe_for_whitelist
        守卫激活,幂等。close_reason 默认 WHITELIST_APPROVED。Returns 实际转
        OBSERVED 的工单数。
        """
        ...


@runtime_checkable
class NotifyLifecycleServiceProtocol(Protocol):
    """通知发送状态机正常路径唯一驱动(service↔service 契约,住 core 自家)。

    对齐工单机的 ``GovernanceLifecycleService`` 收口标准:正常投递路径
    (单条 pending→sending→sent/failed)的状态推进经此驱动,每次先 invoke
    ``GovernanceNotification`` 领域守卫再 save。批量/紧急路径(批量取消、
    紧急制动批量关、手动投递)不走此驱动,直接走 repo SQL 原语(紧急而准确,
    SQL 一条原子 UPDATE 带 WHERE 守卫是最准确形态)。

    Note: 本 Protocol 定义在 core 自家(``services/service_protocols.py``),
    ``api/governance_service.py`` 可 re-export 给 router(若有需要);service
    间消费直接 import 本文件,不跨层依赖 ``api/``。
    """

    def claim(
        self, notification_id: str, *, now: datetime,
    ) -> GovernanceNotification | None:
        """领用 pending→sending(原子 CAS,并发安全);返改后领域模型,被抢/非 pending 返 None。"""
        ...

    def mark_sent(
        self,
        notification_id: str,
        *,
        external_message_id: str | None,
        sent_at: datetime,
    ) -> bool:
        """sending→sent(领域守卫);找不到/guard 失败返 False。"""
        ...

    def mark_failed(
        self,
        notification_id: str,
        *,
        error: str,
        terminal: bool,
    ) -> bool:
        """sending→failed(终态)/ sending→pending(重试);领域守卫;返 False 同上。"""
        ...


@runtime_checkable
class GovernanceWorkflowServiceProtocol(Protocol):
    """工单审批服务契约(从 admin_service 按路由边界拆出)。

    审批面(workflow_router 的 list/detail/review 端点)对应本 Protocol;
    管理面(admin_router)对应 GovernanceAdminServiceProtocol。两服务各管一面,
    零反向依赖:workflow 不依赖 admin,审批副作用(加白名单/关工单经状态机驱动)
    自带。
    """

    def list_review_tickets(
        self,
        statuses: list[str] | None,
        *,
        offset: int = 0,
        limit: int = 50,
        delivery_statuses: list[str] | None = None,
    ) -> tuple[list[GovernanceTicket], int]:
        """评审工单列表(跨 owner, 按治理状态过滤, 分页)。返回领域模型 + 总数。

        ``delivery_statuses`` 可选按投递状态(pending/sent/failed/cancelled)过滤;
        None 不过滤,空列表短路返回空。
        """
        ...

    def list_whitelist_observed_tickets(
        self, *, offset: int = 0, limit: int = 50,
    ) -> tuple[list[GovernanceTicket], int]:
        """白单观察工单视图:当前 OBSERVED 态工单(加白中 bot 最新治理画像)。

        薄包装,转调 list_review_tickets([observed])。语义入口。
        """
        ...

    def get_review_ticket_detail(
        self, ticket_id: str,
    ) -> GovernanceTicket | None:
        """评审工单详情(单工单领域模型,纯本体,不含跨聚合派生)。"""
        ...

    def build_review_ticket_detail(
        self, ticket_id: str,
    ) -> tuple[GovernanceTicket, bool] | None:
        """组装工单详情视图:工单本体 + 是否在治理白名单中(跨聚合派生位)。

        Returns ``(ticket, in_whitelist)``;工单不存在返回 None;
        工单缺 bot_id/owner_id 时 in_whitelist=False。
        """
        ...

    def get_pending_notification(self, ticket_id: str) -> dict | None:
        """查工单待回复通知(notification_id + 元信息)。"""
        ...

    def list_ticket_history_by_worker(
        self,
        *,
        worker_id: str | None = None,
        owner_id: str | None = None,
        bot_id: str | None = None,
        limit: int = 5,
    ) -> tuple[list[GovernanceTicket], str | None, str | None, str | None]:
        """按 worker 取最近 N 条工单历史(全状态,gmt_create 倒序),辅助关单-重开决策。

        管理员处理新开/重开工单时,借此横向看该 worker 的工单生命周期(关单原因/
        裁定结论/用户反馈/审批记录)。只读、无副作用、不写审计。``worker_id``
        (``owner:bot``)优先解析并覆盖独立 ``owner_id``/``bot_id``;解析后三者皆空
        抛 :class:`ValueError`(路由侧 400,防全表扫)。Returns
        ``(tickets, resolved_owner_id, resolved_bot_id, worker_id_echo)``。
        """
        ...

    def delete_ticket_cascade(
        self,
        *,
        ticket_id: str,
        dry_run: bool,
        reason: str,
        operator: str,
    ) -> dict:
        """按 ticket_id 精确级联删单工单 + 归属通知(best-effort)。

        单向(ticket→notify)、单工单防写放大、dry-run 预览。工单不存在
        返回 ticket_found=False 且不写审计。best-effort:通知清理失败不阻断
        工单删除,失败计数计入审计+响应。2026-07-17 从 admin_service 迁入。
        """
        ...

    def review_ticket(
        self, ticket_id: str, action: str, admin_id: str, remark: str = "",
    ) -> TicketActionOutcome:
        """审批:approve_close / approve_whitelist / reject_for_reopen(§7.5.2)。"""
        ...

    def admin_close(
        self, ticket_id: str, admin_id: str, *,
        conclusion: AdminCloseConclusion,
        close_payload: str | None = None,
    ) -> TicketActionOutcome:
        """立即关单(无 cooldown),从 admin_service 迁入(工单运营归属)。

        ``conclusion`` 必传(管理员裁定枚举),``close_payload`` 为明细 JSON 字符串
        (router 从 ``CloseDetailPayload`` 序列化灌入, None = 无手写说明)。
        """
        ...

    def set_override_owner(
        self, ticket_id: str, override_owner: str | None, *, operator: str,
    ) -> TicketActionOutcome:
        """设/清工单通知收件人覆盖 (bot 转交/机器人 owner 代联, D1/D4)。

        只改 ``override_owner`` 字段,不碰状态机/owner_id/cooldown。
        ``override_owner`` 非空 → 设;None/空串 → 清(恢复发原 owner)。
        reminder 建通知时 ``override or owner`` 写进 notify_log.owner_id。
        """
        ...

    def cancel_pending(
        self, reason: str, operator: str,
    ) -> BulkOperationResult:
        """取消全部未响应待通知 + 关对应工单(从 admin_service 迁入)。"""
        ...

    def close_all_open(
        self, reason: str, operator: str,
    ) -> BulkOperationResult:
        """关闭全部 open/muted(含已响应)+ 关全部 open/scheduled 工单(迁入)。"""
        ...
