"""Domain records and enums for spaces and space membership."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class SpaceType(StrEnum):
    PERSONAL = "PERSONAL"
    TEAM = "TEAM"


class SpaceRole(StrEnum):
    OWNER = "OWNER"
    MEMBER = "MEMBER"


class SpaceJoinStatus(StrEnum):
    JOINED = "JOINED"
    APPLYING = "APPLYING"
    NOT_JOINED = "NOT_JOINED"


class SpaceScTeamRepairStatus(StrEnum):
    """Successful outcomes of an idempotent SC Team binding repair."""

    REPAIRED = "REPAIRED"
    ALREADY_BOUND = "ALREADY_BOUND"


class SpaceRecord(BaseModel):
    id: int
    space_code: str
    space_type: SpaceType
    name: str
    personal_owner_id: str | None
    sc_team_id: str | None = None
    env: str
    created_by: str
    updated_by: str
    gmt_created: datetime
    gmt_modified: datetime


class SpaceMemberRecord(BaseModel):
    id: int
    space_id: int
    user_id: str
    role: SpaceRole
    env: str
    created_by: str
    gmt_created: datetime
    gmt_modified: datetime


class SpaceSummaryRecord(BaseModel):
    space: SpaceRecord
    current_user_role: SpaceRole | None
    join_status: SpaceJoinStatus
    member_count: int
    owner_count: int


class SpaceMemberSummaryRecord(BaseModel):
    member: SpaceMemberRecord
    is_creator: bool
    user_name: str | None = None
    display_name: str | None = None


class PersonalSpaceLookupRecord(BaseModel):
    user_id: str
    space_id: int | None
    found: bool


class SpaceScTeamRepairResult(BaseModel):
    """Confirmed SC Team binding after repair or an idempotent retry."""

    space_id: int
    status: SpaceScTeamRepairStatus
    sc_team_id: str
