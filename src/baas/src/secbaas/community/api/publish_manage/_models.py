"""Publish workflow models and enums for baas_publish, baas_publish_batch, baas_publish_record tables.

Corresponding tables:
- baas_publish: Main publish tracking
- baas_publish_batch: Batches within a publish
- baas_publish_record: Individual device operations

Per decisions D-01 to D-06: Supports multi-stage deployment pipelines with approval gates.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass
class ForceSuccessResult:
    """Result of a force-success operation."""

    publish_id: int
    previous_publish_status: str
    batches_updated: int
    records_updated: int
    devices_updated: int
    bot_updated: bool


@dataclass
class UpdateDeviceStatusResult:
    """Result of a device status update operation."""

    device_uuid: str
    previous_status: str
    new_status: str


from secbaas.community.api.device_manage import (  # noqa: E402 — cyclic import guard
    DeployConfig,
)

from ._enums import PublishType, RestartScope  # noqa: E402 — cyclic import guard

# ==================== Constants ====================

DEFAULT_CALLBACK_TIMEOUT_SECONDS = 1800
"""Device callback timeout in seconds."""

DEFAULT_PUBLISH_LEVEL_TIMEOUT_SECONDS = 1800
"""Maximum time a publish can stay in non-terminal state before auto-resolution.

When a publish is stuck in PENDING/ACTIVE/APPROVING for longer than this
threshold (based on gmt_modified), it is considered timed out and can be
auto-resolved (marked FAILED) to unblock subsequent publish operations.
"""


# ==================== Pydantic Models ====================


class StageConfig(BaseModel):
    """Configuration for a single pipeline stage per D-01 extra_config spec."""

    device_count: int = Field(
        default=0, ge=0, description="Number of devices for this stage"
    )
    batch_capacity: int = Field(default=5, ge=1, description="Devices per batch")
    cooldown_seconds: int = Field(
        default=0, ge=0, description="Seconds between batches"
    )
    pause_for_approval: bool = Field(
        default=False, description="Pause for approval after this stage"
    )


class PublishConfig(BaseModel):
    """Complete publish configuration for extra_config JSON field.

    Per D-04: drain_timeout_seconds default is 0 seconds.
    Per CONTEXT.md stage config JSON structure.
    Caller-specific keys are injected by the service layer per publish type.
    """

    model_config = ConfigDict(extra="allow")

    # Existing typed config
    stages: dict[str, StageConfig] = Field(
        default_factory=dict, description="Per-stage configuration"
    )
    drain_timeout_seconds: int = Field(
        default=0, ge=0, le=3600, description="Max wait for session drain"
    )
    batch_capacity_default: int = Field(
        default=5, ge=1, le=100, description="Default devices per batch"
    )
    auto_complete: bool = Field(
        default=True, description="Auto-complete publish when all batches succeed"
    )
    auto_execute_max_iterations: int = Field(
        default=20, ge=1, le=100, description="Max iterations for auto-execute loop"
    )
    callback_timeout_seconds: int = Field(
        default=DEFAULT_CALLBACK_TIMEOUT_SECONDS,
        gt=0,
        le=3600,
        description="Max seconds to wait for device callback before marking record as failed",
    )

    # Caller-specific keys (injected per publish type)
    bot_name: str | None = None
    replica_desired: int | None = None
    batch_capacity: int | None = None
    cooldown_seconds: int | None = None
    reason: str | None = None
    restart_scope: RestartScope | None = None
    restart_reason: str | None = None

    # Deploy config (passed through for device provisioning)
    deploy_config: DeployConfig | None = None

    # UPDATE-specific: ID of the new PENDING bot record created during create_publish
    target_bot_id: int | None = None

    # UPDATE_DEVICE-specific: explicit list of device UUIDs to update
    target_device_uuids: list[str] | None = None

    # auto_approve: when True, manual /approve calls are rejected; only internal loop drives gates
    auto_approve: bool = False

    @classmethod
    def get_defaults_for_type(cls, publish_type: PublishType) -> dict[str, Any]:
        """Get default stage configuration for publish type per D-01a.

        Stage Gate Behavior:
        ┌──────────────────┬──────────────────────────────────────────────────────────┐
        │ PublishType      │ Stages with pause_for_approval=True                      │
        ├──────────────────┼──────────────────────────────────────────────────────────┤
        │ CREATE/UPDATE    │ PREPUB, GRAY, PROD_FIRST_BATCH (3 gates)                 │
        │ RESTART          │ PROD_FIRST_BATCH (1 gate)                                │
        │ SCALE_UP/DOWN    │ None (direct execution, no gates)                        │
        │ DESTROY/STOP     │ None (direct execution, no gates)                        │
        │ UPDATE_DEVICE    │ None (direct execution, no gates)                        │
        └──────────────────┴──────────────────────────────────────────────────────────┘

        Stage Gate Execution Flow:
        1. After execute_stage() completes, check if more batches exist
        2. If more batches AND current stage has pause_for_approval=True:
           → Transition to APPROVING status, await approve_publish()
        3. If more batches AND pause_for_approval=False:
           → Continue to next batch (auto-execute)
        4. If no more batches:
           → Transition to SUCCESS status

        Auto-Compact Logic (applied in _generate_batches):
        - When device_count < stage_count, stages are reduced to match device count
        - 1 device → 1 stage (PROD_FIRST_BATCH only)
        - 2 devices → 2 stages (last 2 stages from pipeline)
        - 3+ devices → full pipeline or device count stages
        - SCALE types: no auto-compact, uses batch_capacity instead

        Args:
            publish_type: Type of publish operation

        Returns:
            Dictionary with stages config and drain_timeout_seconds
        """
        if publish_type in (PublishType.CREATE, PublishType.UPDATE):
            return {
                "stages": {
                    "PREPUB": {
                        "device_count": 2,
                        "batch_capacity": 2,
                        "cooldown_seconds": 0,
                        "pause_for_approval": True,
                    },
                    "GRAY": {
                        "device_count": 4,
                        "batch_capacity": 2,
                        "cooldown_seconds": 0,
                        "pause_for_approval": True,
                    },
                    "PROD_FIRST_BATCH": {
                        "batch_capacity": 5,
                        "cooldown_seconds": 0,
                        "pause_for_approval": True,
                    },
                    "PROD_OTHER_BATCH": {
                        "batch_capacity": 5,
                        "cooldown_seconds": 0,
                        "pause_for_approval": False,
                    },
                },
                "drain_timeout_seconds": 0,
            }
        elif publish_type == PublishType.RESTART:
            return {
                "stages": {
                    "PROD_FIRST_BATCH": {
                        "batch_capacity": 5,
                        "cooldown_seconds": 0,
                        "pause_for_approval": True,
                    },
                    "PROD_OTHER_BATCH": {
                        "batch_capacity": 5,
                        "cooldown_seconds": 0,
                        "pause_for_approval": False,
                    },
                },
                "drain_timeout_seconds": 0,
            }
        elif publish_type in (PublishType.SCALE_UP, PublishType.SCALE_DOWN):
            return {
                "stages": {"direct": {"batch_capacity": 10, "cooldown_seconds": 0}},
                "drain_timeout_seconds": 0,
            }
        elif publish_type in (PublishType.DESTROY, PublishType.STOP):
            return {
                "stages": {"direct": {"batch_capacity": 10, "cooldown_seconds": 0}},
                "drain_timeout_seconds": 0,
            }
        elif publish_type == PublishType.UPDATE_DEVICE:
            return {
                "stages": {"direct": {"batch_capacity": 10, "cooldown_seconds": 0}},
                "drain_timeout_seconds": 0,
            }


class PublishBatchConfig(BaseModel):
    """Typed model for baas_publish_batch.extra_config JSON column.

    Replaces .get() property pattern on PublishBatchRecord.
    """

    model_config = ConfigDict(extra="allow")

    stage: str = Field(default="UNKNOWN", description="Pipeline stage for this batch")
    cooldown_seconds: int = Field(
        default=0, ge=0, description="Cooldown seconds after batch"
    )
    device_count: int | None = Field(
        default=None, description="Number of devices in batch"
    )


class PublishCreate(BaseModel):
    """Create publish request parameters."""

    bot_id: int = Field(..., gt=0, description="Bot ID to publish")
    publish_type: PublishType = Field(..., description="Type of publish operation")
    operator: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Operator user ID",
    )
    extra_config: PublishConfig = Field(
        default_factory=PublishConfig,
        description="Optional stage/drain config override",
    )


class PublishResponse(BaseModel):
    """Publish response with current status."""

    id: int = Field(..., description="Internal publish ID")
    bot_id: int = Field(..., description="Bot ID")
    publish_type: str = Field(..., description="Type of publish")
    status: str = Field(..., description="Current lifecycle status")
    stage: str | None = Field(default=None, description="Current pipeline stage")
    extra_config: PublishConfig | None = Field(
        default=None, description="Stage configuration and drain settings"
    )
    creator: str = Field(..., description="Creator user ID")
    modifier: str = Field(..., description="Last modifier user ID")
    gmt_create: datetime = Field(..., description="Creation timestamp")
    gmt_modified: datetime = Field(..., description="Last modification timestamp")
    request_id: str = Field(default="", description="Request ID for correlation")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class BotPublishSummary(BaseModel):
    """Lightweight publish descriptor for the by-bot listing.

    Carries only what an idempotency/reconciliation caller needs to difference
    a bot's publish workflows (id, type, status, creation time) — no per-row
    stage query or full config, so listing a bot's whole publish history stays
    cheap.
    """

    id: int = Field(..., description="Internal publish ID (the workflow id)")
    bot_id: int = Field(..., description="Bot ID this publish belongs to")
    publish_type: str = Field(..., description="Type of publish")
    status: str = Field(..., description="Current lifecycle status")
    gmt_create: datetime = Field(..., description="Creation timestamp")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class PublishBatchResponse(BaseModel):
    """Publish batch information."""

    id: int = Field(..., description="Internal batch ID")
    publish_id: int = Field(..., description="Parent publish ID")
    batch_index: int = Field(..., ge=0, description="Index within publish")
    batch_capacity: int = Field(..., ge=1, description="Devices in this batch")
    stage: str = Field(..., description="Pipeline stage for this batch")
    cooldown_seconds: int = Field(..., ge=0, description="Cooldown after batch")
    status: str = Field(..., description="Batch execution status")
    creator: str = Field(..., description="Create user ID")
    modifier: str = Field(..., description="Last modifier user ID")
    gmt_create: datetime = Field(..., description="Creation timestamp")
    gmt_modified: datetime = Field(..., description="Last modification timestamp")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class PublishRecordResponse(BaseModel):
    """Individual device operation record response."""

    id: int = Field(..., description="Internal record ID")
    batch_id: int = Field(..., description="Parent batch ID")
    device_id: int | None = Field(default=None, description="Affected device ID")
    event_type: str = Field(..., description="Type of operation")
    status: str = Field(..., description="Operation status")
    old_device_id: int | None = Field(
        default=None, description="Previous device ID (for UPDATE)"
    )
    new_device_id: int | None = Field(
        default=None, description="New device ID (for CREATE/UPDATE)"
    )
    err_msg: str | None = Field(default=None, description="Error message if failed")
    creator: str = Field(..., description="Creator user ID")
    modifier: str = Field(..., description="Last modifier user ID")
    gmt_create: datetime = Field(..., description="Creation timestamp")
    gmt_modified: datetime = Field(..., description="Last modification timestamp")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class PublishListResponse(BaseModel):
    """Paginated publish list response."""

    items: list[PublishResponse]
    total: int = Field(..., ge=0, description="Total matching records")
    page: int = Field(..., ge=1, description="Current page number")
    page_size: int = Field(..., ge=1, description="Items per page")


class ApprovalAction(BaseModel):
    """Approval action request."""

    action: str = Field(
        ..., pattern="^(approve|reject|revoke)$", description="Approval action"
    )
    operator: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="User performing the action",
    )
    comment: str | None = Field(
        default=None,
        max_length=512,
        description="Optional comment/reason",
    )


class BatchResult(BaseModel):
    """Result of batch execution."""

    success: bool = Field(..., description="Whether batch completed successfully")
    processed_count: int = Field(..., ge=0, description="Devices processed")
    failed_count: int = Field(..., ge=0, description="Devices failed")
    error_message: str | None = Field(default=None, description="Error summary")


class DrainResult(BaseModel):
    """Result of device drain operation."""

    success: bool = Field(..., description="Whether drain completed normally")
    sessions_remaining: int = Field(
        ..., ge=0, description="Active sessions at end (0 if drained)"
    )
    duration_seconds: float = Field(..., ge=0, description="Actual drain duration")
    timeout_reached: bool = Field(
        default=False, description="Whether timeout was reached"
    )


class ProgressSummary(BaseModel):
    """Overall progress statistics for a publish operation."""

    total_batches: int = Field(..., ge=0, description="Total number of batches")
    completed_batches: int = Field(..., ge=0, description="Number of completed batches")
    total_devices: int = Field(..., ge=0, description="Total devices to process")
    processed_devices: int = Field(..., ge=0, description="Devices processed so far")
    failed_devices: int = Field(..., ge=0, description="Devices that failed")
    progress_percentage: float = Field(
        ..., ge=0.0, le=100.0, description="Overall progress percentage (0-100)"
    )


class StageProgress(BaseModel):
    """Progress statistics for a single pipeline stage."""

    stage: str = Field(..., description="Stage name (PREPUB, GRAY, etc.)")
    status: str = Field(
        ..., description="Stage status (PENDING, ACTIVE, SUCCESS, FAILED)"
    )
    batches_completed: int = Field(
        ..., ge=0, description="Batches completed in this stage"
    )
    batches_total: int = Field(..., ge=0, description="Total batches in this stage")
    devices_processed: int = Field(
        ..., ge=0, description="Devices processed in this stage"
    )
    devices_failed: int = Field(..., ge=0, description="Devices failed in this stage")
    devices_total: int = Field(..., ge=0, description="Total devices in this stage")


class DeviceOperationResult(BaseModel):
    """Detailed result for a single device operation."""

    device_id: int | None = Field(default=None, description="Device ID")
    device_uuid: str | None = Field(default=None, description="Device UUID")
    event_type: str = Field(
        ..., description="Operation type (CREATE, UPDATE, DESTROY, RESTART)"
    )
    result_status: str = Field(
        ..., description="Result status (SUCCESS, FAILED, PENDING)"
    )
    result_message: str | None = Field(
        default=None, description="Error message if failed"
    )
    old_device_id: int | None = Field(
        default=None, description="Previous device ID (for UPDATE operations)"
    )
    new_device_id: int | None = Field(
        default=None, description="New device ID (for CREATE/UPDATE operations)"
    )
    gmt_create: datetime = Field(..., description="Operation timestamp")

    model_config = ConfigDict(from_attributes=True)


class BatchDeviceProgress(BaseModel):
    """Device-level progress for a single batch."""

    batch_id: int = Field(..., description="Batch ID")
    batch_index: int = Field(..., description="Batch index within publish")
    stage: str = Field(..., description="Stage this batch belongs to")
    status: str = Field(
        ...,
        description="Batch status (PENDING, RUNNING, COMPLETED, FAILED, ROLLED_BACK)",
    )
    devices: list[DeviceOperationResult] = Field(
        default_factory=list, description="Device operation results in this batch"
    )


class ProgressTimeline(BaseModel):
    """Timeline information for a publish operation."""

    gmt_create: datetime = Field(..., description="Publish creation time")
    gmt_modified: datetime = Field(..., description="Last modification time")
    estimated_remaining_seconds: float | None = Field(
        default=None, ge=0, description="Estimated remaining time (null if not ACTIVE)"
    )


class PublishProgressResponse(BaseModel):
    """Detailed progress information for a publish operation."""

    publish_id: int = Field(..., description="Publish ID")
    status: str = Field(..., description="Publish lifecycle status")
    current_stage: str | None = Field(
        default=None, description="Current pipeline stage"
    )
    overall_progress: ProgressSummary = Field(
        ..., description="Overall progress statistics"
    )
    stages: list[StageProgress] = Field(
        default_factory=list, description="Per-stage progress breakdown"
    )
    timeline: ProgressTimeline = Field(..., description="Timeline information")
    device_details: list[BatchDeviceProgress] = Field(
        default_factory=list,
        description="Detailed device operation results per batch (empty if detail_level='summary')",
    )
    failed_devices: list[DeviceOperationResult] = Field(
        default_factory=list,
        description="List of failed device operations for retry",
    )

    model_config = ConfigDict(from_attributes=True)


# ==============================================================================
# Device Callback Models & Helpers
# ==============================================================================


class DeviceCallbackRequest(BaseModel):
    """Request model for device callback endpoint.

    Receives start hook execution results from async workers or external systems.
    Field names align with CommandResult model: exit_code, stdout, stderr.
    """

    device_uuid: str = Field(..., description="Device UUID")
    publish_id: int = Field(..., gt=0, description="Publish ID for lookup")
    event_type: Literal["start", "stop"] = Field(
        ..., description="Which hook completed (currently only 'start' is processed)"
    )
    result_status: str = Field(
        ...,
        description="Result status - case-insensitive 'success'/'SUCCESS' or 'failed'/'FAILED', normalized via .upper() internally",
    )
    exit_code: int | None = Field(default=None, description="Hook exit code")
    stdout: str | None = Field(default=None, description="Hook stdout")
    stderr: str | None = Field(default=None, description="Hook stderr")
    tenant: str = Field(..., description="Tenant name for isolation")

    model_config = ConfigDict(populate_by_name=True)


_RESULT_MESSAGE_MAX = 4096
_STDERR_BUDGET = 512
_TRUNCATED_SUFFIX = "[truncated]"


def serialize_hook_result(
    exit_code: int | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
    message: str | None = None,
) -> str:
    """Pack hook execution details into JSON for result_message varchar(4096).

    Truncation strategy:
    - JSON skeleton + exit_code + message ~100 bytes
    - stderr budget ~512 bytes
    - stdout gets remaining ~3400 bytes
    - Overflow truncates and appends "[truncated]"

    Consumers check first char '{' to detect JSON; old records are plain text.
    """
    import json

    truncated_suffix = _TRUNCATED_SUFFIX

    # Build initial JSON to measure overhead
    skeleton = {
        "exit_code": exit_code,
        "stdout": "",
        "stderr": "",
        "message": message or "",
    }
    overhead = len(json.dumps(skeleton, ensure_ascii=False))

    budget = _RESULT_MESSAGE_MAX - overhead
    stderr_budget = min(_STDERR_BUDGET, budget // 2)
    stdout_budget = budget - stderr_budget

    def _truncate(s: str | None, budget: int) -> str | None:
        if s is None:
            return None
        if len(s) <= budget:
            return s
        keep = budget - len(truncated_suffix)
        if keep < 0:
            keep = 0
        return s[:keep] + truncated_suffix

    result = {
        "exit_code": exit_code,
        "stdout": _truncate(stdout, stdout_budget),
        "stderr": _truncate(stderr, stderr_budget),
        "message": message or "",
    }
    serialized = json.dumps(result, ensure_ascii=False)

    # Final safety: if still over budget (edge case), truncate the whole thing
    if len(serialized) > _RESULT_MESSAGE_MAX:
        return (
            serialized[: _RESULT_MESSAGE_MAX - len(truncated_suffix)] + truncated_suffix
        )

    return serialized
