"""Public request and response contracts for unified marketplace queries."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid", populate_by_name=True)
_UPSTREAM_ITEM = ConfigDict(extra="allow", populate_by_name=True)


class SkillMarketSearchRequest(BaseModel):
    """Search the built-in OCB Skill marketplace."""

    model_config = _STRICT

    keyword: str = Field(default="", max_length=200)
    page_num: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class McpMarketSearchRequest(BaseModel):
    """Search the MCP marketplace."""

    model_config = _STRICT

    keyword: str | None = Field(default=None, max_length=200)
    page_num: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class SkillCenterMarketSearchRequest(BaseModel):
    """Search public Skills in the external Skill Center catalogue.

    Application credentials and team identifiers are server-controlled and are
    intentionally absent from this public request.
    """

    model_config = _STRICT

    keyword: str | None = Field(default=None, max_length=200)
    page_num: int = Field(default=1, ge=1, alias="pageNum")
    page_size: int = Field(default=20, ge=1, le=100, alias="pageSize")
    is_official: bool | None = Field(default=None, alias="isOfficial")
    is_recommended: bool | None = Field(default=None, alias="isRecommended")
    tag_list: list[str] = Field(default_factory=list, alias="tagList", max_length=50)
    sort_by: Literal["latest", "oldest", "heat", "download", "favorite"] = Field(
        default="latest", alias="sortBy"
    )
    creator_name: str | None = Field(default=None, alias="creatorName", max_length=100)
    creator_work_no: str | None = Field(
        default=None, alias="creatorWorkNo", max_length=100
    )
    belong_to: Literal["PERSONAL", "TEAM"] | None = Field(
        default=None, alias="belongTo"
    )


class SkillMarketItem(BaseModel):
    """A Skill record from the built-in OCB marketplace."""

    model_config = _UPSTREAM_ITEM

    id: str | int | None = None
    skill_uuid: str | None = None
    name: str = ""
    description: str | None = None
    category: str | None = None
    tags: list[str] | str | None = None
    git_path: str | None = None


class McpMarketItem(BaseModel):
    """An MCP server visible in the public marketplace."""

    server_code: str
    name: str
    description: str | None = None
    network_types: list[str] = Field(default_factory=list)
    transport_protocol: str | None = None


class SkillCenterMarketItem(BaseModel):
    """A Skill record returned by Skill Center.

    Known fields are typed; additional upstream display metadata is preserved
    so Skill Center can add non-breaking fields without forcing this API to
    discard them.
    """

    model_config = _UPSTREAM_ITEM

    skill_id: str | int | None = Field(default=None, alias="skillId")
    skill_code: str | None = Field(default=None, alias="skillCode")
    skill_name: str | None = Field(default=None, alias="skillName")
    description: str | None = None
    creator_name: str | None = Field(default=None, alias="creatorName")
    creator_work_no: str | None = Field(default=None, alias="creatorWorkNo")
    access_level: str | None = Field(default=None, alias="accessLevel")
    belong_to: str | None = Field(default=None, alias="belongTo")
    tag_list: list[str] = Field(default_factory=list, alias="tagList")
