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

    # Canonical role for the current API contract.
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"
    # Compatibility-only input aliases for old clients and historical rows.
    # New clients and new writes must use ADMIN; responses are canonical ADMIN.
    OWNER = "OWNER"
    ADMINISTRATOR = "ADMINISTRATOR"

    __descriptions__ = {
        "ADMIN": "May manage the Space and its membership.",
        "MEMBER": "May use the Space without managing its membership.",
        "OWNER": "Legacy alias for ADMIN.",
        "ADMINISTRATOR": "Legacy alias for ADMIN.",
    }


class SkillRole(_DocumentedEnum):
    """Role held by the current user for one Space Skill."""

    OWNER = "OWNER"
    MANAGER = "MANAGER"

    __descriptions__ = {
        "OWNER": "Owns the Skill and may manage its edit grants.",
        "MANAGER": "May edit the Skill without managing its ownership.",
    }


class SkillGrantItem(BaseModel):
    """One active OWNER or MANAGER Grant."""

    user_id: str = Field(description="User holding this active Skill Grant.")
    role: SkillRole = Field(description="Role held by the user for this Skill.")


class SkillActorPermissions(BaseModel):
    """ACL/Grant qualifications; current command state is checked separately."""

    edit_draft: bool = Field(description="Actor may request a Draft edit command.")
    publish_draft: bool = Field(description="Actor may request Draft publication.")
    delete_draft: bool = Field(description="Actor may request Draft deletion.")
    create_upgrade_draft: bool = Field(
        description="Actor may request creation of an upgrade Draft."
    )
    retire_skill: bool = Field(description="Actor may request Skill retirement.")
    manage_grants: bool = Field(description="Actor may add or remove MANAGER Grants.")
    transfer_owner: bool = Field(description="Actor may request OWNER transfer.")
    request_edit_access: bool = Field(
        description="Actor may apply for a MANAGER Grant in a Team Space."
    )
    takeover_lease: bool = Field(
        description="Actor may request takeover of the current Draft edit Lease."
    )


class SkillGrantActor(BaseModel):
    """Current caller's Grant role and command qualifications."""

    skill_role: SkillRole | None = Field(
        default=None, description="Current active Skill Grant role, or null."
    )
    permissions: SkillActorPermissions = Field(
        description="ACL/Grant qualifications independent of current command state."
    )


class SpaceSkillGrants(BaseModel):
    """Complete active Grant set for one Space Skill."""

    owner: SkillGrantItem = Field(description="The unique active OWNER Grant.")
    managers: list[SkillGrantItem] = Field(
        description="All active MANAGER Grants, ordered by user identifier."
    )
    actor: SkillGrantActor = Field(description="Current caller role and permissions.")


class TransferSkillOwnerRequest(BaseModel):
    """Atomically move the unique OWNER slot to an active Space Member."""

    new_owner_user_id: str = Field(
        min_length=1,
        max_length=128,
        description="Active Space Member who will receive the unique OWNER Grant.",
    )
    reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=1024,
        description="Required audit reason when a Space administrator transfers ownership.",
    )


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


class MarketSource(_DocumentedEnum):
    """Marketplace system that owns the target identifier."""

    SKILLCENTER = "SKILLCENTER"
    TEAMCLAW = "TEAMCLAW"

    __descriptions__ = {
        "SKILLCENTER": "SkillCenter marketplace.",
        "TEAMCLAW": "TeamClaw marketplace.",
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
    creator_user_id: str = Field(
        description="Identifier of the user who created the Space."
    )
    creator_user_name: str | None = Field(
        default=None,
        description="Snapshot of the creator's user name, when available.",
    )
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


class SpaceSkillItem(_UtcResponseModel):
    """Skill card data owned by one Space."""

    skill_id: str = Field(description="Unique numeric Skill identifier.")
    skill_uuid: str = Field(description="Stable Skill identity across versions.")
    name: str = Field(description="Skill name projected from SKILL.md.")
    description: str | None = Field(
        default=None, description="Skill description projected from SKILL.md."
    )
    status: str | None = Field(
        default=None, description="Current Skill lifecycle status, when available."
    )
    draft_status: str | None = Field(
        default=None, description="Current draft status, when available."
    )
    space_type: SpaceType = Field(
        description="Whether the Skill belongs to a personal or team Space."
    )
    current_user_skill_role: SkillRole | None = Field(
        default=None,
        description="Current user's active Skill grant, or null when ungranted.",
    )
    can_edit: bool = Field(description="Whether the current user may edit this Skill.")
    can_grant: bool = Field(
        description="Whether the current user may grant team Skill edit access."
    )
    can_apply_edit: bool = Field(
        description=(
            "Whether the current user is eligible to apply for team Skill edit "
            "access; this does not represent a pending application state."
        )
    )
    gmt_created: datetime = Field(
        description="UTC time when the Skill was created.",
        json_schema_extra={"format": "date-time"},
    )
    gmt_modified: datetime = Field(
        description="UTC time when the Skill metadata was last modified.",
        json_schema_extra={"format": "date-time"},
    )


class AddSpaceMemberRequest(BaseModel):
    """Request for adding a user to a Space."""

    member_user_id: str = Field(
        min_length=1, max_length=256, description="Identifier of the user to add."
    )
    member_user_name: str | None = Field(
        default=None,
        max_length=128,
        description="Legacy compatibility field; ignored. The backend resolves the "
        "member nickname from member_user_id.",
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
    """Marketplace target to add to or remove from favorites."""

    market_source: MarketSource = Field(
        description="Source marketplace for this target identifier."
    )
    target_type: FavoriteTargetType = Field(
        description="Marketplace category of the target."
    )
    target_code: str = Field(
        min_length=1,
        max_length=128,
        description="Stable marketplace code of the target.",
    )


class SearchFavoritesRequest(BaseModel):
    """Filters and pagination for searching Space favorites."""

    market_source: MarketSource | None = Field(
        default=None, description="Marketplace source filter, or null for all sources."
    )
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
    market_source: MarketSource = Field(
        description="Source marketplace for this target identifier."
    )
    target_type: FavoriteTargetType = Field(
        description="Marketplace category of the target."
    )
    target_code: str = Field(description="Stable marketplace code of the target.")
    is_favorited: bool = Field(
        default=True, description="Whether the target is now favorited."
    )
    changed: bool = Field(
        description="Whether this request created the favorite record."
    )


class FavoriteCanceledResult(BaseModel):
    """Favorite state returned after a target is removed."""

    market_source: MarketSource = Field(
        description="Source marketplace for this target identifier."
    )
    target_type: FavoriteTargetType = Field(
        description="Marketplace category of the target."
    )
    target_code: str = Field(description="Stable marketplace code of the target.")
    is_favorited: bool = Field(
        default=False, description="Whether the target remains favorited."
    )
    changed: bool = Field(
        description="Whether this request removed an existing favorite record."
    )


class MarketFavoriteItem(_UtcResponseModel):
    """One marketplace favorite saved in a Space."""

    favorite_id: int = Field(description="Identifier of the favorite record.")
    market_source: MarketSource = Field(
        description="Source marketplace for this target identifier."
    )
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


class FavoriteStatusesRequest(BaseModel):
    """Batch query for favorite state of marketplace targets in one Space."""

    market_source: MarketSource = Field(
        description="Source marketplace shared by all requested targets."
    )
    target_type: FavoriteTargetType = Field(
        description="Marketplace category shared by every requested target."
    )
    target_codes: list[str] = Field(
        min_length=1,
        max_length=100,
        description="One to 100 stable marketplace target codes.",
    )


class FavoriteStatusesResult(BaseModel):
    """Targets currently favorited by any member of the selected Space."""

    market_source: MarketSource = Field(
        description="Source marketplace of the returned target identifiers."
    )
    target_type: FavoriteTargetType = Field(
        description="Marketplace category of the target identifiers."
    )
    favorited_target_codes: list[str] = Field(
        description="Requested target codes currently favorited in this Space."
    )
