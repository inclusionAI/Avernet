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
from agentclaw.community.core.service_bot.errors import (
    ServicePublicationGateBlockedError,
    ServicePublicationGatePendingError,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillInvalidPackageError,
    LocalSkillTooLargeError,
    SkillAssetInUseError,
    SkillOfflineBlockedError,
)


def error_data(exc: Exception) -> object | None:
    """Return caller-actionable error data for explicitly admitted types."""
    if isinstance(exc, LocalSkillInvalidPackageError):
        reasons = {
            "invalid_package",
            "invalid_zip",
            "unsafe_file_path",
            "duplicate_file_path",
            "unreadable_archive",
            "missing_skill_file",
            "multiple_skill_files",
            "invalid_wrapper",
            "invalid_encoding",
            "invalid_metadata",
            "wrapper_name_mismatch",
        }
        details = {
            "MISSING_FRONTMATTER",
            "INVALID_FRONTMATTER",
            "INVALID_ENCODING",
            "MISSING_NAME",
            "MISSING_DESCRIPTION",
            "INVALID_NAME_TYPE",
            "INVALID_DESCRIPTION_TYPE",
            "EMPTY_NAME",
            "EMPTY_DESCRIPTION",
            "NAME_TOO_LONG",
            "DESCRIPTION_TOO_LONG",
            "INVALID_NAME",
            "RESERVED_NAME",
        }
        reason = (
            exc.detail
            if exc.reason == "invalid_metadata" and exc.detail in details
            else exc.reason
        )
        return {
            "reason": reason
            if reason in reasons or reason in details
            else "invalid_package"
        }
    if isinstance(exc, LocalSkillTooLargeError):
        reasons = {
            "file_too_large",
            "too_many_files",
            "expanded_too_large",
            "compressed_too_large",
        }
        return {"reason": exc.reason if exc.reason in reasons else "too_large"}
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
    if isinstance(
        exc, ServicePublicationGateBlockedError | ServicePublicationGatePendingError
    ):
        return gate_error_data(exc)
    if isinstance(exc, AcliCliNameMismatchError):
        return {"market_name": exc.market_name}
    if isinstance(exc, AcliOperationInProgressError):
        op = exc.operation
        return (
            {"operation": acli_operation_payload(op)}
            if isinstance(op, AcliOperation)
            else None
        )
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


def gate_error_data(
    exc: ServicePublicationGateBlockedError | ServicePublicationGatePendingError,
) -> dict[str, object]:
    """Expose only the stable, UI-actionable gate projection."""
    return dict(exc.gate_result)
