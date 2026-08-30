"""HTTP error mappings for the Space Skill collaboration control plane."""

from agentclaw.community.adapters.http.openapi_v1.errors_space import (
    SpaceErrorCode,
    SpacePublicErrorMessage,
)
from agentclaw.community.core.skill_center.errors import (
    DraftFileNotFoundError,
    DraftFileNotTextError,
    DraftFrozenError,
    DraftNotFoundError,
    DraftRevisionConflictError,
    SkillNameChangedError,
    SpaceSkillIdempotencyConflictError,
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
from agentclaw.community.core.skill_center.draft_content import DraftContentStoreError
from agentclaw.community.core.skill_center.git_snapshot import (
    GitSnapshotError,
    GitSnapshotInvalidError,
)
from agentclaw.community.core.skill_center.skill_package import (
    SkillPackageInvalidError,
    SkillPackageTooLargeError,
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
    SpaceSkillIdempotencyConflictError: (409, SpacePublicErrorMessage.IDEMPOTENCY_KEY_REUSED),
    DraftNotFoundError: (404, SpacePublicErrorMessage.DRAFT_NOT_FOUND),
    DraftFileNotFoundError: (404, SpacePublicErrorMessage.DRAFT_NOT_FOUND),
    DraftFileNotTextError: (422, SpacePublicErrorMessage.SKILL_PACKAGE_INVALID),
    DraftFrozenError: (409, SpacePublicErrorMessage.DRAFT_FROZEN),
    DraftRevisionConflictError: (409, SpacePublicErrorMessage.DRAFT_REVISION_CONFLICT),
    SkillNameChangedError: (422, SpacePublicErrorMessage.SKILL_NAME_CHANGED),
    SkillPackageInvalidError: (422, SpacePublicErrorMessage.SKILL_PACKAGE_INVALID),
    SkillPackageTooLargeError: (422, SpacePublicErrorMessage.SKILL_PACKAGE_INVALID),
    GitSnapshotInvalidError: (422, SpacePublicErrorMessage.SKILL_PACKAGE_INVALID),
    GitSnapshotError: (502, SpacePublicErrorMessage.SKILL_GIT_UNAVAILABLE),
    DraftContentStoreError: (503, SpacePublicErrorMessage.SKILL_DRAFT_STORE_UNAVAILABLE),
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
    SpaceSkillIdempotencyConflictError: SpaceErrorCode.IDEMPOTENCY_KEY_REUSED,
    DraftNotFoundError: SpaceErrorCode.DRAFT_NOT_FOUND,
    DraftFileNotFoundError: SpaceErrorCode.DRAFT_NOT_FOUND,
    DraftFileNotTextError: SpaceErrorCode.SKILL_PACKAGE_INVALID,
    DraftFrozenError: SpaceErrorCode.DRAFT_FROZEN,
    DraftRevisionConflictError: SpaceErrorCode.DRAFT_REVISION_CONFLICT,
    SkillNameChangedError: SpaceErrorCode.SKILL_NAME_CHANGED,
    SkillPackageInvalidError: SpaceErrorCode.SKILL_PACKAGE_INVALID,
    SkillPackageTooLargeError: SpaceErrorCode.SKILL_PACKAGE_INVALID,
    GitSnapshotInvalidError: SpaceErrorCode.SKILL_PACKAGE_INVALID,
    GitSnapshotError: SpaceErrorCode.SKILL_GIT_UNAVAILABLE,
    DraftContentStoreError: SpaceErrorCode.SKILL_DRAFT_STORE_UNAVAILABLE,
}
