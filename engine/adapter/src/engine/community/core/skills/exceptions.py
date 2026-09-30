"""Stable domain errors exposed by the Skills service contract."""


class InvalidPoolMappingRequestError(ValueError):
    """A Pool mapping request is invalid and safe to reject without mutation."""


class LocalSkillPackageError(RuntimeError):
    """Base error for the package-level Local Skill apply contract."""


class LocalSkillPackageInvalidError(LocalSkillPackageError):
    """The ZIP or logical request is invalid and no canonical write occurred."""


class LocalSkillPackageTooLargeError(LocalSkillPackageInvalidError):
    """The ZIP exceeds the published package limits."""


class LocalSkillPackagePublishInProgressError(LocalSkillPackageError):
    """Another process currently owns the same target package lock."""


class LocalSkillPackagePublishLockUnavailableError(LocalSkillPackageError):
    """The cross-process lock backend could not be opened safely."""


class LocalSkillPackagePublishFailedError(LocalSkillPackageError):
    """Publication failed while the old package remained authoritative."""


class LocalSkillPackageRollbackFailedError(LocalSkillPackageError):
    """Publication failed and the final canonical state is unknown."""


__all__ = [
    "InvalidPoolMappingRequestError",
    "LocalSkillPackageError",
    "LocalSkillPackageInvalidError",
    "LocalSkillPackagePublishFailedError",
    "LocalSkillPackagePublishInProgressError",
    "LocalSkillPackagePublishLockUnavailableError",
    "LocalSkillPackageRollbackFailedError",
    "LocalSkillPackageTooLargeError",
]
