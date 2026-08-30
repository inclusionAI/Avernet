"""HTTP error mappings for the Space Skill collaboration control plane."""

from agentclaw.community.adapters.http.openapi_v1.errors_space import (
    SpaceErrorCode,
    SpacePublicErrorMessage,
)
from agentclaw.community.core.skill_center.errors import (
    DraftFileNotFoundError,
    DraftFileNotTextError,
    DraftFrozenError,
    DraftAlreadyExistsError,
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
from agentclaw.community.plugin_api.space_skill_source import (
    ExactSkillPackageFetchError,
    GitSnapshotError,
    GitSnapshotInvalidError,
)
from agentclaw.community.plugin_api.skill_center_gateway import SkillCenterGatewayError
from agentclaw.community.core.skill_center.skill_package import (
    SkillManifestMissingError,
    SkillManifestMultipleError,
    SkillPackageInvalidError,
    SkillPackageTooLargeError,
    SkillPathInvalidError,
)

SPACE_SKILL_HTTP_ERRORS = {
    ExactSkillPackageFetchError: (502, SpacePublicErrorMessage.SC_MARKET_UNAVAILABLE),
    SkillCenterGatewayError: (502, SpacePublicErrorMessage.SC_MARKET_UNAVAILABLE),
    SkillManifestMissingError: (422, SpacePublicErrorMessage.SKILL_MANIFEST_MISSING),
    SkillManifestMultipleError: (422, SpacePublicErrorMessage.SKILL_MANIFEST_MULTIPLE),
    SkillPathInvalidError: (422, SpacePublicErrorMessage.SKILL_PATH_INVALID),
    SpaceSkillGrantForbiddenError: (403, SpacePublicErrorMessage.SKILL_GRANT_FORBIDDEN),
    SpaceSkillGrantNotFoundError: (404, SpacePublicErrorMessage.SKILL_GRANT_NOT_FOUND),
    SpaceSkillGrantMemberRequiredError: (
        409,
        SpacePublicErrorMessage.SKILL_GRANT_MEMBER_REQUIRED,
    ),
    SpaceSkillGrantConflictError: (409, SpacePublicErrorMessage.SKILL_GRANT_CONFLICT),
    SpaceSkillGrantReasonRequiredError: (
        422,
        SpacePublicErrorMessage.SKILL_GRANT_REASON_REQUIRED,
    ),
    DraftEditLeaseForbiddenError: (
        403,
        SpacePublicErrorMessage.DRAFT_EDIT_LEASE_FORBIDDEN,
    ),
    DraftEditLeaseNotFoundError: (
        404,
        SpacePublicErrorMessage.DRAFT_EDIT_LEASE_NOT_FOUND,
    ),
    DraftEditLeaseConflictError: (
        409,
        SpacePublicErrorMessage.DRAFT_EDIT_LEASE_CONFLICT,
    ),
    DraftEditLeaseTokenRejectedError: (
        409,
        SpacePublicErrorMessage.DRAFT_EDIT_LEASE_TOKEN_REJECTED,
    ),
    SpaceSkillIdempotencyConflictError: (
        409,
        SpacePublicErrorMessage.IDEMPOTENCY_KEY_REUSED,
    ),
    DraftNotFoundError: (404, SpacePublicErrorMessage.DRAFT_NOT_FOUND),
    DraftFileNotFoundError: (404, SpacePublicErrorMessage.DRAFT_NOT_FOUND),
    DraftFileNotTextError: (422, SpacePublicErrorMessage.SKILL_PACKAGE_INVALID),
    DraftFrozenError: (409, SpacePublicErrorMessage.DRAFT_FROZEN),
    DraftAlreadyExistsError: (409, SpacePublicErrorMessage.DRAFT_ALREADY_EXISTS),
    DraftRevisionConflictError: (409, SpacePublicErrorMessage.DRAFT_REVISION_CONFLICT),
    SkillNameChangedError: (422, SpacePublicErrorMessage.SKILL_NAME_CHANGED),
    SkillPackageInvalidError: (422, SpacePublicErrorMessage.SKILL_PACKAGE_INVALID),
    SkillPackageTooLargeError: (422, SpacePublicErrorMessage.SKILL_PACKAGE_INVALID),
    GitSnapshotInvalidError: (422, SpacePublicErrorMessage.SKILL_PACKAGE_INVALID),
    GitSnapshotError: (502, SpacePublicErrorMessage.SKILL_GIT_UNAVAILABLE),
    DraftContentStoreError: (
        503,
        SpacePublicErrorMessage.SKILL_DRAFT_STORE_UNAVAILABLE,
    ),
}

SPACE_SKILL_ERROR_CODES = {
    ExactSkillPackageFetchError: SpaceErrorCode.SC_MARKET_UNAVAILABLE,
    SkillCenterGatewayError: SpaceErrorCode.SC_MARKET_UNAVAILABLE,
    SkillManifestMissingError: SpaceErrorCode.SKILL_MANIFEST_MISSING,
    SkillManifestMultipleError: SpaceErrorCode.SKILL_MANIFEST_MULTIPLE,
    SkillPathInvalidError: SpaceErrorCode.SKILL_PATH_INVALID,
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
    DraftAlreadyExistsError: SpaceErrorCode.DRAFT_ALREADY_EXISTS,
    DraftRevisionConflictError: SpaceErrorCode.DRAFT_REVISION_CONFLICT,
    SkillNameChangedError: SpaceErrorCode.SKILL_NAME_CHANGED,
    SkillPackageInvalidError: SpaceErrorCode.SKILL_PACKAGE_INVALID,
    SkillPackageTooLargeError: SpaceErrorCode.SKILL_PACKAGE_INVALID,
    GitSnapshotInvalidError: SpaceErrorCode.SKILL_PACKAGE_INVALID,
    GitSnapshotError: SpaceErrorCode.SKILL_GIT_UNAVAILABLE,
    DraftContentStoreError: SpaceErrorCode.SKILL_DRAFT_STORE_UNAVAILABLE,
}
