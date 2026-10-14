"""Transport-independent commands and results for trusted Space maintenance."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from agentclaw.community.core.spaces.models import SpaceRecord

EmployeeId = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=128),
]
SpaceName = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=128),
]
ManagementRole = Literal["ADMIN", "MEMBER"]


class ManagementInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateTeam(ManagementInput):
    space_type: Literal["TEAM"] = "TEAM"
    space_name: SpaceName
    admin_user_id: EmployeeId


class InitializeTeam(CreateTeam):
    member_user_ids: list[EmployeeId] = Field(default_factory=list, max_length=500)


class RenameTeam(ManagementInput):
    space_name: SpaceName


class MemberBatch(ManagementInput):
    member_user_ids: list[EmployeeId] = Field(min_length=1, max_length=500)


class SetMemberRoles(MemberBatch):
    role: ManagementRole


class SpacePageQuery(ManagementInput):
    page_no: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    keyword: str | None = Field(default=None, max_length=128)
    creator_user_id: EmployeeId | None = None


class MemberPageQuery(ManagementInput):
    page_no: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    keyword: str | None = Field(default=None, max_length=128)
    role: ManagementRole | None = None
    status: Literal["ACTIVE", "INACTIVE", "ALL"] = "ACTIVE"


class ManagedSpace(BaseModel):
    space: SpaceRecord
    member_count: int
    admin_count: int


class ManagementError(RuntimeError):
    """Stable domain code plus safe structured details; no HTTP status."""

    def __init__(self, code: str, **details: object) -> None:
        super().__init__(code)
        self.code = code
        self.details = details
