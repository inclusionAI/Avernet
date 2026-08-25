"""Stable OpenAPI error codes and fixed public messages for work orders."""

from enum import IntEnum, StrEnum


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
