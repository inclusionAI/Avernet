"""Domain errors raised by Skill Center operations."""

from agentclaw.community.core.errors import DomainError


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

    _PUBLIC_MESSAGES = {
        "invalid_package": "Invalid Skill package",
        "invalid_zip": "Skill package must be a valid ZIP archive",
        "unsafe_file_path": "Skill package contains an unsafe file path",
        "duplicate_file_path": "Skill package contains duplicate file paths",
        "unreadable_archive": "Skill package could not be read",
        "missing_skill_file": "Skill package must contain one SKILL.md file",
        "multiple_skill_files": "Skill package must contain exactly one SKILL.md file",
        "invalid_wrapper": "Skill package files must be under one Skill directory",
        "invalid_encoding": "SKILL.md must be UTF-8 encoded",
        "invalid_metadata": "SKILL.md must define a valid name and description",
        "wrapper_name_mismatch": "Skill directory name must match SKILL.md name",
    }

    def __init__(self, reason: str = "invalid_package") -> None:
        super().__init__(reason)
        self.public_message = self._PUBLIC_MESSAGES.get(
            reason, self._PUBLIC_MESSAGES["invalid_package"]
        )


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


class SpaceSkillGrantNotFoundError(Exception):
    """The addressed Skill is not owned by the addressed Space."""


class SpaceSkillGrantForbiddenError(Exception):
    """The actor lacks the live Skill/Space role required by the command."""


class SpaceSkillGrantMemberRequiredError(Exception):
    """A Grant target is not an active member of the owning Space."""


class SpaceSkillGrantConflictError(Exception):
    """The command conflicts with the current active Grant set."""


class SpaceSkillGrantReasonRequiredError(Exception):
    """A Space administrator transfer omitted its mandatory audit reason."""


class DraftEditLeaseNotFoundError(Exception):
    """The addressed Space Skill Draft does not exist."""


class DraftEditLeaseForbiddenError(Exception):
    """The actor has no active OWNER/MANAGER Grant for this Draft."""


class DraftEditLeaseConflictError(Exception):
    """Another editor currently holds the permanent Draft Lease."""


class DraftEditLeaseTokenRejectedError(Exception):
    """The supplied fencing token is stale or belongs to another holder."""


class SpaceSkillIdempotencyConflictError(Exception):
    """An Idempotency-Key was already bound to a different creation intent."""


class DraftNotFoundError(Exception):
    """The addressed Space Skill has no current Draft."""


class DraftFrozenError(Exception):
    """A FROZEN Draft cannot be mutated or deleted."""


class DraftRevisionConflictError(Exception):
    """The expected Draft revision is no longer current."""


class DraftFileNotFoundError(Exception):
    """The addressed file does not exist in the current Draft."""


class DraftFileNotTextError(Exception):
    """The addressed Draft file is not UTF-8 text."""


class SkillNameChangedError(Exception):
    """A Draft mutation attempted to change the stable SKILL.md name."""


# ── SkillSet control plane ────────────────────────────────────────────────
# These are ``DomainError`` subclasses so the SkillSet routers can raise the
# situation and let the HTTP adapter decide the status: the mapping lives in
# ``adapters.http.app._DOMAIN_ERROR_STATUS_MAP`` (Rule 7 — core/ is
# transport-free), and ``_domain_error_handler`` logs 5xx with a traceback, so
# the ``__cause__`` chain survives to the log instead of being replaced by a
# hand-built ``HTTPException`` at the route.
#
# Each default ``detail`` is the message the legacy ``/api/skillsets`` wire
# already published, so callers see the same body as before. Every raise site
# may override it when it has something more specific to say.


class SkillSetControlPlaneNotFoundError(DomainError):
    """The addressed Bot scope, or a canonical SkillSet inside it, is absent."""

    def __init__(self, detail: str = "Skill set not found") -> None:
        super().__init__(detail)


class SkillSetAccessDeniedError(DomainError):
    """The authenticated principal cannot mutate the addressed Bot SkillSet."""

    def __init__(self, detail: str = "Forbidden") -> None:
        super().__init__(detail)


class SkillSetControlPlaneConflictError(DomainError):
    """A canonical SkillSet command conflicts with desired state.

    Raised with a stable uppercase reason code (``SKILL_SET_NAME_CONFLICT``,
    ``BOT_MUTATION_BUSY``, ...) that the published wire echoes verbatim.
    """

    def __init__(self, detail: str = "Skill set operation conflict") -> None:
        super().__init__(detail)


class SkillSetRuntimeReconcileError(DomainError):
    """Runtime reconciliation failed after desired-state compensation."""

    def __init__(self, detail: str = "Skill set runtime sync failed") -> None:
        super().__init__(detail)

class SkillSetControlPlaneLockUnavailableError(DomainError):
    """The runtime layout edit boundary is unavailable; mutation failed closed."""

    def __init__(self, detail: str = "Skill set mutation unavailable") -> None:
        super().__init__(detail)


class SkillSetManagedResourceError(DomainError):
    """A Direct command targets a resource managed by an ordinary SkillSet."""


class McpPermissionDeniedError(DomainError):
    """The actor cannot install or activate the addressed MCP server."""

    def __init__(self, detail: str = "MCP permission denied") -> None:
        super().__init__(detail)
