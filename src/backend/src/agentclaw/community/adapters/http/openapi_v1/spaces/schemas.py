"""HTTP schemas for spaces, members and market favorites."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_serializer

from agentclaw.community.adapters.http.openapi_v1.enums import _DocumentedEnum


class SpaceType(_DocumentedEnum):
    """Kind of Space and its ownership model."""

    PERSONAL = "PERSONAL"
    TEAM = "TEAM"

    __descriptions__ = {
        "PERSONAL": "A private Space initialized for one user.",
        "TEAM": "A shared Space managed by one or more owners.",
    }


class SpaceRole(_DocumentedEnum):
    """Role held by a user in a Space."""

    OWNER = "OWNER"
    MEMBER = "MEMBER"

    __descriptions__ = {
        "OWNER": "May manage the Space and its membership.",
        "MEMBER": "May use the Space without managing its membership.",
    }


class SpaceJoinStatus(_DocumentedEnum):
    """Current user's membership state for a Space."""

    JOINED = "JOINED"
    APPLYING = "APPLYING"
    NOT_JOINED = "NOT_JOINED"

    __descriptions__ = {
        "JOINED": "The user is currently a member of the Space.",
        "APPLYING": "The user's join request is awaiting review.",
        "NOT_JOINED": "The user is not a member and has no pending request.",
    }


class FavoriteTargetType(_DocumentedEnum):
    """Type of marketplace item saved as a favorite."""

    SKILL = "SKILL"
    MCP = "MCP"

    __descriptions__ = {
        "SKILL": "A published Skill.",
        "MCP": "A published MCP server.",
    }


def _utc_datetime(value: datetime) -> str:
    """Serialize persisted timestamps as explicit UTC on the public wire."""
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


class _UtcResponseModel(BaseModel):
    @field_serializer(
        "gmt_created",
        "gmt_modified",
        "favorite_at",
        check_fields=False,
        when_used="json",
    )
    def _serialize_utc_datetime(self, value: datetime) -> str:
        return _utc_datetime(value)


class SpaceItem(_UtcResponseModel):
    """Summary of a Space visible to the current user."""

    space_id: int = Field(description="Unique numeric identifier of the Space.")
    space_code: str = Field(description="Stable external code of the Space.")
    space_name: str = Field(description="Display name of the Space.")
    space_type: SpaceType = Field(description="Ownership model of the Space.")
    current_user_role: SpaceRole | None = Field(
        description="Current user's role, or null when the user has not joined."
    )
    join_status: SpaceJoinStatus = Field(
        description="Current user's membership or application state."
    )
    member_count: int = Field(description="Number of members in the Space.")
    owner_count: int = Field(description="Number of owners in the Space.")
    gmt_modified: datetime = Field(
        description="UTC time when the Space metadata was last modified.",
        json_schema_extra={"format": "date-time"},
    )


class SpaceCreated(_UtcResponseModel):
    """Details returned after a Space is created."""

    space_id: int = Field(description="Unique numeric identifier of the Space.")
    space_code: str = Field(description="Stable external code of the Space.")
    space_name: str = Field(description="Display name of the Space.")
    space_type: SpaceType = Field(description="Ownership model of the Space.")
    current_user_role: SpaceRole = Field(description="Creator's role in the new Space.")
    is_creator: bool = Field(description="Whether the current user created the Space.")
    member_count: int = Field(description="Number of members in the Space.")
    owner_count: int = Field(description="Number of owners in the Space.")
    gmt_created: datetime = Field(
        description="UTC time when the Space was created.",
        json_schema_extra={"format": "date-time"},
    )
    gmt_modified: datetime = Field(
        description="UTC time when the Space metadata was last modified.",
        json_schema_extra={"format": "date-time"},
    )


class PersonalSpaceInitialized(SpaceCreated):
    """Result of ensuring that the current user's personal Space exists."""

    created: bool = Field(
        description="True when a new personal Space was created by this request."
    )


class CreateSpaceRequest(BaseModel):
    """Request for creating a shared team Space."""

    space_name: str = Field(
        min_length=1, max_length=128, description="Display name for the new Space."
    )


class SpaceMemberItem(_UtcResponseModel):
    """Membership details for one user in a Space."""

    user_id: str = Field(description="Identifier of the member user.")
    user_name: str | None = Field(
        default=None, description="Account name of the member, when available."
    )
    display_name: str | None = Field(
        default=None, description="Display name of the member, when available."
    )
    role: SpaceRole = Field(description="Role currently held by the member.")
    is_creator: bool = Field(
        description="Whether this member originally created the Space."
    )
    gmt_modified: datetime = Field(
        description=(
            "UTC time when this membership relation was created or its role "
            "was last changed."
        ),
        json_schema_extra={"format": "date-time"},
    )


class AddSpaceMemberRequest(BaseModel):
    """Request for adding a user to a Space."""

    member_user_id: str = Field(
        min_length=1, max_length=256, description="Identifier of the user to add."
    )
    role: SpaceRole = Field(
        default=SpaceRole.MEMBER,
        description="Role granted when the member is added; defaults to MEMBER.",
    )


class UpdateSpaceMemberRoleRequest(BaseModel):
    """Request for changing a Space member's role."""

    role: SpaceRole = Field(description="New role to assign to the member.")


class SpaceMemberMutationResult(BaseModel):
    """Membership state returned after an add or role update."""

    space_id: int = Field(description="Identifier of the affected Space.")
    user_id: str = Field(description="Identifier of the affected member.")
    role: SpaceRole = Field(description="Role held after the operation.")


class SpaceMemberDeletedResult(BaseModel):
    """Confirmation that a member was removed from a Space."""

    space_id: int = Field(description="Identifier of the affected Space.")
    user_id: str = Field(description="Identifier of the removed member.")
    deleted: bool = Field(
        description="Whether the membership was deleted.", default=True
    )


class FavoriteTargetRequest(BaseModel):
    """Marketplace target saved in the current user's favorites."""

    target_type: FavoriteTargetType = Field(
        description="Marketplace category of the target."
    )
    target_code: str = Field(
        min_length=1,
        max_length=128,
        description="Stable marketplace code of the target.",
    )


class SearchFavoritesRequest(BaseModel):
    """Filters and pagination for the current user's favorites."""

    target_type: FavoriteTargetType | None = Field(
        default=None, description="Target category filter, or null for all categories."
    )
    keyword: str | None = Field(
        default=None,
        max_length=128,
        description="Optional case-insensitive target-code search text.",
    )
    page_no: int = Field(default=1, ge=1, description="One-based page number.")
    page_size: int = Field(
        default=20, ge=1, le=100, description="Maximum items returned per page."
    )


class FavoriteAddedResult(BaseModel):
    """Favorite state returned after a target is added."""

    favorite_id: int = Field(description="Identifier of the favorite record.")
    target_type: FavoriteTargetType = Field(
        description="Marketplace category of the target."
    )
    target_code: str = Field(description="Stable marketplace code of the target.")
    is_favorited: bool = Field(
        default=True, description="Whether the target is now favorited."
    )


class FavoriteCanceledResult(BaseModel):
    """Favorite state returned after a target is removed."""

    target_type: FavoriteTargetType = Field(
        description="Marketplace category of the target."
    )
    target_code: str = Field(description="Stable marketplace code of the target.")
    is_favorited: bool = Field(
        default=False, description="Whether the target remains favorited."
    )


class MarketFavoriteItem(_UtcResponseModel):
    """One marketplace favorite saved by the current user."""

    favorite_id: int = Field(description="Identifier of the favorite record.")
    target_type: FavoriteTargetType = Field(
        description="Marketplace category of the target."
    )
    target_code: str = Field(description="Stable marketplace code of the target.")
    favorite_at: datetime = Field(
        description="UTC time when the target was added to this Space's favorites.",
        json_schema_extra={"format": "date-time"},
    )
    is_favorited: bool = Field(
        default=True, description="Whether the target is currently favorited."
    )
