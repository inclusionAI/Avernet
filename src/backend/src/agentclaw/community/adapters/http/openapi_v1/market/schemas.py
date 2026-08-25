"""Public request and response contracts for marketplace queries."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentclaw.community.adapters.http.openapi_v1.mcp.schemas import McpServerDetail

_STRICT = ConfigDict(extra="forbid", populate_by_name=True)
_UPSTREAM_ITEM = ConfigDict(extra="allow", populate_by_name=True)


class SkillMarketSearchRequest(BaseModel):
    """Search the built-in Skill marketplace."""

    model_config = _STRICT

    keyword: str = Field(
        default="", max_length=200, description="Text matched against Skill metadata."
    )
    page_num: int = Field(default=1, ge=1, description="One-based page number.")
    page_size: int = Field(
        default=20, ge=1, le=100, description="Maximum Skills returned per page."
    )


class McpMarketSearchRequest(BaseModel):
    """Search the MCP marketplace with the legacy catalogue filters."""

    model_config = _STRICT

    keyword: str | None = Field(
        default=None,
        max_length=200,
        description="Optional text matched against MCP server metadata.",
    )
    page_num: int = Field(default=1, ge=1, description="One-based page number.")
    page_size: int = Field(
        default=20, ge=1, le=100, description="Maximum MCP servers returned per page."
    )
    server_codes: list[str] | None = Field(
        default=None, max_length=100, description="Filter by MCP server codes."
    )
    platform_server_codes: list[str] | None = Field(
        default=None, max_length=100, description="Filter by platform MCP server codes."
    )
    run_modes: list[str] | None = Field(
        default=None, max_length=20, description="Filter by MCP run modes."
    )
    statuses: list[str] | None = Field(
        default=None, max_length=20, description="Filter by publication statuses."
    )
    transport_protocols: list[str] | None = Field(
        default=None, max_length=20, description="Filter by supported transport protocols."
    )
    host_platforms: list[str] | None = Field(
        default=None, max_length=50, description="Filter by host platforms."
    )
    owners: list[str] | None = Field(
        default=None, max_length=100, description="Filter by owner user or employee identifiers."
    )
    network_types: list[str] | None = Field(
        default=None, max_length=20, description="Filter by visible network types (INTERNET or OFFICE)."
    )
    categories: list[str] | None = Field(
        default=None, max_length=100, description="Filter by marketplace categories."
    )
    tenants: list[str] | None = Field(
        default=None, max_length=100, description="Filter by tenant codes."
    )
    tags: list[str] | None = Field(
        default=None, max_length=100, description="Filter by marketplace tags."
    )


class SkillCenterMarketSearchRequest(BaseModel):
    """Search public Skills in the external Skill Center catalogue."""

    model_config = _STRICT

    keyword: str | None = Field(
        default=None,
        max_length=200,
        description="Optional text matched against Skill Center metadata.",
    )
    page_num: int = Field(
        default=1, ge=1, alias="pageNum", description="One-based page number."
    )
    page_size: int = Field(
        default=20,
        ge=1,
        le=100,
        alias="pageSize",
        description="Maximum Skill Center records returned per page.",
    )
    is_official: bool | None = Field(
        default=None,
        alias="isOfficial",
        description="Filter by official publication status, or null for either.",
    )
    is_recommended: bool | None = Field(
        default=None,
        alias="isRecommended",
        description="Filter by recommendation status, or null for either.",
    )
    tag_list: list[str] = Field(
        default_factory=list,
        alias="tagList",
        max_length=50,
        description="Tags that requested Skills must match.",
    )
    sort_by: Literal["latest", "oldest", "heat", "download", "favorite"] = Field(
        default="latest",
        alias="sortBy",
        description="Ordering applied to matched Skills.",
    )
    creator_name: str | None = Field(
        default=None,
        alias="creatorName",
        max_length=100,
        description="Optional creator display-name filter.",
    )
    creator_work_no: str | None = Field(
        default=None,
        alias="creatorWorkNo",
        max_length=100,
        description="Optional creator employee-number filter.",
    )
    belong_to: Literal["PERSONAL", "TEAM"] | None = Field(
        default=None,
        alias="belongTo",
        description="Ownership category filter, or null for all categories.",
    )


class SkillCenterTag(BaseModel):
    """A tag returned by Skill Center, including its nested children."""

    model_config = _UPSTREAM_ITEM

    id: int = Field(description="Skill Center tag identifier.")
    name: str = Field(description="Display name of the tag.")
    description: str | None = Field(
        default=None, description="Optional description of the tag."
    )
    icon_url: str | None = Field(
        default=None, alias="iconUrl", description="Optional tag icon URL."
    )
    parent_id: int | None = Field(
        default=None, alias="parentId", description="Parent tag identifier."
    )
    tag_level: int = Field(
        alias="tagLevel", ge=1, description="Tag depth, starting at level one."
    )
    children: list["SkillCenterTag"] = Field(
        default_factory=list, description="Nested child tags."
    )

    @field_validator("children", mode="before")
    @classmethod
    def normalize_children(cls, value):
        """Normalize Skill Center leaf tags from ``null`` to an empty list."""
        return [] if value is None else value


class SkillMarketItem(BaseModel):
    """A Skill record from the built-in marketplace."""

    model_config = _UPSTREAM_ITEM

    id: str | int | None = Field(default=None, description="Source record identifier.")
    skill_uuid: str | None = Field(
        default=None, description="Stable UUID when supplied by the source."
    )
    name: str = Field(default="", description="Display name of the Skill.")
    description: str | None = Field(
        default=None, description="Human-readable purpose of the Skill."
    )
    category: str | None = Field(
        default=None, description="Marketplace category assigned to the Skill."
    )
    tags: list[str] | str | None = Field(
        default=None, description="Tags supplied for marketplace discovery."
    )
    git_path: str | None = Field(
        default=None, description="Governed source locator for the Skill content."
    )


class McpMarketItem(McpServerDetail):
    """A lossless snake-case equivalent of one legacy MCP market-list item."""


class SkillCenterMarketItem(BaseModel):
    """A public Skill record returned by Skill Center."""

    model_config = _UPSTREAM_ITEM

    skill_id: str | int | None = Field(
        default=None, alias="skillId", description="Skill Center record identifier."
    )
    skill_code: str | None = Field(
        default=None, alias="skillCode", description="Stable Skill Center code."
    )
    skill_name: str | None = Field(
        default=None, alias="skillName", description="Display name of the Skill."
    )
    description: str | None = Field(
        default=None, description="Human-readable purpose of the Skill."
    )
    creator_name: str | None = Field(
        default=None, alias="creatorName", description="Display name of the creator."
    )
    creator_work_no: str | None = Field(
        default=None,
        alias="creatorWorkNo",
        description="Employee number of the creator when available.",
    )
    access_level: str | None = Field(
        default=None, alias="accessLevel", description="Published access level."
    )
    belong_to: str | None = Field(
        default=None, alias="belongTo", description="Published ownership category."
    )
    tag_list: list[str] = Field(
        default_factory=list,
        alias="tagList",
        description="Tags supplied by Skill Center.",
    )
