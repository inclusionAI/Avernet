"""Strict browser commands: actor and environment never come from the body."""

from agentclaw.community.kernel.knowledge import require_knowledge_id
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EnablementRequest(StrictBody):
    target_owner_id: str = Field(min_length=1, max_length=128)
    enabled: bool
    expected_revision: int = Field(ge=0)

    @field_validator("target_owner_id")
    @classmethod
    def identifier(cls, value):
        return require_knowledge_id(value, "identifier")


class CatalogItem(BaseModel):
    model_config = {"extra": "allow"}

    space_id: str = Field(description="provider space identifier")
    space_name: str = Field(description="Existing workspace space name")
    kb_id: str = Field(description="Canonical provider KB identifier")
    name: str = Field(description="Canonical provider KB name")
    kgbase_status: str = Field(description="provider provisioning status")
    can_manage: bool = Field(description="provider native KB manage permission")
    description: str | None = Field(
        None, description="Stored description, never sample text"
    )
    document_count: int | None = Field(
        None, description="provider document count, not page length"
    )
    authorized_bot_count: int | None = Field(
        None, description="provider current grant count, including missing Bots"
    )
    created_at: str | None = Field(
        None, description="Real creation timestamp, not a modification fallback"
    )
    updated_at: str | None = Field(
        None, description="Business modification timestamp; currently unavailable"
    )
    updated_at_basis: str | None = Field(
        None, description="Currently unavailable; never synthesized from created_at"
    )


class CatalogPageResponse(BaseModel):
    model_config = {"extra": "allow"}

    items: list[CatalogItem] = Field(
        description="Current bounded provider catalog page"
    )
    knowledge_bases: list[CatalogItem] = Field(
        description="Compatibility alias of items"
    )
    next_cursor: str | None = Field(
        description="provider cursor, preserved without walking subsequent pages"
    )


class BotDisplay(BaseModel):
    model_config = {"extra": "allow"}

    bot_id: str = Field(description="Business Bot identifier; not globally unique")
    owner_id: str = Field(description="Owner identity; composite key with bot_id")
    bot_name: str | None = Field(description="Existing Bot name or null")
    avatar_url: str | None = Field(description="Existing Bot avatar or null")
    owner_name: str | None = Field(
        description="Existing owner/member display name or null"
    )
    bot_type: str | None = Field(description="Existing Bot type or null")
    space_id: str = Field(description="Authorized workspace space scope")
    space_name: str = Field(description="Existing workspace space name")


class AuthorizedBot(BotDisplay):
    id: int | str = Field(description="Canonical grant identifier")
    kb_id: str = Field(description="Canonical grant KB scope")
    platform: str = Field(description="Current grant platform")
    platform_env: str = Field(description="Current grant server environment")
    user_id: str = Field(description="Canonical grant owner identity")
    bot_owner_id: str = Field(description="Read alias of user_id")
    granted: bool = Field(description="Current effective grant fact, always true")
    revision: int = Field(description="provider grant CAS revision")
    updated_by: str | None = Field(description="Actual grant updater")
    bot_available: bool = Field(
        description="Bot metadata exists in the current workspace directory scope"
    )
    metadata_missing: bool = Field(
        description="No metadata in the current scope; grant is still retained"
    )
    enabled: bool | None = Field(
        description="Stored enablement; null when Bot metadata is missing"
    )


class AuthorizedBotPageResponse(BaseModel):
    model_config = {"extra": "allow"}

    items: list[AuthorizedBot] = Field(
        description="This grant page with bounded workspace display enrichment"
    )
    next_cursor: int | None = Field(
        description="Original provider grant cursor, including all-missing metadata pages"
    )


class BotCandidatePageResponse(BaseModel):
    items: list[BotDisplay] = Field(
        description="Same-space candidate page after provider KB MANAGE"
    )
    next_cursor: int | None = Field(description="workspace Bot primary-key cursor")


class GrantRequest(StrictBody):
    target_owner_id: str = Field(min_length=1, max_length=128)
    granted: bool
    expected_revision: int = Field(ge=0)

    @field_validator("target_owner_id")
    @classmethod
    def identifier(cls, value):
        return require_knowledge_id(value, "identifier")


class GrantStateTarget(StrictBody):
    bot_id: str = Field(min_length=1, max_length=128)
    owner_id: str = Field(min_length=1, max_length=128)

    @field_validator("bot_id", "owner_id")
    @classmethod
    def identifier(cls, value):
        return require_knowledge_id(value, "identifier")


class GrantStatesRequest(StrictBody):
    targets: list[GrantStateTarget] = Field(min_length=1, max_length=20)

    @field_validator("targets")
    @classmethod
    def unique(cls, value):
        if len({(item.bot_id, item.owner_id) for item in value}) != len(value):
            raise ValueError("Duplicate Bot identity")
        return value


class ConversationKnowledgeItem(BaseModel):
    kb_id: str
    name: str


class ConversationKnowledgePage(BaseModel):
    items: list[ConversationKnowledgeItem]
    next_cursor: int | None
