"""
Publish management domain types.

Package mirrors the existing api/{domain}/ pattern:
- _enums.py: StrEnum classes
- _models.py: Pydantic models
- _exceptions.py: Domain exceptions
"""

from ._enums import (
    BatchStatus,
    PublishEventType,
    PublishRecordResult,
    PublishStage,
    PublishStatus,
    PublishType,
    RestartScope,
)
from ._exceptions import PublishConflictError, PublishNotFoundError
from ._models import (
    DEFAULT_CALLBACK_TIMEOUT_SECONDS,
    DEFAULT_PUBLISH_LEVEL_TIMEOUT_SECONDS,
    ApprovalAction,
    BatchDeviceProgress,
    BatchResult,
    DeviceCallbackRequest,
    DeviceOperationResult,
    DrainResult,
    ForceSuccessResult,
    ProgressSummary,
    ProgressTimeline,
    PublishBatchConfig,
    PublishBatchResponse,
    PublishConfig,
    PublishCreate,
    PublishListResponse,
    PublishProgressResponse,
    PublishRecordResponse,
    PublishResponse,
    StageConfig,
    StageProgress,
    UpdateDeviceStatusResult,
    serialize_hook_result,
)
from ._protocols import PublishAdminService, PublishService

__all__ = [
    "ApprovalAction",
    "ForceSuccessResult",
    "PublishAdminService",
    "PublishService",
    "UpdateDeviceStatusResult",
    "BatchDeviceProgress",
    "BatchResult",
    "BatchStatus",
    "DEFAULT_CALLBACK_TIMEOUT_SECONDS",
    "DEFAULT_PUBLISH_LEVEL_TIMEOUT_SECONDS",
    "DeviceCallbackRequest",
    "DeviceOperationResult",
    "DrainResult",
    "ProgressSummary",
    "ProgressTimeline",
    "PublishBatchConfig",
    "PublishBatchResponse",
    "PublishConfig",
    "PublishConflictError",
    "PublishCreate",
    "PublishEventType",
    "PublishListResponse",
    "PublishNotFoundError",
    "PublishProgressResponse",
    "PublishRecordResponse",
    "PublishRecordResult",
    "PublishResponse",
    "PublishStage",
    "PublishStatus",
    "PublishType",
    "RestartScope",
    "StageConfig",
    "StageProgress",
    "serialize_hook_result",
]
