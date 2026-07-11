"""Pydantic schemas for economy/governance endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from agentclaw.community.core.economy.governance.domain.record import GovernanceRecord

if TYPE_CHECKING:
    from agentclaw.community.core.economy.governance.domain.ticket import (
        GovernanceTicket,
    )
    from agentclaw.community.core.economy.governance.services.admin_service import (
        TicketActionOutcome,
    )


# ---------------------------------------------------------------------------
# Generic
# ---------------------------------------------------------------------------

class ApiResponse(BaseModel):
    """Generic API response wrapper."""
    success: bool = True
    data: Any = None
    message: str = ""
    error_code: str = ""


# ---------------------------------------------------------------------------
# Resolve (task_record based, §7.4)
# ---------------------------------------------------------------------------

class GovernanceNotifyResolveRequest(BaseModel):
    """Request body for resolving a governance notification."""
    response: str = Field(..., description="optimized / need_time / dispute / whitelist")
    remark: str | None = Field(None, description="Optional remark (required for dispute)")
    repair_deadline: str | None = Field(
        None,
        description="ISO date, required for need_time",
    )
    feedback_payload: dict | None = Field(
        None,
        description="Structured feedback JSON (validated as JSON only)",
    )


class GovernanceNotifyResolveResponse(BaseModel):
    """Response for resolve endpoint — now ticket-based (§7.4)."""
    notification_id: str = ""
    ticket_id: str = ""
    governance_status: str = ""
    close_reason: str | None = None
    mute_until: str | None = None


# ---------------------------------------------------------------------------
# Record process result (shared by offline-batch)
# ---------------------------------------------------------------------------

class RecordProcessResultItem(BaseModel):
    """Single record result from record-process."""
    worker_key: str = ""
    entered_governance_scope: bool = False
    action: str = ""
    reason: str = ""
    ticket_id: str | None = None
    notification_md_preview: str | None = None


# ---------------------------------------------------------------------------
# Offline batch (§7.2, upgraded)
# ---------------------------------------------------------------------------

class GovernanceRecordInput(BaseModel):
    """单条离线批治理记录(分层必填) — 边界校验后转 GovernanceRecord 领域模型。

    必填(身份/路由/数据版本):owner_id / bot_id / governance_decision / dt_version。
    其余可选,缺则 None,由下游 refresh_snapshot/add_ticket 接受 None。
    """

    # 必填:身份/路由/数据版本
    owner_id: str = Field(..., min_length=1, description="负责人工号")
    bot_id: str = Field(..., min_length=1, description="Bot ID")
    governance_decision: str = Field(..., min_length=1, description="治理判决 actionable/normal")
    dt_version: str = Field(..., min_length=1, description="数据版本 YYYYMMDD")
    # 可选:身份补充
    worker_id: str | None = Field(None, description="生产者优先的 worker_id(owner_id:bot_id);缺则合成")
    bot_name: str | None = Field(None, description="Bot 名称")
    # 可选:数据字段,缺则默认 None,传给 refresh_snapshot/add_ticket
    hit_dimensions: str | None = Field(None, description="命中维度")
    hit_dimensions_count: int | None = Field(None, description="命中维度数")
    governance_max_priority: str | None = Field(None, description="治理优先级")
    expected_token_saving: int | None = Field(None, description="预期 token 节省")
    saving_ratio: float | None = Field(None, description="节省率 0-1")
    task_summary: str | None = Field(None, description="任务摘要")
    notification_structured: str | None = Field(None, description="结构化通知 JSON")
    analysis_status: str | None = Field(None, description="分析状态")

    def to_record(self) -> GovernanceRecord:
        """边界转领域模型:Pydantic 校验后由 endpoint 调用,service 接 GovernanceRecord。"""
        return GovernanceRecord(
            owner_id=self.owner_id,
            bot_id=self.bot_id,
            governance_decision=self.governance_decision,
            dt_version=self.dt_version,
            worker_id=self.worker_id,
            bot_name=self.bot_name,
            hit_dimensions=self.hit_dimensions,
            hit_dimensions_count=self.hit_dimensions_count,
            governance_max_priority=self.governance_max_priority,
            expected_token_saving=self.expected_token_saving,
            saving_ratio=self.saving_ratio,
            task_summary=self.task_summary,
            notification_structured=self.notification_structured,
            analysis_status=self.analysis_status,
        )


class OfflineBatchRequest(BaseModel):
    """Request body for offline-batch upsert (§7.2)."""
    records: list[GovernanceRecordInput] = Field(..., min_length=1)
    batch_id: str = Field("", description="Batch unique ID from producer")
    dt_version: str = Field("", description="Data version (YYYYMMDD)")
    total_count: int = Field(0, description="Expected record count for quality check")


class OfflineBatchResponse(BaseModel):
    """Response for offline-batch upsert (§7.2, upgraded)."""
    batch_id: str = ""
    run_id: str = ""
    total_records: int = 0
    upsert_results: list[RecordProcessResultItem] = Field(default_factory=list)
    batch_quality_skipped: bool = False
    batch_quality_skip_reasons: list[str] = Field(default_factory=list)
    errors: int = 0


# ---------------------------------------------------------------------------
# Admin: review (§7.5.2) / pause (§7.5.1) / emergency-close
# ---------------------------------------------------------------------------

class AdminReviewRequest(BaseModel):
    """Request body for admin review (§7.5.2)."""
    ticket_id: str = Field(..., description="Ticket to review")
    action: str = Field(..., description="approve_close / approve_whitelist / reject_for_reopen")
    admin_id: str = Field("", description="Admin who triggered the review")
    remark: str = Field("", description="Review remark")


class AdminReviewResponse(BaseModel):
    """Response for admin review."""

    ticket_id: str = ""
    governance_status: str = ""
    close_reason: str | None = None

    @classmethod
    def from_outcome(cls, outcome: TicketActionOutcome) -> AdminReviewResponse:
        """从 TicketActionOutcome 领域返回值构造响应(显式序列化,非裸 dict)。

        Args:
            outcome: review_ticket / emergency_close 返回的领域 I/O 对象。

        Returns:
            审批响应,status 取 enum 的 value 字符串。
        """
        status = outcome.status.value if hasattr(outcome.status, "value") else str(outcome.status or "")
        return cls(
            ticket_id=outcome.ticket_id,
            governance_status=status,
            close_reason=outcome.close_reason,
        )


# ---------------------------------------------------------------------------
# Review router — 评审工单列表/详情响应(从领域模型 GovernanceTicket 显式构造)
# ---------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    """datetime → ISO 字符串(None 透传),供 schema 序列化复用。"""
    return value.isoformat() if value is not None else None


class ReviewTicketItem(BaseModel):
    """评审工单列表单行 — 字段贴合 governance-admin.html 三栏展示。"""

    ticket_id: str | None = None
    bot_name: str | None = None
    owner_id: str | None = None
    governance_status: str = ""
    latest_decision: str | None = None
    hit_dimensions: str | None = None
    saving_ratio: float | None = None
    response: str | None = None
    review_reason: str | None = None
    gmt_create: str | None = None

    @classmethod
    def from_ticket(cls, ticket: GovernanceTicket) -> ReviewTicketItem:
        """从 GovernanceTicket 领域模型构造列表行(读 snapshot 委托属性 + 实体字段)。"""
        s = ticket.snapshot
        status = ticket.governance_status
        return cls(
            ticket_id=ticket.ticket_id,
            bot_name=ticket.bot_name,
            owner_id=ticket.owner_id,
            governance_status=status.value if hasattr(status, "value") else str(status or ""),
            latest_decision=s.current_decision,
            hit_dimensions=s.triggered_dimensions,
            saving_ratio=s.saving_ratio,
            response=ticket.user_feedback,
            review_reason=ticket.review_reason,
            gmt_create=_iso(ticket.gmt_create),
        )


class ReviewTicketListResponse(BaseModel):
    """评审工单列表响应 — items + 分页元信息。"""

    items: list[ReviewTicketItem] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0
    status_filter: list[str] = Field(default_factory=list)

    @classmethod
    def from_tickets(
        cls,
        tickets: list[GovernanceTicket],
        *,
        total: int,
        limit: int,
        offset: int,
        status_filter: list[str],
    ) -> ReviewTicketListResponse:
        """从领域工单列表构造响应(逐条 from_ticket,显式序列化)。"""
        return cls(
            items=[ReviewTicketItem.from_ticket(t) for t in tickets],
            total=total,
            limit=limit,
            offset=offset,
            status_filter=status_filter,
        )


class ReviewTicketDetailResponse(BaseModel):
    """评审工单详情 — 列表字段外加详情面板所需全部字段。"""

    # 基础信息
    ticket_id: str | None = None
    worker_id: str | None = None
    bot_id: str | None = None
    owner_id: str | None = None
    bot_name: str | None = None
    dt_version: str | None = None
    task_summary: str | None = None
    governance_max_priority: str | None = None
    # 治理态
    governance_status: str = ""
    latest_decision: str | None = None
    hit_dimensions: str | None = None
    saving_ratio: float | None = None
    consecutive_normal_days: int = 0
    # 用户反馈
    response: str | None = None
    response_remark: str | None = None
    response_at: str | None = None
    response_source: str | None = None
    feedback_payload: str | None = None
    # 评审 / 生命周期
    review_reason: str | None = None
    review_decision: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_remark: str | None = None
    close_reason: str | None = None
    closed_at: str | None = None
    cooldown_until: str | None = None
    mute_until: str | None = None
    remind_at: str | None = None
    remind_count: int = 0
    # 元信息
    gmt_create: str | None = None
    gmt_modified: str | None = None

    @classmethod
    def from_ticket(cls, ticket: GovernanceTicket) -> ReviewTicketDetailResponse:
        """从 GovernanceTicket 领域模型构造详情(读 snapshot 委托 + 实体字段,datetime→ISO)。"""
        s = ticket.snapshot
        status = ticket.governance_status
        return cls(
            ticket_id=ticket.ticket_id,
            worker_id=ticket.worker_id,
            bot_id=ticket.bot_id,
            owner_id=ticket.owner_id,
            bot_name=ticket.bot_name,
            dt_version=s.dt_version,
            task_summary=s.task_summary,
            governance_max_priority=s.severity,
            governance_status=status.value if hasattr(status, "value") else str(status or ""),
            latest_decision=s.current_decision,
            hit_dimensions=s.triggered_dimensions,
            saving_ratio=s.saving_ratio,
            consecutive_normal_days=s.consecutive_normal_days,
            response=ticket.user_feedback,
            response_remark=ticket.feedback_remark,
            response_at=_iso(ticket.feedback_at),
            response_source=ticket.feedback_source,
            feedback_payload=ticket.feedback_payload,
            review_reason=ticket.review_reason,
            review_decision=ticket.review_decision,
            reviewed_by=ticket.reviewed_by,
            reviewed_at=_iso(ticket.reviewed_at),
            review_remark=ticket.review_remark,
            close_reason=ticket.close_reason,
            closed_at=_iso(ticket.closed_at),
            cooldown_until=_iso(ticket.cooldown_until),
            mute_until=_iso(ticket.resume_at),
            remind_at=_iso(ticket.remind_at),
            remind_count=ticket.remind_count,
            gmt_create=_iso(ticket.gmt_create),
            gmt_modified=_iso(ticket.gmt_modified),
        )


class AdminPauseRequest(BaseModel):
    """Request body for admin pause (§7.5.1)."""
    ticket_id: str = Field(..., description="Ticket to pause")
    admin_id: str = Field("", description="Admin who triggered the pause")
    reason: str = Field("", description="Pause reason")


class AdminEmergencyCloseRequest(BaseModel):
    """Request body for emergency close (§6.3)."""
    ticket_id: str = Field(..., description="Ticket to close")
    admin_id: str = Field("", description="Admin who triggered the close")
    reason: str = Field("", description="Close reason")


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------

class WhitelistEntry(BaseModel):
    """A single whitelist entry."""
    bot_id: str
    owner_id: str
    reason: str | None = None
    expires_at: str | None = None


class WhitelistAddRequest(BaseModel):
    """Request body for single whitelist add (point-to-point)."""
    bot_id: str = Field(..., description="Bot ID to whitelist")
    owner_id: str = Field(..., description="Owner (staff) ID")
    reason: str = Field("", description="Optional reason for whitelisting")
    source: str = Field("manual", description="manual / owner / admin / system")


class WhitelistDeletePair(BaseModel):
    """A single (bot_id, owner_id) pair for whitelist deletion."""
    bot_id: str
    owner_id: str


class WhitelistDeleteRequest(BaseModel):
    """Request body for whitelist deletion."""
    bot_owner_pairs: list[WhitelistDeletePair] = Field(
        ..., min_length=1, description="按 (bot_id, owner_id) 对删除",
    )
    reason: str = Field(..., description="操作原因，写入 audit")


# ---------------------------------------------------------------------------
# Emergency
# ---------------------------------------------------------------------------

class EmergencyRequest(BaseModel):
    """Request body for emergency brake / admin actions."""
    action: str = Field(..., description="pause / resume / bulk-whitelist / cancel-pending / close-all-open")
    reason: str = Field(..., description="Required: reason for the action")
    operator: str = Field("", description="Who triggered the action")
    bot_ids: list[str] | None = Field(None, description="Required for bulk-whitelist")


class EmergencyStateResponse(BaseModel):
    """Response for emergency state query."""
    paused: bool = False
    reason: str | None = None
    operator: str | None = None
    paused_at: str | None = None
    pending_count: int = 0
    open_count: int = 0
    whitelist_count: int = 0


# ---------------------------------------------------------------------------
# Records / Notifications delete
# ---------------------------------------------------------------------------

class RecordsDeleteRequest(BaseModel):
    """Request body for emergency records / notifications delete."""
    table: str = Field(..., description="record_daily | notify_log")
    dt_versions: list[str] | None = Field(None, description="按 dt_version 删除 (record_daily)")
    ids: list[int] | None = Field(None, description="按主键 ID 批量删除 (record_daily)")
    notification_ids: list[str] | None = Field(None, description="按 notification_id 批量删除 (notify_log)")
    dry_run: bool = Field(True, description="true=只统计不删除")
    reason: str = Field(..., description="操作原因，写入 audit")


# ---------------------------------------------------------------------------
# Card callback (iframe fetch POST)
# ---------------------------------------------------------------------------

class CardCallbackIFrameRequest(BaseModel):
    """Request body for card-callback iframe fetch POST."""

    notification_id: str = Field(..., description="通知唯一 ID")
    response: str = Field(..., description="optimized / need_time / dispute / whitelist")
    remark: str | None = Field(None, description="用户补充说明（dispute/whitelist 时必填，其他时选填）")
    repair_deadline: str | None = Field(None, description="need_time 时必填，ISO 日期")
    feedback_payload: dict | None = Field(None, description="结构化反馈 JSON（items 等，不含 overall_remark）")


class CardCallbackResponse(BaseModel):
    """Response body for card-callback endpoint — now ticket-based (§7.4)."""

    notification_id: str = ""
    ticket_id: str = ""
    governance_status: str = ""
    close_reason: str | None = None
    response: str = ""
    response_source: str = ""
    message: str | None = None

    @classmethod
    def from_result(cls, result: Any) -> CardCallbackResponse:
        """Build response from a ResolveResult."""
        return cls(
            notification_id=result.notification_id,
            ticket_id=getattr(result, "ticket_id", "") or "",
            governance_status=result.governance_status,
            close_reason=result.close_reason,
            response=getattr(result, "response", "") or "",
            response_source=getattr(result, "response_source", "") or "",
            message=getattr(result, "message", None),
        )
