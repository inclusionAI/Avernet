"""Structured data carried by the small set of actionable public errors."""

from agentclaw.community.api.acli_cli_service import (
    AcliCliNameMismatchError,
    AcliContainerError,
    AcliOperation,
    AcliOperationInProgressError,
)
from agentclaw.community.api.bot_config_manifest_service import (
    ManifestValidationError,
)
from agentclaw.community.adapters.http.openapi_v1.errors_genome import genome_error_data
from agentclaw.community.core.bot_management.bot_quota import (
    BotQuotaExceededError,
)
from agentclaw.community.core.bot_management.errors import (
    BotCreationRetainedError,
)
from agentclaw.community.core.skill_center.errors import (
    SkillAssetInUseError,
    SkillOfflineBlockedError,
)


def error_data(exc: Exception) -> object | None:
    """Return caller-actionable error data for explicitly admitted types."""
    if isinstance(exc, SkillOfflineBlockedError):
        return exc.impact
    if isinstance(exc, SkillAssetInUseError):
        return {"blockers": exc.blocker_counts}
    if isinstance(exc, ManifestValidationError):
        return exc.as_payload()
    if isinstance(exc, BotQuotaExceededError):
        return exc.as_payload()
    if isinstance(exc, BotCreationRetainedError):
        return {"bot_id": exc.bot_id, "retryable": True}
    if isinstance(exc, AcliContainerError):
        return {"code": exc.code, "reason": exc.detail}
    if isinstance(exc, AcliCliNameMismatchError):
        return {"market_name": exc.market_name}
    if isinstance(exc, AcliOperationInProgressError):
        op = exc.operation
        return {"operation": acli_operation_payload(op)} if isinstance(op, AcliOperation) else None
    return genome_error_data(exc)


def acli_operation_payload(op: AcliOperation) -> dict[str, object]:
    """An acli operation as the public API publishes it."""
    return {
        "operation_id": op.id,
        "kind": op.kind.value,
        "action": op.action.value,
        "cli_code": op.cli_code,
        "stage": op.stage.value,
        "status": op.status.value,
        "step": op.step.value if op.step is not None else None,
        "error_code": op.error_code,
        "error_message": op.error_message,
    }
