"""Pydantic schemas for economy/governance endpoints."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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

class OfflineBatchRequest(BaseModel):
    """Request body for offline-batch upsert (§7.2)."""
    records: list[dict] = Field(..., min_length=1)
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


class WhitelistBatchRequest(BaseModel):
    """Request body for batch whitelist add."""
    entries: list[WhitelistEntry] = Field(..., min_length=1)
    source: str = Field("manual", description="manual / owner / admin / system")


class WhitelistBatchResponse(BaseModel):
    """Response for batch whitelist add."""
    inserted: int = 0
    skipped: int = 0


class WhitelistDeletePair(BaseModel):
    """A single (bot_id, owner_id) pair for whitelist deletion."""
    bot_id: str
    owner_id: str


class WhitelistDeleteRequest(BaseModel):
    """Request body for whitelist deletion."""
    ids: list[int] | None = Field(None, description="按主键 ID 删除")
    bot_owner_pairs: list[WhitelistDeletePair] | None = Field(
        None, description="按 (bot_id, owner_id) 对删除",
    )
    dry_run: bool = Field(True, description="true=只统计不删除")
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
