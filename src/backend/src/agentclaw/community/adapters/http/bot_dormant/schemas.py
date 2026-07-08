"""Pydantic schemas for bot dormant / reactivation endpoints."""

from pydantic import BaseModel


class ActivateBotResponse(BaseModel):
    status: str   # 'REACTIVATING' | 'ACTIVE'
    message: str


class WhitelistEntry(BaseModel):
    bot_id: str
    owner_id: str
    governance_source: str = "manual"
    reason: str | None = None


class WhitelistBatchRequest(BaseModel):
    entries: list[WhitelistEntry]


class WhitelistBatchResponse(BaseModel):
    inserted: int
    skipped: int  # 已存在的


class ApiResponse(BaseModel):
    success: bool
    data: ActivateBotResponse | WhitelistBatchResponse | None = None
    message: str | None = None
    error_code: int | None = None


class PendingNotification(BaseModel):
    """Single row returned by GET /pending-notifications."""
    id: int
    bot_id: str
    entity_id: str | None = None
    notify_target: str | None = None
    notify_type: str          # 'warn' | 'recycle'
    notify_source: str        # 'internal_scan' | 'external_input'
    content: str
    enqueued_at: str | None = None  # ISO timestamp


class PendingNotificationsResponse(BaseModel):
    data: list[PendingNotification]
    total: int


class MarkSentRequest(BaseModel):
    id: int
    success: bool
    error_msg: str | None = None


class MarkSentResponse(BaseModel):
    ok: bool
    status: str   # 'sent' | 'failed' | 'already_resolved'


class OpsRecycleOneRequest(BaseModel):
    bot_id: str
    owner_id: str
    dry_run: bool = True
    reason: str | None = None


class OpsActivateOneRequest(BaseModel):
    bot_id: str
    owner_id: str
    nick_name: str | None = None
