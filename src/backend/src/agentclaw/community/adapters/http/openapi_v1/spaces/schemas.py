"""HTTP schemas for spaces, members and market favorites."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_serializer

from agentclaw.community.core.market_favorites.models import FavoriteTargetType
from agentclaw.community.core.spaces.models import (
    SpaceJoinStatus,
    SpaceRole,
    SpaceType,
)


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
    space_id: int
    space_code: str
    space_name: str
    space_type: SpaceType
    current_user_role: SpaceRole | None
    join_status: SpaceJoinStatus
    member_count: int
    owner_count: int
    gmt_modified: datetime = Field(
        description="UTC time when the Space metadata was last modified.",
        json_schema_extra={"format": "date-time"},
    )


class SpaceCreated(_UtcResponseModel):
    space_id: int
    space_code: str
    space_name: str
    space_type: SpaceType
    current_user_role: SpaceRole
    is_creator: bool
    member_count: int
    owner_count: int
    gmt_created: datetime = Field(
        description="UTC time when the Space was created.",
        json_schema_extra={"format": "date-time"},
    )
    gmt_modified: datetime = Field(
        description="UTC time when the Space metadata was last modified.",
        json_schema_extra={"format": "date-time"},
    )


class PersonalSpaceInitialized(SpaceCreated):
    created: bool


class CreateSpaceRequest(BaseModel):
    space_name: str = Field(min_length=1, max_length=128)


class SpaceMemberItem(_UtcResponseModel):
    user_id: str
    role: SpaceRole
    is_creator: bool
    gmt_modified: datetime = Field(
        description=(
            "UTC time when this membership relation was created or its role "
            "was last changed."
        ),
        json_schema_extra={"format": "date-time"},
    )


class AddSpaceMemberRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=256)
    role: SpaceRole = Field(
        default=SpaceRole.MEMBER,
        description="Role granted when the member is added; defaults to MEMBER.",
    )


class UpdateSpaceMemberRoleRequest(BaseModel):
    role: SpaceRole


class SpaceMemberMutationResult(BaseModel):
    space_id: int
    user_id: str
    role: SpaceRole


class SpaceMemberDeletedResult(BaseModel):
    space_id: int
    user_id: str
    deleted: bool = True


class FavoriteTargetRequest(BaseModel):
    target_type: FavoriteTargetType
    target_code: str = Field(min_length=1, max_length=128)


class SearchFavoritesRequest(BaseModel):
    target_type: FavoriteTargetType | None = None
    keyword: str | None = Field(default=None, max_length=128)
    page_no: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class FavoriteAddedResult(BaseModel):
    favorite_id: int
    target_type: FavoriteTargetType
    target_code: str
    is_favorited: bool = True


class FavoriteCanceledResult(BaseModel):
    target_type: FavoriteTargetType
    target_code: str
    is_favorited: bool = False


class MarketFavoriteItem(_UtcResponseModel):
    favorite_id: int
    target_type: FavoriteTargetType
    target_code: str
    favorite_at: datetime = Field(
        description="UTC time when the target was added to this Space's favorites.",
        json_schema_extra={"format": "date-time"},
    )
    is_favorited: bool = True
