"""Typed data contracts for the Skill Center repository Protocols."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, NotRequired, TypedDict


class SpaceCreateData(TypedDict):
    space_code: str
    space_type: Literal["PERSONAL", "TEAM"]
    name: str
    created_by: str
    env: str
    description: NotRequired[str | None]
    personal_owner_id: NotRequired[str | None]
    sc_team_id: NotRequired[int | None]


class SpaceRecord(TypedDict):
    id: int
    space_code: str
    space_type: str
    name: str
    description: str | None
    personal_owner_id: str | None
    sc_team_id: int | None
    sc_mapping_status: str
    created_by: str
    deleted_at: object | None
    deleted_by: str | None
    env: str


class SpaceSkillCreateData(TypedDict):
    name: str
    env: str
    skill_uuid: str
    zip_url: str
    draft_target_version: int
    draft_status: Literal["EDITING", "FROZEN"]
    draft_description: str | None
    draft_source_kind: Literal["FOLDER", "GIT", "PUBLISHED_VERSION"]
    creation_request_id: str
    creation_request_hash: str
    description: NotRequired[str | None]
    source_type: NotRequired[str | None]
    source_repo_url: NotRequired[str | None]
    source_branch: NotRequired[str | None]
    source_subdir: NotRequired[str | None]
    source_commit_sha: NotRequired[str | None]


class SpaceSkillOwnershipData(TypedDict):
    space_id: int
    created_by: str
    env: str


class SpaceSkillOwnerGrantData(TypedDict):
    user_id: str
    granted_by: str
    env: str


class SpaceSkillIdentityRecord(TypedDict):
    id: int
    skill_uuid: str
    draft_target_version: int
    draft_status: str
    env: str


class SpaceSkillOwnershipRecord(TypedDict):
    id: int
    skill_id: int
    space_id: int
    env: str


class SpaceSkillGrantRecord(TypedDict):
    id: int
    skill_id: int
    user_id: str
    role: str
    status: str
    owner_slot: int | None
    env: str


class SpaceSkillGrantItem(TypedDict):
    """Stable public-facing identity of one active Skill Grant."""

    user_id: str
    role: Literal["OWNER", "MANAGER"]
    display_name: NotRequired[str | None]


class SpaceSkillGrantSetRecord(TypedDict):
    """Current active grants plus the addressed actor's Skill role."""

    owner: SpaceSkillGrantItem
    managers: list[SpaceSkillGrantItem]
    actor_role: Literal["OWNER", "MANAGER"] | None


class SpaceSkillActorPermissions(TypedDict):
    edit_draft: bool
    publish_draft: bool
    delete_draft: bool
    create_upgrade_draft: bool
    offline_skill: bool
    manage_grants: bool
    transfer_owner: bool
    request_edit_access: bool
    takeover_lease: bool


class SpaceSkillGrantActorRecord(TypedDict):
    skill_role: Literal["OWNER", "MANAGER"] | None
    permissions: SpaceSkillActorPermissions


class SpaceSkillGrantViewRecord(TypedDict):
    owner: SpaceSkillGrantItem
    managers: list[SpaceSkillGrantItem]
    actor: SpaceSkillGrantActorRecord


class SpaceSkillCreationRecord(TypedDict):
    created: bool
    skill: SpaceSkillIdentityRecord
    ownership: SpaceSkillOwnershipRecord
    owner_grant: SpaceSkillGrantRecord


class SpaceSkillCreationReplayRecord(TypedDict):
    skill_id: int
    space_id: int
    request_hash: str


class SpaceSkillDraftRecord(TypedDict):
    skill_id: int
    skill_uuid: str
    name: str
    draft_description: str
    target_version: int
    status: Literal["EDITING", "FROZEN"]
    locator: str
    source_kind: Literal["FOLDER", "GIT", "PUBLISHED_VERSION"]
    source_repo_url: str | None
    source_branch: str | None
    source_subdir: str | None
    source_commit_sha: str | None
    space_type: Literal["PERSONAL", "TEAM"]
    sc_team_id: int | None


class DraftDeleteRecord(TypedDict):
    changed: bool
    deleted_scope: Literal["DRAFT", "SKILL"]
    locator: str


class SkillUpgradeIdentityRecord(TypedDict):
    skill_id: int
    skill_uuid: str
    name: str
    space_type: Literal["PERSONAL", "TEAM"]
    sc_team_id: int | None
    offline_at: datetime | None


class DraftUpgradeRecord(TypedDict):
    created: bool
    draft: SpaceSkillDraftRecord


class SkillUpgradeRequestRecord(TypedDict):
    """Durable upgrade-command identity and its optional live Draft."""

    skill_id: int
    space_id: int
    status: Literal["ACTIVE", "SPENT"]
    draft: SpaceSkillDraftRecord | None


class SpaceSkillQueryRecord(TypedDict):
    """Persistence projection for a Space-owned Skill and actor grant."""

    id: int
    skill_uuid: str
    name: str
    description: str | None
    status: str | None
    draft_status: str | None
    space_type: Literal["PERSONAL", "TEAM"]
    current_user_skill_role: Literal["OWNER", "MANAGER"] | None
    lease_holder_user_id: str | None
    lease_holder_display_name: str | None
    gmt_created: datetime
    gmt_modified: datetime


class SpaceSkillReadRecord(TypedDict):
    id: int
    skill_uuid: str
    name: str
    description: str | None
    status: str | None
    draft_status: str | None
    space_type: Literal["PERSONAL", "TEAM"]
    current_user_skill_role: Literal["OWNER", "MANAGER"] | None
    lease_holder_user_id: str | None
    lease_holder_display_name: str | None
    gmt_created: datetime
    gmt_modified: datetime
    source_type: Literal["FOLDER", "GIT", "COPY"]
    draft_target_version: int | None
    draft_description: str | None
    draft_locator: str | None
    draft_source_kind: str | None
    source_repo_url: str | None
    source_branch: str | None
    source_subdir: str | None
    source_commit_sha: str | None
    offline_at: datetime | None
    offline_by: str | None
    owner_user_id: str
    owner_display_name: str | None
    latest_version_id: int | None
    latest_version_ordinal: int | None
    latest_sc_version_number: str | None
    latest_published_at: datetime | None
    active_attempt_id: int | None
    active_attempt_target_version: int | None
    active_attempt_status: str | None
    pending_request_id: int | None
    pending_request_no: str | None


class DraftEditLeaseRecord(TypedDict):
    """Current durable lease row; its token monotonically increases forever."""

    holder_user_id: str | None
    fencing_token: int


class DraftEditLeaseViewRecord(TypedDict):
    """Actor-relative Lease resource returned by the Service API."""

    required: bool
    state: Literal["NOT_REQUIRED", "FREE", "HELD_BY_ME", "HELD_BY_OTHER"]
    holder_user_id: str | None
    fencing_token: int | None


class SkillVersionRecord(TypedDict):
    """Persistence projection for one immutable Skill Version."""

    id: int
    skill_id: int
    version_ordinal: int
    status: Literal["MATERIALIZING", "PUBLISHED"]
    sc_version_number: str
    sc_skill_id: int | None
    sc_version_id: int | None
    name: str
    description: str | None
    metadata_json: str | None
    published_at: datetime | None


class SpaceSkillVersionRecord(TypedDict):
    id: int
    skill_id: int
    version_ordinal: int
    status: Literal["MATERIALIZING", "PUBLISHED"]
    sc_version_number: str
    sc_skill_id: int | None
    sc_version_id: int | None
    name: str
    description: str | None
    metadata_json: str | None
    published_at: datetime | None
    skill_uuid: str


class ConsumableSpaceSkillRecord(TypedDict):
    skill_id: int
    skill_uuid: str
    name: str
    description: str | None
    version_ordinal: int
    sc_version_number: str
    published_at: datetime
