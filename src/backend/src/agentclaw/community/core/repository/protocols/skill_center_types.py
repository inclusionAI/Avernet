"""Typed data contracts for the Skill Center repository Protocols."""

from __future__ import annotations

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


class SpaceSkillCreationRecord(TypedDict):
    skill: SpaceSkillIdentityRecord
    ownership: SpaceSkillOwnershipRecord
    owner_grant: SpaceSkillGrantRecord
