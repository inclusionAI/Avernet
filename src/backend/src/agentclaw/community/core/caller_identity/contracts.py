"""Caller identity DTOs and stable domain errors."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agentclaw.community.core.caller_identity.models import McpCallType
from agentclaw.community.core.errors import Conflict, Forbidden, InternalError, NotFound


CALLER_IDENTITY_CAPABILITY = "caller_identity.v1"


class CallerIdentityStage(StrEnum):
    DRAFT = "draft"
    VERIFY = "verify"
    ONLINE = "online"


class CallerIdentityPermissionError(Forbidden):
    def __init__(self) -> None:
        super().__init__("CALLER_IDENTITY_FORBIDDEN")


class CallerIdentityNotFoundError(NotFound):
    def __init__(self) -> None:
        super().__init__("BOT_NOT_FOUND")


class CallerIdentityReadOnlyError(Conflict):
    def __init__(self) -> None:
        super().__init__("BOT_CONFIG_READ_ONLY")


class CallerLockEpochError(Conflict):
    def __init__(self) -> None:
        super().__init__("CALLER_LOCK_EPOCH_INVALID")


class CallerMcpNotFoundError(NotFound):
    def __init__(self) -> None:
        super().__init__("CALLER_MCP_NOT_FOUND")


class CallerMcpSyncError(InternalError):
    def __init__(self) -> None:
        super().__init__("CALLER_MCP_SYNC_FAILED")


class CallerCallTypeInvalidError(InternalError):
    def __init__(self) -> None:
        super().__init__("CALLER_CALL_TYPE_INVALID")


@dataclass(frozen=True, slots=True)
class DraftCallTypeMutationResult:
    previous_explicit_call_type: McpCallType | None
    bot_call_type: McpCallType
    revision: int


@dataclass(frozen=True, slots=True)
class DraftCallTypeCompensationResult:
    applied: bool
    bot_call_type: McpCallType
    revision: int


@dataclass(frozen=True, slots=True)
class McpCallTypeUpdateResult:
    server_code: str
    call_type: McpCallType
    bot_call_type: McpCallType


@dataclass(frozen=True, slots=True)
class CallerIamTokenContext:
    bot_id: str
    owner_id: str | None
    stage: CallerIdentityStage
    publish_id: int | None
    bot_call_type: McpCallType
    should_exchange_caller_token: bool


@dataclass(frozen=True, slots=True)
class CallerContext:
    capability: str
    stage: CallerIdentityStage
    publish_id: int | None
    bot_call_type: McpCallType
    mcp_call_types: dict[str, McpCallType]
    editable: bool


__all__ = [
    "CALLER_IDENTITY_CAPABILITY",
    "CallerCallTypeInvalidError",
    "CallerContext",
    "CallerIamTokenContext",
    "CallerIdentityNotFoundError",
    "CallerIdentityPermissionError",
    "CallerIdentityReadOnlyError",
    "CallerIdentityStage",
    "CallerLockEpochError",
    "CallerMcpNotFoundError",
    "CallerMcpSyncError",
    "DraftCallTypeCompensationResult",
    "DraftCallTypeMutationResult",
    "McpCallType",
    "McpCallTypeUpdateResult",
]
