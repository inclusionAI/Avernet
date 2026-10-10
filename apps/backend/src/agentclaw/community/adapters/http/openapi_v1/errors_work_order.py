"""Stable OpenAPI error codes and fixed public messages for work orders."""

from enum import IntEnum, StrEnum

from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderAlreadyPendingError,
    WorkOrderAlreadyProcessedError,
    WorkOrderCallbackError,
    WorkOrderLocalFinalizeError,
    WorkOrderApplicantAlreadyEditorError,
    WorkOrderApplicantAlreadyMemberError,
    WorkOrderBotEditorRequestNotAllowedError,
    WorkOrderSkillEditorRequestNotAllowedError,
    WorkOrderSkillApplicantAlreadyEditorError,
    WorkOrderInvalidReasonError,
    WorkOrderInvalidEventError,
    WorkOrderInvalidRemarkError,
    WorkOrderJoinNotAllowedError,
    WorkOrderNoReviewerError,
    WorkOrderNotFoundError,
    WorkOrderNotificationNotFoundError,
)


class WorkOrderErrorCode(IntEnum):
    INVALID_REASON = 400201
    INVALID_REMARK = 400202
    ACCESS_DENIED = 403201
    NOT_FOUND = 404201
    NOTIFICATION_NOT_FOUND = 404202
    ALREADY_PENDING = 409201
    ALREADY_PROCESSED = 409202
    APPLICANT_ALREADY_MEMBER = 409203
    NO_REVIEWER = 409204
    JOIN_NOT_ALLOWED = 409205
    APPLICANT_ALREADY_EDITOR = 409206
    BOT_EDITOR_REQUEST_NOT_ALLOWED = 409207
    SKILL_EDITOR_REQUEST_NOT_ALLOWED = 409208
    SKILL_APPLICANT_ALREADY_EDITOR = 409209
    CALLBACK_FAILED = 502201
    LOCAL_FINALIZE_FAILED = 500201


class WorkOrderPublicErrorMessage(StrEnum):
    INVALID_REASON = "Invalid application reason"
    INVALID_REMARK = "Invalid review remark"
    FORBIDDEN = "Forbidden"
    NOT_FOUND = "Not found"
    ALREADY_PENDING = "A pending application already exists"
    ALREADY_PROCESSED = "The work order has already been processed"
    APPLICANT_ALREADY_MEMBER = "Applicant is already a space member"
    NO_REVIEWER = "The space has no available approver"
    JOIN_NOT_ALLOWED = "The space does not accept join requests"
    APPLICANT_ALREADY_EDITOR = "Applicant already has Bot editor access"
    BOT_EDITOR_REQUEST_NOT_ALLOWED = "The Bot does not accept editor requests"
    SKILL_EDITOR_REQUEST_NOT_ALLOWED = "The Skill does not accept editor requests"
    SKILL_APPLICANT_ALREADY_EDITOR = "Applicant already has Skill editor access"
    CALLBACK_FAILED = "Upstream work-order callback failed"
    LOCAL_FINALIZE_FAILED = (
        "External decision completed; local result persistence failed. "
        "Reconcile before retrying."
    )


WORK_ORDER_ERROR_STATUS = {
    WorkOrderAccessDeniedError: (403, WorkOrderPublicErrorMessage.FORBIDDEN),
    WorkOrderNotFoundError: (404, WorkOrderPublicErrorMessage.NOT_FOUND),
    WorkOrderNotificationNotFoundError: (
        404,
        WorkOrderPublicErrorMessage.NOT_FOUND,
    ),
    WorkOrderInvalidEventError: (400, "Invalid work-order event"),
    WorkOrderInvalidReasonError: (
        400,
        WorkOrderPublicErrorMessage.INVALID_REASON,
    ),
    WorkOrderInvalidRemarkError: (
        400,
        WorkOrderPublicErrorMessage.INVALID_REMARK,
    ),
    WorkOrderAlreadyPendingError: (
        409,
        WorkOrderPublicErrorMessage.ALREADY_PENDING,
    ),
    WorkOrderAlreadyProcessedError: (
        409,
        WorkOrderPublicErrorMessage.ALREADY_PROCESSED,
    ),
    WorkOrderCallbackError: (502, WorkOrderPublicErrorMessage.CALLBACK_FAILED),
    WorkOrderApplicantAlreadyMemberError: (
        409,
        WorkOrderPublicErrorMessage.APPLICANT_ALREADY_MEMBER,
    ),
    WorkOrderApplicantAlreadyEditorError: (
        409,
        WorkOrderPublicErrorMessage.APPLICANT_ALREADY_EDITOR,
    ),
    WorkOrderJoinNotAllowedError: (
        409,
        WorkOrderPublicErrorMessage.JOIN_NOT_ALLOWED,
    ),
    WorkOrderBotEditorRequestNotAllowedError: (
        409,
        WorkOrderPublicErrorMessage.BOT_EDITOR_REQUEST_NOT_ALLOWED,
    ),
    WorkOrderSkillEditorRequestNotAllowedError: (
        409,
        WorkOrderPublicErrorMessage.SKILL_EDITOR_REQUEST_NOT_ALLOWED,
    ),
    WorkOrderSkillApplicantAlreadyEditorError: (
        409,
        WorkOrderPublicErrorMessage.SKILL_APPLICANT_ALREADY_EDITOR,
    ),
    WorkOrderNoReviewerError: (
        409,
        WorkOrderPublicErrorMessage.NO_REVIEWER,
    ),
    WorkOrderLocalFinalizeError: (
        500,
        WorkOrderPublicErrorMessage.LOCAL_FINALIZE_FAILED,
    ),
}

WORK_ORDER_ERROR_CODES = {
    WorkOrderInvalidEventError: WorkOrderErrorCode.INVALID_REASON,
    WorkOrderInvalidReasonError: WorkOrderErrorCode.INVALID_REASON,
    WorkOrderInvalidRemarkError: WorkOrderErrorCode.INVALID_REMARK,
    WorkOrderAccessDeniedError: WorkOrderErrorCode.ACCESS_DENIED,
    WorkOrderNotFoundError: WorkOrderErrorCode.NOT_FOUND,
    WorkOrderNotificationNotFoundError: WorkOrderErrorCode.NOTIFICATION_NOT_FOUND,
    WorkOrderAlreadyPendingError: WorkOrderErrorCode.ALREADY_PENDING,
    WorkOrderAlreadyProcessedError: WorkOrderErrorCode.ALREADY_PROCESSED,
    WorkOrderCallbackError: WorkOrderErrorCode.CALLBACK_FAILED,
    WorkOrderApplicantAlreadyMemberError: WorkOrderErrorCode.APPLICANT_ALREADY_MEMBER,
    WorkOrderApplicantAlreadyEditorError: WorkOrderErrorCode.APPLICANT_ALREADY_EDITOR,
    WorkOrderNoReviewerError: WorkOrderErrorCode.NO_REVIEWER,
    WorkOrderJoinNotAllowedError: WorkOrderErrorCode.JOIN_NOT_ALLOWED,
    WorkOrderBotEditorRequestNotAllowedError: WorkOrderErrorCode.BOT_EDITOR_REQUEST_NOT_ALLOWED,
    WorkOrderSkillEditorRequestNotAllowedError: WorkOrderErrorCode.SKILL_EDITOR_REQUEST_NOT_ALLOWED,
    WorkOrderSkillApplicantAlreadyEditorError: WorkOrderErrorCode.SKILL_APPLICANT_ALREADY_EDITOR,
    WorkOrderLocalFinalizeError: WorkOrderErrorCode.LOCAL_FINALIZE_FAILED,
}
