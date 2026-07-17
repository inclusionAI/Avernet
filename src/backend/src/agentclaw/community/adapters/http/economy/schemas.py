"""Pydantic schemas for economy/governance endpoints."""
from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from agentclaw.community.core.economy.governance.domain.enums import GovernanceStatus
from agentclaw.community.core.economy.governance.domain.record import GovernanceRecord
from agentclaw.community.core.economy.governance.domain.ticket import compute_available_actions

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


class RemindRequest(BaseModel):
    """Request body for admin tickets:remind — 手动补发 reminder。"""
    worker_id: str = Field(..., min_length=1, description="worker_id (owner_id:bot_id)")


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
# Workflow: review (§7.5.2) — 正常业务流程面,ticket_id 走 body/query(零 path 参数)
# ---------------------------------------------------------------------------

class WorkflowReviewRequest(BaseModel):
    """Request body for workflow ticket review (§7.5.2).

    审计操作人不在 body(取自鉴权上下文 ``ctx.user_id``,防 body 顶替)。
    """
    ticket_id: str = Field(..., description="Ticket to review")
    action: str = Field(..., description="approve_close / approve_whitelist / reject_for_reopen")
    remark: str = Field("", description="Review remark")


class WorkflowReviewResponse(BaseModel):
    """Response for workflow ticket review."""

    ticket_id: str = ""
    governance_status: str = ""
    close_reason: str | None = None

    @classmethod
    def from_outcome(cls, outcome: TicketActionOutcome) -> WorkflowReviewResponse:
        """从 TicketActionOutcome 领域返回值构造响应(显式序列化,非裸 dict)。

        Args:
            outcome: review_ticket / admin_close 返回的领域 I/O 对象。

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
    id: int | None = None
    bot_id: str | None = None
    bot_name: str | None = None
    owner_id: str | None = None
    governance_status: str = ""
    latest_decision: str | None = None
    hit_dimensions: str | None = None
    saving_ratio: float | None = None
    response: str | None = None
    review_reason: str | None = None
    gmt_create: str | None = None
    gmt_modified: str | None = None
    delivery_status: str | None = None

    @classmethod
    def from_ticket(cls, ticket: GovernanceTicket) -> ReviewTicketItem:
        """从 GovernanceTicket 领域模型构造列表行(读 snapshot 委托属性 + 实体字段)。"""
        s = ticket.snapshot
        status = ticket.governance_status
        return cls(
            ticket_id=ticket.ticket_id,
            id=ticket.id,
            bot_id=ticket.bot_id,
            bot_name=ticket.bot_name,
            owner_id=ticket.owner_id,
            governance_status=status.value if hasattr(status, "value") else str(status or ""),
            latest_decision=s.current_decision,
            hit_dimensions=s.triggered_dimensions,
            saving_ratio=s.saving_ratio,
            response=ticket.user_feedback,
            review_reason=ticket.review_reason,
            gmt_create=_iso(ticket.gmt_create),
            gmt_modified=_iso(ticket.gmt_modified),
            delivery_status=ticket.delivery_status,
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


def _extract_feedback_notification_id(payload_json: str | None) -> str | None:
    """从 feedback_payload JSON 解出本次反馈回执来自哪个 notification_id。

    v2 自包含 payload 的 ``ticket_ref.notification_id`` 即回调触发的通知 ID
    (写库时由服务端 enrich 从回调入参注入,不可伪造)。v1 / 解析失败 / 缺字段 → None。
    保证反馈原子性:回执来源随反馈 payload 同行落地,零额外列、零 join。

    Args:
        payload_json: task_record.feedback_payload 列原始 JSON 字符串。

    Returns:
        notification_id 或 None。
    """
    if not payload_json:
        return None
    try:
        payload = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    ref = payload.get("ticket_ref")
    if isinstance(ref, dict):
        nid = ref.get("notification_id")
        if isinstance(nid, str) and nid:
            return nid
    return None


class TicketActionInfo(BaseModel):
    """单个可做 review 动作(后端下发,前端动态渲染)。

    后端成动作单一事实源:按用户反馈返回可做动作集 + 差异化 label + endpoint +
    remark_required。前端据此渲染,不硬编码动作/文案/状态-动作映射。
    """

    value: str = Field(..., description="动作枚举值(approve_close/approve_scheduled/approve_whitelist/reject_for_reopen)")
    label: str = Field(..., description="中文展示文案(按用户反馈差异化)")
    endpoint: str = Field(..., description="POST 端点路径")
    remark_required: bool = Field(False, description="备注是否必填")


class ReviewTicketDetailResponse(BaseModel):
    """评审工单详情 — 列表字段外加详情面板所需全部字段。"""

    # 基础信息
    ticket_id: str | None = None
    id: int | None = None
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
    feedback_notification_id: str | None = None
    available_actions: list[TicketActionInfo] = Field(
        default_factory=list,
        description="当前可做 review 动作(按用户反馈派生,前端动态渲染)",
    )
    # 白名单状态(单点查询,仅详情;列表接口不查以防 N+1)
    in_whitelist: bool = False
    # 投递状态(回写自 notify_log)
    delivery_status: str | None = None
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
    def _build_available_actions(cls, ticket: GovernanceTicket) -> list[TicketActionInfo]:
        """按工单当前状态构造可做 review 动作(仅 waiting_review 下发,其余空)。

        委托领域层 ``compute_available_actions(user_feedback)``(按反馈派生)。
        非 waiting_review(open/scheduled/closed)不发动作。
        """
        if ticket.governance_status != GovernanceStatus.WAITING_REVIEW:
            return []
        return [
            TicketActionInfo(**a) for a in compute_available_actions(ticket.user_feedback)
        ]

    @classmethod
    def from_ticket(
        cls,
        ticket: GovernanceTicket,
        *,
        in_whitelist: bool = False,
    ) -> ReviewTicketDetailResponse:
        """从 GovernanceTicket 领域模型构造详情(读 snapshot 委托 + 实体字段,datetime→ISO)。

        Args:
            ticket: 工单领域模型。
            in_whitelist: 该工单 (bot_id, owner_id) 是否在治理白名单中
                (由 service 层单点查询传入,默认 False;列表接口不传以防 N+1)。
        """
        s = ticket.snapshot
        status = ticket.governance_status
        return cls(
            ticket_id=ticket.ticket_id,
            id=ticket.id,
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
            feedback_notification_id=_extract_feedback_notification_id(ticket.feedback_payload),
            available_actions=cls._build_available_actions(ticket),
            in_whitelist=in_whitelist,
            delivery_status=ticket.delivery_status,
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
# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------

class WhitelistEntry(BaseModel):
    """A single whitelist entry."""
    bot_id: str
    owner_id: str
    reason: str | None = None
    expires_at: str | None = None
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
# Admin: 制动 (brake) / 工单批量管理 / 白名单批量 / 按 worker 投递
# 拆自原 5-action 统一 action 端点(action 开关分发多资源,已退场),全 body 驱动。
# ---------------------------------------------------------------------------

class BrakeToggleRequest(BaseModel):
    """Request body for global governance brake toggle (pause/resume).

    审计操作人不在 body(取自鉴权上下文 ``ctx.user_id``,防 body 顶替)。
    """
    enabled: bool = Field(..., description="true=pause(暂停治理流程), false=resume(恢复)")
    reason: str = Field("", description="Optional reason for audit")


class TicketsCloseRequest(BaseModel):
    """Request body for closing one or more governance tickets.

    单条/批量统一入参:把要关的 ticket_id 放进列表,handler 循环调
    ``admin_svc.admin_close``(已委托 ``lifecycle_svc``,关工单+cancel_pending
    由 driver 编排)。禁止直调 repo。
    """
    reason: str = Field(..., description="Close reason for audit")
    ticket_ids: list[str] = Field(..., min_length=1, description="Ticket IDs to close")


class TicketsCloseAllRequest(BaseModel):
    """Request body for closing all active governance tickets (admin bulk).

    handler dispatch 复用状态机收口后的两方法:
    ``only_unresponded=true`` → ``admin_svc.cancel_pending``(仅未响应,ADMIN_CLOSED);
    否则 → ``admin_svc.close_all_open``(全量含已响应,ADMIN_CLOSED)。两方法已联合编排
    task_record 工单主体 + notify_log 通知 + audit(状态机 Task 8 口径对齐)。
   cooldown_days 走 config.cool_down_days,无入参。
    """
    reason: str = Field(..., description="Close reason for audit")
    only_unresponded: bool = Field(False, description="true=仅关未响应(cancel_pending),false=全量(close_all_open)")


class WhitelistBulkAddRequest(BaseModel):
    """Request body for bulk whitelist add (admin 代加白).

    审计操作人不在 body(取自鉴权上下文 ``ctx.user_id``,防 body 顶替)。
    """
    bot_ids: list[str] = Field(..., min_length=1, description="Bot IDs to whitelist")
    reason: str = Field(..., description="Required: reason for audit")


class TicketsDeliverRequest(BaseModel):
    """Request body for delivering pending notifications by worker_id.

    按 worker 精准取该工单 pending 通知投递,**不重跑状态机**(治理决策进入时
    已跑过,pending 已躺 notify_log)。前一档 scan-and-deliver 是随机批量兜底,
    本端点是精准单 worker。
    """
    worker_id: str = Field(..., description="owner_id:bot_id")
    override_recipient: str | None = Field(None, description="覆盖收件人工号(纯数字 4~10 位)")
    dry_run: bool = Field(True, description="true=只构建不发钉钉")
    channel: str = Field("auto", description="auto(跟随DB)|markdown|tc_card")


class BrakeStateResponse(BaseModel):
    """Response for brake state query."""
    paused: bool = False
    reason: str | None = None
    operator: str | None = None
    paused_at: str | None = None
    pending_count: int = 0
    open_count: int = 0
    whitelist_count: int = 0
# ---------------------------------------------------------------------------
# Audit log (read-only history query by worker)
# ---------------------------------------------------------------------------

class AuditLogItemResponse(BaseModel):
    """Single governance audit row — mirrors ``AuditLogOrm.to_dict()``.

    治理审计记录视图(只读),供前端按 worker 追溯治理动作。
    字段对齐 ``ac_governance_audit`` 表(AuditLogOrm),无 ``worker_id`` 列
    ——worker 维度由 owner_id:bot_id 复合定位(见 audit-logs 端点)。
    """

    id: int | None = None
    run_id: str | None = None
    notification_id: str | None = None
    bot_id: str | None = None
    owner_id: str | None = None
    check_result: str | None = None
    governance_decision: str | None = None
    hit_dimensions: str | None = None
    expected_token_saving: int | None = None
    saving_ratio: float | None = None
    action_taken: str | None = None
    source: str | None = None
    error_msg: str | None = None
    actor_id: str | None = None
    server_host: str | None = None
    dry_run: int | None = None
    env: str | None = None
    gmt_create: datetime | None = None
    gmt_modified: datetime | None = None


# ---------------------------------------------------------------------------
# Records / Notifications delete
# ---------------------------------------------------------------------------

class RecordsDeleteRequest(BaseModel):
    """Request body for admin records / notifications delete."""
    table: str = Field(..., description="record_daily | notify_log")
    dt_versions: list[str] | None = Field(None, description="按 dt_version 删除 (record_daily)")
    ids: list[int] | None = Field(None, description="按主键 ID 批量删除 (record_daily)")
    notification_ids: list[str] | None = Field(None, description="按 notification_id 批量删除 (notify_log)")
    dry_run: bool = Field(True, description="true=只统计不删除")
    reason: str = Field(..., description="操作原因，写入 audit")


# ---------------------------------------------------------------------------
# Card callback (iframe fetch POST)
# ---------------------------------------------------------------------------

class CardCallbackFeedbackItem(BaseModel):
    """单个建议项的用户逐项决策(卡片 items[] 输入)。

    仅含用户能提供的信息;建议项正文快照由服务端 enrich 注入,不在输入内。
    """

    index: int = Field(..., description="建议项序号,与分析表 action_items[].index 对齐")
    action: str = Field(..., description="accepted / partial / rejected")
    remark: str | None = Field(None, description="逐项备注(选填)")


class CardCallbackFeedbackPayload(BaseModel):
    """卡片 feedback_payload 输入结构(v1 字段集,服务端据此 enrich 成 v2 自包含 payload)。"""

    version: int | None = Field(None, description="卡片自定版本号(非顶层 feedback_schema_version)")
    overall_action: str | None = Field(None, description="整体决策(accepted/partial/rejected 等)")
    overall_remark: str | None = Field(None, description="整体补充说明")
    repair_deadline: str | None = Field(None, description="need_time 修复截止日期(ISO)")
    items: list[CardCallbackFeedbackItem] | None = Field(None, description="逐项决策列表")

    model_config = {"extra": "allow"}


class CardCallbackIFrameRequest(BaseModel):
    """Request body for card-callback iframe fetch POST.

    ``feedback_payload`` 优先按结构化模型解析;旧卡片回传的任意 dict 仍被接受
    (模型允许 extra 字段),逐项决策取 ``items[]``,其余兜底走 dict 访问。
    """

    notification_id: str = Field(..., description="通知唯一 ID")
    response: str = Field(..., description="optimized / need_time / dispute / whitelist")
    remark: str | None = Field(None, description="用户补充说明（dispute/whitelist 时必填，其他时选填）")
    repair_deadline: str | None = Field(None, description="need_time 时必填，ISO 日期")
    feedback_payload: CardCallbackFeedbackPayload | dict | None = Field(
        None, description="结构化反馈 JSON（items 等，不含 overall_remark）",
    )


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
