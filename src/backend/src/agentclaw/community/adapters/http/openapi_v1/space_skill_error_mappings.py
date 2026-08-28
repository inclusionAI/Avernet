"""Space Skill control-plane mappings owned outside the shared response table."""

from agentclaw.community.adapters.http.openapi_v1.errors_space import (
    SpaceErrorCode,
    SpacePublicErrorMessage,
)
from agentclaw.community.core.skill_center.errors import (
    DraftEditLeaseConflictError,
    DraftEditLeaseForbiddenError,
    DraftEditLeaseNotFoundError,
    DraftEditLeaseTokenRejectedError,
    SpaceSkillGrantConflictError,
    SpaceSkillGrantForbiddenError,
    SpaceSkillGrantMemberRequiredError,
    SpaceSkillGrantNotFoundError,
    SpaceSkillGrantReasonRequiredError,
)

SPACE_SKILL_HTTP_ERRORS = {
    SpaceSkillGrantForbiddenError: (403, SpacePublicErrorMessage.SKILL_GRANT_FORBIDDEN),
    SpaceSkillGrantNotFoundError: (404, SpacePublicErrorMessage.SKILL_GRANT_NOT_FOUND),
    SpaceSkillGrantMemberRequiredError: (409, SpacePublicErrorMessage.SKILL_GRANT_MEMBER_REQUIRED),
    SpaceSkillGrantConflictError: (409, SpacePublicErrorMessage.SKILL_GRANT_CONFLICT),
    SpaceSkillGrantReasonRequiredError: (422, SpacePublicErrorMessage.SKILL_GRANT_REASON_REQUIRED),
    DraftEditLeaseForbiddenError: (403, SpacePublicErrorMessage.DRAFT_EDIT_LEASE_FORBIDDEN),
    DraftEditLeaseNotFoundError: (404, SpacePublicErrorMessage.DRAFT_EDIT_LEASE_NOT_FOUND),
    DraftEditLeaseConflictError: (409, SpacePublicErrorMessage.DRAFT_EDIT_LEASE_CONFLICT),
    DraftEditLeaseTokenRejectedError: (409, SpacePublicErrorMessage.DRAFT_EDIT_LEASE_TOKEN_REJECTED),
}

SPACE_SKILL_ERROR_CODES = {
    SpaceSkillGrantForbiddenError: SpaceErrorCode.SKILL_GRANT_FORBIDDEN,
    SpaceSkillGrantNotFoundError: SpaceErrorCode.SKILL_GRANT_NOT_FOUND,
    SpaceSkillGrantMemberRequiredError: SpaceErrorCode.SKILL_GRANT_MEMBER_REQUIRED,
    SpaceSkillGrantConflictError: SpaceErrorCode.SKILL_GRANT_CONFLICT,
    SpaceSkillGrantReasonRequiredError: SpaceErrorCode.SKILL_GRANT_REASON_REQUIRED,
    DraftEditLeaseForbiddenError: SpaceErrorCode.DRAFT_EDIT_LEASE_FORBIDDEN,
    DraftEditLeaseNotFoundError: SpaceErrorCode.DRAFT_EDIT_LEASE_NOT_FOUND,
    DraftEditLeaseConflictError: SpaceErrorCode.DRAFT_EDIT_LEASE_CONFLICT,
    DraftEditLeaseTokenRejectedError: SpaceErrorCode.DRAFT_EDIT_LEASE_TOKEN_REJECTED,
}
