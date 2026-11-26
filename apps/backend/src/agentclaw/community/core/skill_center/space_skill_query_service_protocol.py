"""Service API contract for querying Skills owned by a Space."""

from __future__ import annotations

from abc import abstractmethod
from datetime import datetime
from typing import Literal, Protocol, TypedDict, runtime_checkable


class SkillOwnerSummaryRecord(TypedDict):
    user_id: str
    display_name: str | None


class SkillVersionSummaryRecord(TypedDict):
    version: int
    sc_version_number: str
    published_at: datetime


class SkillDraftDetailRecord(TypedDict):
    target_version: int
    status: Literal["EDITING", "FROZEN"]
    revision_id: str
    name: str
    description: str | None
    source_kind: Literal["FOLDER", "GIT", "PUBLISHED_VERSION"]
    source_repo_url: str | None
    source_branch: str | None
    source_commit_sha: str | None
    source_subdir: str | None


class PublicationAttemptSummaryRecord(TypedDict):
    attempt_id: str
    target_version: int
    status: Literal[
        "PREPARING",
        "SC_SUBMITTING",
        "WAITING_SC",
        "RESULT_UNKNOWN",
        "MATERIALIZING",
    ]


class PendingEditorRequestSummaryRecord(TypedDict):
    work_order_id: int
    work_order_no: str
    status: Literal["PENDING"]


class SkillActorPermissionsRecord(TypedDict):
    edit_draft: bool
    publish_draft: bool
    delete_draft: bool
    create_upgrade_draft: bool
    offline_skill: bool
    manage_grants: bool
    transfer_owner: bool
    request_edit_access: bool
    takeover_lease: bool


class SkillActorSummaryRecord(TypedDict):
    skill_role: Literal["OWNER", "MANAGER"] | None
    permissions: SkillActorPermissionsRecord
    pending_editor_request: PendingEditorRequestSummaryRecord | None


class DraftEditLeaseSummaryRecord(TypedDict):
    required: bool
    state: Literal["NOT_REQUIRED", "FREE", "HELD_BY_ME", "HELD_BY_OTHER"]
    holder_user_id: str | None
    holder_display_name: str | None


class SpaceSkillSummaryRecord(TypedDict):
    id: int
    skill_uuid: str
    name: str
    description: str | None
    lifecycle_status: Literal["DRAFT_ONLY", "PUBLISHED", "OFFLINE"]
    space_type: Literal["PERSONAL", "TEAM"]
    owner: SkillOwnerSummaryRecord
    latest_published_version: SkillVersionSummaryRecord | None
    draft: SkillDraftDetailRecord | None
    active_publication: PublicationAttemptSummaryRecord | None
    actor: SkillActorSummaryRecord
    gmt_created: datetime
    gmt_modified: datetime
    lease_summary: DraftEditLeaseSummaryRecord | None


class SpaceSkillDetailRecord(SpaceSkillSummaryRecord):
    source: Literal["FOLDER", "GIT", "COPY"]
    offline_at: datetime | None
    offline_by: str | None


@runtime_checkable
class SpaceSkillQueryServiceProtocol(Protocol):
    """Read-only Space Skill query service."""

    @abstractmethod
    def list_space_skills(
        self,
        *,
        space_id: int,
        actor_id: str,
        keyword: str | None,
        page: int,
        page_size: int,
    ) -> tuple[int, list[SpaceSkillSummaryRecord]]: ...

    @abstractmethod
    def get_space_skill(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> SpaceSkillDetailRecord: ...
