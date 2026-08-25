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
    retire_skill: bool
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
    skill: SpaceSkillIdentityRecord
    ownership: SpaceSkillOwnershipRecord
    owner_grant: SpaceSkillGrantRecord


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
    gmt_created: datetime
    gmt_modified: datetime


class SpaceSkillSummaryRecord(TypedDict):
    """Service projection containing explicit UI authorization decisions."""

    id: int
    skill_uuid: str
    name: str
    description: str | None
    status: str | None
    draft_status: str | None
    space_type: Literal["PERSONAL", "TEAM"]
    current_user_skill_role: Literal["OWNER", "MANAGER"] | None
    gmt_created: datetime
    gmt_modified: datetime
    can_edit: bool
    can_grant: bool
    can_apply_edit: bool
