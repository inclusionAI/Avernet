"""Published request and response models for Phase 2 Space Skills.

These models define the additive Workshop wire before its domain slices land.
They deliberately describe facts and commands only; no model implies that the
corresponding persistence, Skill Center, or runtime capability exists yet.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import UploadFile
from pydantic import BaseModel, Field


class SpaceSkillDetail(BaseModel):
    """Workshop projection for one Space Skill."""

    skill_id: str = Field(description="Stable platform Skill identity.")
    skill_uuid: str = Field(description="Stable Skill Center and runtime code.")
    name: str = Field(description="Name read from the Skill's SKILL.md manifest.")
    description: str | None = Field(
        default=None, description="Description read from SKILL.md when present."
    )
    latest_published_version: int | None = Field(
        default=None, ge=1, description="Latest immutable Published business version."
    )
    draft_target_version: int | None = Field(
        default=None, ge=1, description="Business version currently being edited."
    )
    draft_status: str | None = Field(
        default=None, description="Independent Draft state, such as EDITING or FROZEN."
    )
    publication_status: str | None = Field(
        default=None, description="Current or latest Publication Attempt state."
    )
    current_user_skill_role: str | None = Field(
        default=None, description="OWNER or MANAGER when the caller has a Skill grant."
    )


class DraftDetail(BaseModel):
    """Mutable Draft metadata, separate from published Versions."""

    target_version: int = Field(ge=1, description="Business version being edited.")
    status: str = Field(description="Draft state, such as EDITING or FROZEN.")
    source_type: str | None = Field(
        default=None, description="Creation source, for example ZIP or GIT."
    )
    repository_url: str | None = Field(
        default=None, description="Original Git repository URL when the Draft is Git-backed."
    )
    branch: str | None = Field(
        default=None, description="Original Git branch when the Draft is Git-backed."
    )
    subdir: str | None = Field(
        default=None, description="Original Git subdirectory when configured."
    )
    gmt_modified: datetime | None = Field(
        default=None, description="When this Draft was last changed."
    )


class GitImportRequest(BaseModel):
    """Git source used to create a Space Skill Draft."""

    repository_url: str = Field(
        min_length=1, max_length=2048, description="Git repository URL to import."
    )
    branch: str | None = Field(
        default=None, max_length=256, description="Optional branch or ref."
    )
    subdir: str | None = Field(
        default=None, max_length=1024, description="Optional repository subdirectory."
    )


class SpaceSkillFolderUpload(BaseModel):
    """Browser-selected files and relative paths for one Space Skill directory."""

    files: list[UploadFile] = Field(
        description="All files selected from the local Skill directory."
    )
    file_paths: str | None = Field(
        default=None,
        description=(
            "Optional JSON array of relative paths aligned one-to-one with files; "
            "file names are used when omitted."
        ),
    )


class RefreshDraftFromGitRequest(BaseModel):
    """Explicit confirmation for replacing a Git-backed Draft from its source."""

    confirm_overwrite: bool = Field(
        default=False, description="Confirms replacement of the current Draft files."
    )


class FileTreeItem(BaseModel):
    """One file or directory in a Draft or exact Version tree."""

    path: str = Field(description="Slash-separated path relative to the Skill root.")
    is_directory: bool = Field(description="Whether this entry is a directory.")
    size_bytes: int | None = Field(
        default=None, ge=0, description="File size; null for directories."
    )


class FileContent(BaseModel):
    """UTF-8 text content of one Draft or exact Version file."""

    path: str = Field(description="Slash-separated path relative to the Skill root.")
    content: str = Field(description="UTF-8 file content.")


class WriteDraftFileRequest(BaseModel):
    """Replacement content for one Draft file."""

    content: str = Field(description="UTF-8 content replacing the file.")
    fencing_token: int | None = Field(
        default=None,
        ge=1,
        description="Team Draft lease token when a lease is required.",
    )


class SkillGrant(BaseModel):
    """One immutable role grant on a Space Skill."""

    user_id: str = Field(description="Granted Space member identifier.")
    role: str = Field(description="OWNER or MANAGER.")


class SkillGrants(BaseModel):
    """The unique Owner and zero or more Managers for one Space Skill."""

    owner: SkillGrant = Field(description="The Skill's sole Owner.")
    managers: list[SkillGrant] = Field(description="Current Manager grants.")


class OwnerTransferRequest(BaseModel):
    """Request to atomically transfer the unique Skill Owner role."""

    target_user_id: str = Field(
        min_length=1, max_length=256, description="Current Space member receiving ownership."
    )
    reason: str = Field(
        min_length=1, max_length=1000, description="Auditable reason for the transfer."
    )
    retain_previous_owner_as_manager: bool = Field(
        default=True,
        description="Whether the former Owner remains a Manager after transfer.",
    )


class DraftLease(BaseModel):
    """Current Team Draft edit lease and fencing state."""

    required: bool = Field(description="False for Personal Spaces, which need no lease.")
    holder_user_id: str | None = Field(
        default=None, description="Current lease holder when a Team lease is held."
    )
    fencing_token: int | None = Field(
        default=None, ge=1, description="Current lease fencing token when held."
    )


class PublishedVersion(BaseModel):
    """One immutable, exactly-addressable published Skill version."""

    version: int = Field(ge=1, description="Business version ordinal, not a database id.")
    status: str = Field(description="Version lifecycle state, for example PUBLISHED.")
    sc_version_number: str | None = Field(
        default=None, description="Exact externally materialized Skill Center version."
    )
    published_at: datetime | None = Field(
        default=None, description="When this exact version became published."
    )


class UpgradeImpact(BaseModel):
    """Read-only impact projection before publishing a Draft upgrade."""

    affected_bot_count: int = Field(ge=0, description="Number of affected Bot bindings.")
    affected_bots: list[str] = Field(description="Affected Bot identifiers.")


class PublicationAttempt(BaseModel):
    """One asynchronous publish or materialization attempt."""

    attempt_id: str = Field(description="Stable publication attempt identifier.")
    target_version: int = Field(ge=1, description="Business version this attempt handles.")
    status: str = Field(description="Attempt state, including RESULT_UNKNOWN when applicable.")
    created_at: datetime | None = Field(default=None, description="Attempt creation time.")
    error_code: str | None = Field(
        default=None, description="Stable failure code when the attempt failed."
    )


class CreatePublicationRequest(BaseModel):
    """Optional Team lease proof attached to a publication command."""

    fencing_token: int | None = Field(
        default=None, ge=1, description="Team Draft lease token when a lease is required."
    )


class RetirementImpact(BaseModel):
    """References and in-flight work that can block whole-Skill retirement."""

    can_retire: bool = Field(description="Whether retirement may start now.")
    blocking_reasons: list[str] = Field(
        description="Existing bindings, artifacts, or in-flight work that block retirement."
    )


class RetireSkillRequest(BaseModel):
    """Auditable request to retire the complete Space Skill."""

    reason: str = Field(
        min_length=1, max_length=1000, description="Reason recorded for whole-Skill retirement."
    )
