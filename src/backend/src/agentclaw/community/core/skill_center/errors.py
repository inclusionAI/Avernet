"""Domain errors raised by Skill Center operations."""


class SkillDeleteConsistencyError(RuntimeError):
    """A Skill delete could not safely converge filesystem and database state."""


class SkillReferencedBySkillSetError(RuntimeError):
    """A Skill cannot be deleted while any SkillSet still references it."""

    def __init__(self, skill_set_ids: list[str]) -> None:
        super().__init__("skill is still referenced by a skill set")
        self.skill_set_ids = skill_set_ids


class LocalSkillNotFoundError(Exception):
    """A Local Skill or its authorized Bot scope is not visible to the actor."""


class LocalSkillOwnerAmbiguousError(Exception):
    """Legacy Local Skill ownership cannot be resolved without guessing."""


class LocalSkillInvalidPackageError(Exception):
    """A ZIP package is malformed or violates the Local Skill layout."""


class LocalSkillTooLargeError(Exception):
    """A ZIP package exceeds a published upload limit."""


class LocalSkillNotReadyError(Exception):
    """The Bot cannot safely accept a mutation yet."""


class LocalSkillActiveError(Exception):
    """An active Local Skill must be explicitly deactivated before deletion."""


class LocalSkillDuplicateError(Exception):
    """A same-name Local Skill already exists; replacement is not supported."""


class LocalSkillStorageError(Exception):
    """A package persistence or compensating cleanup operation failed."""


class SkillParameterValidationError(Exception):
    """The full Bot-level parameter object violates SKILL.md config."""


class LocalSkillRuntimeSyncError(Exception):
    """The runtime projection could not match Local Skill desired state."""


class LocalSkillEditPausedError(Exception):
    """A Bot Skill layout operation currently owns the edit lock."""


class LocalSkillEditBusyError(LocalSkillEditPausedError):
    """Another Local Skill mutation is still in progress."""


class LocalSkillLayoutRollbackError(LocalSkillEditPausedError):
    """Pool-to-Legacy layout rollback currently owns the filesystem."""


class LocalSkillEditLockUnavailableError(LocalSkillEditPausedError):
    """The distributed lock backend is unavailable, so writes fail closed."""


class ActiveSkillSetReferenceError(RuntimeError):
    """A Skill became referenced by an active custom SkillSet."""


class SkillManagedBySkillSetError(Exception):
    """A Direct command addressed a Skill owned by a normal SkillSet."""


class SkillRuntimeNameConflictError(Exception):
    """A Direct activation would create an ambiguous runtime entry name."""


class SkillEngineNotSupportedError(Exception):
    """The addressed Bot type and logical Engine cannot consume this Skill."""


class RepositoryCatalogNotFoundError(Exception):
    """A governed shared Repo Skill is not visible in this environment."""


class RepositoryCatalogSyncInProgressError(Exception):
    """The environment-wide governed Repo synchronization already has a holder."""


class RepositoryCatalogSyncFailedError(Exception):
    """The governed Repo synchronization could not finish successfully."""


class SkillSetControlPlaneNotFoundError(Exception):
    """A canonical SkillSet is absent from the addressed Bot scope."""


class SkillSetControlPlaneConflictError(Exception):
    """A canonical SkillSet command conflicts with desired state."""


class SkillSetRuntimeReconcileError(Exception):
    """Runtime reconciliation failed after desired-state compensation."""


class SkillSetManagedResourceError(Exception):
    """A Direct command targets a resource managed by an ordinary SkillSet."""
