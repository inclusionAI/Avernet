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
