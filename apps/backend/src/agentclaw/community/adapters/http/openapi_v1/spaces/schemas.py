"""HTTP schemas for spaces, members and market favorites."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field, create_model, field_serializer

from agentclaw.community.adapters.http.openapi_v1.enums import _DocumentedEnum


class SpaceListScope(_DocumentedEnum):
    """Visibility scope used when listing Spaces."""

    ALL = "all"
    ACCESSIBLE = "accessible"

    __descriptions__ = {
        "all": "Return all live Spaces visible in the current environment.",
        "accessible": "Return the user's personal Space and active team memberships.",
    }


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
    display_name: str | None = Field(None, description="Current staff-directory display name.")
    role: SkillRole = Field(description="Role held by the user for this Skill.")


class SkillActorPermissions(BaseModel):
    """ACL/Grant qualifications; current command state is checked separately."""

    edit_draft: bool = Field(description="Actor may request a Draft edit command.")
    publish_draft: bool = Field(description="Actor may request Draft publication.")
    delete_draft: bool = Field(description="Actor may request Draft deletion.")
    create_upgrade_draft: bool = Field(
        description="Actor may request creation of an upgrade Draft."
    )
    offline_skill: bool = Field(
        description="Actor may request terminal local Skill Offline."
    )
    copy_offline_skill: bool = Field(
        default=False,
        description="Actor may copy an Offline Skill's exact Published Version."
    )
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
    pending_editor_request: SkillEditorRequestCreated | None = Field(
        default=None,
        description="Current actor's pending editor request, when one exists.",
    )


class SpaceSkillGrants(BaseModel):
    """Complete active Grant set for one Space Skill."""

    owner: SkillGrantItem = Field(description="The unique active OWNER Grant.")
    managers: list[SkillGrantItem] = Field(
        description="All active MANAGER Grants, ordered by user identifier."
    )
    actor: SkillGrantActor = Field(description="Current caller role and permissions.")


class DraftEditLeaseState(_DocumentedEnum):
    """Actor-relative state of a Space Skill Draft's permanent edit Lease."""

    NOT_REQUIRED = "NOT_REQUIRED"
    FREE = "FREE"
    HELD_BY_ME = "HELD_BY_ME"
    HELD_BY_OTHER = "HELD_BY_OTHER"

    __descriptions__ = {
        "NOT_REQUIRED": "Personal Space Drafts do not use an edit Lease.",
        "FREE": "No editor currently holds the Team Draft Lease.",
        "HELD_BY_ME": "The current actor holds the Team Draft Lease.",
        "HELD_BY_OTHER": "Another OWNER or MANAGER holds the Team Draft Lease.",
    }


class DraftEditLeaseResource(BaseModel):
    """Live Lease resource; only its current holder receives the fencing token."""

    required: bool = Field(description="Whether this Space Draft requires a Lease.")
    state: DraftEditLeaseState = Field(description="Actor-relative Lease state.")
    holder_user_id: str | None = Field(
        default=None, description="Current holder identifier, or null when unheld."
    )
    fencing_token: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Current fencing token only when the caller holds the Lease; list "
            "summaries and other actors never receive it."
        ),
    )


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
    retain_previous_owner_as_manager: bool = Field(
        default=False,
        description="Whether to retain the previous OWNER as an active MANAGER.",
    )


class CreateSkillEditorRequest(BaseModel):
    """Request Manager edit access to a Team Space Skill."""

    reason: str = Field(
        min_length=1,
        max_length=512,
        description="Reason for requesting Skill edit access.",
    )


class SkillEditorRequestCreated(BaseModel):
    """Pending Work Order created for a Skill editor application."""

    work_order_id: int = Field(description="Created Work Order identifier.")
    work_order_no: str = Field(description="Human-readable Work Order number.")
    status: Literal["PENDING"] = Field(description="Initial Work Order status.")


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
        "published_at",
        "offline_at",
        check_fields=False,
        when_used="json",
    )
    def _serialize_utc_datetime(self, value: datetime | None) -> str | None:
        return _utc_datetime(value) if value is not None else None


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


class InitializePersonalSpaceRequest(BaseModel):
    """Optional controls for initializing the current user's personal Space."""

    skip_sc: bool = Field(
        default=False,
        alias="skipSC",
        description="Whether to skip creating or binding the corresponding Skill Center Team.",
    )


class CreateSpaceRequest(BaseModel):
    """Request for creating a shared team Space."""

    space_name: str = Field(
        min_length=1, max_length=128, description="Display name for the new Space."
    )
    skip_sc: bool = Field(
        default=False,
        alias="skipSC",
        description="Whether to skip creating the corresponding Skill Center Team.",
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


class DraftEditLeaseSummary(BaseModel):
    """List-card Lease state; fencing tokens only exist on the live resource."""

    required: bool = Field(description="Whether this Space Draft requires a Lease.")
    state: DraftEditLeaseState = Field(description="Actor-relative Lease state.")
    holder_user_id: str | None = Field(
        default=None, description="Current holder identifier, or null when unheld."
    )
    holder_display_name: str | None = Field(
        default=None, description="Current holder display name, when available."
    )


class SkillLifecycleStatus(_DocumentedEnum):
    """Recoverable lifecycle of a Space Skill asset."""

    DRAFT_ONLY = "DRAFT_ONLY"
    PUBLISHED = "PUBLISHED"
    OFFLINE = "OFFLINE"

    __descriptions__ = {
        "DRAFT_ONLY": "The Skill has a Draft but no Published Version.",
        "PUBLISHED": "The Skill has at least one Published Version and is online.",
        "OFFLINE": "The Skill is offline, retains its Published history, and is not editable.",
    }


class SkillDraftStatus(_DocumentedEnum):
    """Mutability state of the current Draft."""

    EDITING = "EDITING"
    FROZEN = "FROZEN"

    __descriptions__ = {
        "EDITING": "The Draft accepts revision-CAS edit commands.",
        "FROZEN": "Publication has frozen the Draft against mutation.",
    }


class SkillDraftSourceKind(_DocumentedEnum):
    """Immutable source used to initialize the current Draft."""

    FOLDER = "FOLDER"
    GIT = "GIT"
    PUBLISHED_VERSION = "PUBLISHED_VERSION"

    __descriptions__ = {
        "FOLDER": "Created from a browser folder upload.",
        "GIT": "Created or refreshed from a frozen Git snapshot.",
        "PUBLISHED_VERSION": "Copied from the latest exact Published Version.",
    }


class SkillOwnerSummary(BaseModel):
    """The unique active owner of one Space Skill."""

    user_id: str = Field(description="Unique active OWNER user identifier.")
    display_name: str | None = Field(default=None, description="Owner display name.")


class SkillVersionSummary(_UtcResponseModel):
    """Identity and completion time of one Published Version."""

    version: int = Field(ge=1, description="Business version ordinal.")
    sc_version_number: str = Field(description="Exact SkillCenter version number.")
    published_at: datetime = Field(description="UTC publication completion time.")


class SkillVersionDetail(SkillVersionSummary):
    """Published Version metadata addressed by business ordinal."""

    name: str = Field(description="Name captured from this Version's SKILL.md.")
    description: str | None = Field(
        default=None, description="Description captured from this Version's SKILL.md."
    )
    mcp_dependencies: list[str] = Field(
        default_factory=list,
        description="MCP dependency codes captured in immutable Version metadata.",
    )


class PublishedVersionFileTree(BaseModel):
    """Complete file tree of one exact Published Version."""

    version: int = Field(ge=1, description="Business version ordinal.")
    files: list[DraftFileItem] = Field(description="Files ordered by POSIX path.")


class PublishedVersionFileContent(BaseModel):
    """One UTF-8 text file from an exact Published Version."""

    version: int = Field(ge=1, description="Business version ordinal.")
    path: str = Field(description="Normalized POSIX-relative file path.")
    content: str = Field(description="UTF-8 decoded file content.")


class ConsumableSpaceSkill(BaseModel):
    """Canonical-ready, online Space Skill available to the workshop."""

    skill_id: str = Field(description="Unique numeric Skill identifier.")
    name: str = Field(description="Name of the latest Published Version.")
    description: str | None = Field(
        default=None, description="Description of the latest Published Version."
    )
    latest_published_version: SkillVersionSummary = Field(
        description="Latest Canonical-ready Published Version."
    )


class SkillDraftSummary(BaseModel):
    """Current immutable Draft revision and target Version."""

    target_version: int = Field(ge=1, description="Target business version ordinal.")
    status: SkillDraftStatus = Field(description="Current Draft mutability state.")
    revision_id: str = Field(description="Current immutable Draft revision identity.")


class SkillDraftDetail(SkillDraftSummary):
    """Complete authoring metadata for the current Draft revision."""

    model_config = ConfigDict(from_attributes=True)
    name: str = Field(description="Immutable Skill name parsed from SKILL.md.")
    description: str | None = Field(
        default=None, description="Draft description parsed from SKILL.md."
    )
    source_kind: SkillDraftSourceKind = Field(
        description="Source used to initialize this Draft."
    )
    source_repo_url: str | None = Field(
        default=None, description="Credential-free Git repository URL, when Git-backed."
    )
    source_branch: str | None = Field(
        default=None, description="Resolved Git branch frozen for refresh."
    )
    source_commit_sha: str | None = Field(
        default=None, description="Exact Git commit represented by this revision."
    )
    source_subdir: str | None = Field(
        default=None, description="Selected normalized repository subdirectory."
    )


class PublicationAttemptSummary(BaseModel):
    """Current non-terminal publication attempt, when one exists."""

    attempt_id: str = Field(description="Stable publication attempt identifier.")
    target_version: int = Field(ge=1, description="Target business version ordinal.")
    status: str = Field(description="Current final-contract Attempt state.")


class SpaceSkillSummary(_UtcResponseModel):
    """Stable domain summary for one Space-owned Skill."""

    skill_id: str = Field(description="Unique numeric Skill identifier.")
    skill_uuid: str = Field(description="Stable Skill identity across versions.")
    name: str = Field(description="Skill name projected from SKILL.md.")
    description: str | None = Field(
        default=None, description="Skill description projected from SKILL.md."
    )
    lifecycle_status: SkillLifecycleStatus = Field(
        description="Recoverable lifecycle derived from Published and Offline facts."
    )
    space_type: SpaceType = Field(
        description="Whether the Skill belongs to a personal or team Space."
    )
    actor: SkillGrantActor = Field(
        description="Current caller's Grant role and ACL/Grant qualifications."
    )
    owner: SkillOwnerSummary = Field(description="Unique active Skill owner.")
    latest_published_version: SkillVersionSummary | None = Field(
        default=None,
        description="Latest Published Version, or null before first publish.",
    )
    draft: SkillDraftSummary | None = Field(
        default=None, description="Current Draft summary, or null when no Draft exists."
    )
    active_publication: PublicationAttemptSummary | None = Field(
        default=None, description="Current non-terminal Attempt, or null."
    )
    lease_summary: DraftEditLeaseSummary | None = Field(
        default=None,
        description="List-only Lease state without a fencing token; null when no Draft exists.",
    )
    gmt_created: datetime = Field(
        description="UTC time when the Skill was created.",
        json_schema_extra={"format": "date-time"},
    )
    gmt_modified: datetime = Field(
        description="UTC time when the Skill metadata was last modified.",
        json_schema_extra={"format": "date-time"},
    )


class SpaceSkillDetail(SpaceSkillSummary):
    """Authoring detail for one Space-owned Skill asset."""

    draft: SkillDraftDetail | None = Field(
        default=None, description="Complete current Draft facts, or null."
    )
    source: Literal["FOLDER", "GIT", "COPY"] = Field(
        description="Original Space Skill creation source."
    )
    offline_at: datetime | None = Field(
        default=None, description="UTC terminal local Offline time, or null."
    )
    offline_by: str | None = Field(
        default=None, description="Actor that placed the Skill Offline, or null."
    )


class SkillOfflineBlockerKind(_DocumentedEnum):
    """Category for an Offline blocker or a diagnostic warning."""

    DRAFT = "DRAFT"
    PUBLICATION = "PUBLICATION"
    MEMBERSHIP = "MEMBERSHIP"
    INSTALLATION = "INSTALLATION"
    SERVICE_ARTIFACT = "SERVICE_ARTIFACT"
    UNKNOWN_ARTIFACT = "UNKNOWN_ARTIFACT"

    __descriptions__ = {
        "DRAFT": "A mutable or frozen Draft already exists.",
        "PUBLICATION": "A publication Attempt is still in progress or unknown.",
        "MEMBERSHIP": "An ordinary or Default SkillSet still contains the Skill.",
        "INSTALLATION": "A Bot still has an effective Skill Installation.",
        "SERVICE_ARTIFACT": "A live Service Bot can replay this exact Skill Version.",
        "UNKNOWN_ARTIFACT": (
            "Artifact lineage could not be proved complete and valid; "
            "returned only as a non-blocking diagnostic warning."
        ),
    }


class SkillOfflineImpactItem(BaseModel):
    """One explicit blocker or diagnostic warning for recoverable Offline."""

    kind: SkillOfflineBlockerKind = Field(
        description="Category of the blocking reference or lifecycle fact."
    )
    resource_id: str = Field(description="Stable blocker resource identifier.")
    display_name: str = Field(description="Human-readable blocker label.")


class SkillOfflineImpact(BaseModel):
    """Complete explicit blockers plus diagnostic warnings for recoverable Offline."""

    blocked: bool = Field(description="Whether at least one blocker exists.")
    total: int = Field(ge=0, description="Total explicit blockers across all categories.")
    counts: dict[str, int] = Field(
        description="Non-zero explicit blocker totals keyed by blocker category."
    )
    items: list[SkillOfflineImpactItem] = Field(
        description="Requested page of blockers in deterministic order."
    )
    warnings: list[SkillOfflineImpactItem] = Field(
        default_factory=list,
        description=(
            "Diagnostic findings that did not prove a live reference and therefore "
            "do not block Offline."
        ),
    )


class SkillOfflineResult(BaseModel):
    """Terminal local Offline result for the existing Skill."""

    changed: bool = Field(
        description="Whether this request newly moved the Skill Offline."
    )
    lifecycle_status: Literal["OFFLINE"] = Field(
        description="Current recoverable lifecycle state."
    )
    offline_at: datetime | None = Field(
        default=None, description="UTC time at which Offline was recorded."
    )


class ImportSpaceSkillFromGitRequest(BaseModel):
    """Credential-free Git snapshot coordinates for Space Skill creation."""

    git_url: str = Field(
        min_length=1, max_length=2048, description="Credential-free HTTPS Git URL."
    )
    branch: str | None = Field(
        default=None, max_length=512, description="Optional branch to resolve."
    )
    subdir: str | None = Field(
        default=None,
        max_length=1024,
        description="Optional normalized parent directory containing SKILL.md.",
    )


class DraftFileItem(BaseModel):
    """One file entry in an immutable Draft or Published tree."""

    model_config = ConfigDict(from_attributes=True)
    path: str = Field(description="Normalized POSIX-relative file path.")
    size: int = Field(ge=0, description="File size in bytes.")


class DraftFileTree(BaseModel):
    """Complete file tree for the current immutable Draft revision."""

    model_config = ConfigDict(from_attributes=True)
    revision_id: str = Field(description="Current immutable Draft revision identity.")
    files: list[DraftFileItem] = Field(description="Files ordered by POSIX path.")


class DraftFileContent(BaseModel):
    """One UTF-8 file from the current immutable Draft revision."""

    model_config = ConfigDict(from_attributes=True)
    path: str = Field(description="Normalized POSIX-relative file path.")
    content: str = Field(description="UTF-8 decoded file content.")
    revision_id: str = Field(description="Revision from which content was read.")


class SaveDraftFileRequest(BaseModel):
    """Revision-CAS request to replace one UTF-8 Draft file."""

    content: str = Field(description="Complete replacement UTF-8 text content.")
    expected_revision_id: str = Field(
        min_length=1,
        max_length=128,
        description="Revision the caller read and expects to replace.",
    )
    fencing_token: int | None = Field(
        default=None,
        ge=1,
        description="Current Team Lease token; null for Personal Space.",
    )


class DraftRevisionRequest(BaseModel):
    """Revision and optional Team Lease preconditions for a Draft mutation."""

    expected_revision_id: str = Field(
        min_length=1,
        max_length=128,
        description="Revision the caller expects to mutate.",
    )
    fencing_token: int | None = Field(
        default=None,
        ge=1,
        description="Current Team Lease token; null for Personal Space.",
    )


class DraftDeleteResult(BaseModel):
    """Result of deleting only a Draft or its unreferenced Skill aggregate."""

    model_config = ConfigDict(from_attributes=True)
    changed: bool = Field(description="Whether persisted state was deleted.")
    deleted_scope: Literal["DRAFT", "SKILL"] = Field(
        description="DRAFT preserves external facts; SKILL removes the empty aggregate."
    )


SpaceSkillFolderUpload = create_model(
    "Body_create_space_skill_from_folder_openapi_v1_bots_spaces__space_id__skills_post",
    __base__=BaseModel,
    __config__=ConfigDict(
        json_schema_extra={
            "description": "Files and aligned POSIX paths for one Space Skill folder."
        }
    ),
    files=(
        list[UploadFile],
        Field(description="All files from the selected Space Skill directory."),
    ),
    file_paths=(
        str,
        Field(
            description="JSON array of relative paths aligned one-to-one with files."
        ),
    ),
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


class PublicationAttemptState(_DocumentedEnum):
    """Persisted stage or terminal outcome of one Publication Attempt."""

    PREPARING = "PREPARING"
    SC_SUBMITTING = "SC_SUBMITTING"
    WAITING_SC = "WAITING_SC"
    MATERIALIZING = "MATERIALIZING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RESULT_UNKNOWN = "RESULT_UNKNOWN"

    __descriptions__ = {
        "PREPARING": "The frozen Draft package is being prepared.",
        "SC_SUBMITTING": "The one-shot Skill Center request has started.",
        "WAITING_SC": "Skill Center processing or exact metadata discovery is pending.",
        "MATERIALIZING": "The exact published Version is passing the Ready Gate.",
        "SUCCEEDED": "The exact Version is Published and ready for consumption.",
        "FAILED": "Publication was explicitly rejected and the Draft is editable.",
        "RESULT_UNKNOWN": "The external publication outcome is not yet confirmed.",
    }


class PublicationRecoveryState(_DocumentedEnum):
    """Availability of safe recovery for one Attempt."""

    AUTO_RETRYING = "AUTO_RETRYING"
    AVAILABLE = "AVAILABLE"
    NOT_AVAILABLE = "NOT_AVAILABLE"

    __descriptions__ = {
        "AUTO_RETRYING": "The backend is retrying automatically.",
        "AVAILABLE": "The caller may request recovery of this same Attempt.",
        "NOT_AVAILABLE": "No recovery action applies to the current state.",
    }


class PublicationRecoveryKind(_DocumentedEnum):
    """Backend-owned stage resumed by an Attempt retry."""

    PREPARATION = "PREPARATION"
    SC_STATUS_CHECK = "SC_STATUS_CHECK"
    MATERIALIZATION = "MATERIALIZATION"

    __descriptions__ = {
        "PREPARATION": "Resume package preparation before the first submission.",
        "SC_STATUS_CHECK": "Resume status and exact Version discovery without submitting again.",
        "MATERIALIZATION": "Resume the Ready Gate for the same exact Version.",
    }


class PublicationRecovery(BaseModel):
    """Safe recovery state selected by the backend."""

    state: PublicationRecoveryState = Field(
        description="Whether automatic or actor-triggered recovery is available."
    )
    kind: PublicationRecoveryKind | None = Field(
        default=None,
        description="The safe recovery stage; clients never choose its implementation.",
    )


class PublicationAttempt(_UtcResponseModel):
    """Durable progress resource for one Draft publication command."""

    attempt_id: str = Field(description="Stable Publication Attempt identifier.")
    target_version: int = Field(ge=1, description="Frozen business-version ordinal.")
    status: PublicationAttemptState = Field(
        description="Current persisted Publication state."
    )
    sc_version_number: str | None = Field(
        default=None, description="Frozen exact Skill Center version number."
    )
    recovery: PublicationRecovery = Field(
        description="Backend-owned recovery availability and stage."
    )
    error_code: str | None = Field(
        default=None, description="Stable persisted failure category, when present."
    )
    error_message: str | None = Field(
        default=None, description="Auditable failure detail, when present."
    )
    gmt_created: datetime = Field(description="UTC creation time.")
    gmt_modified: datetime = Field(description="UTC last-update time.")


class PublicationImpactItem(BaseModel):
    """One Bot potentially affected after a new Version is Published."""

    owner_id: str = Field(description="Owner of a potentially affected Bot.")
    bot_id: str = Field(description="Potentially affected Bot identifier.")
    bot_name: str | None = Field(
        default=None, description="Current Bot display name, when available."
    )
