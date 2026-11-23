from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.contracts import ExtensibleResponse
from agentclaw.community.adapters.http.openapi_v1.bots.schemas_metadata import BotMetadataId


class ManagedBotTarget(BaseModel):
    """Exact current Owner and Bot identity of a managed draft runtime."""
    model_config = ConfigDict(extra='forbid')
    bot_id: BotMetadataId = Field(description='Source Bot identifier.')
    owner_id: BotMetadataId = Field(description='Current Bot Owner identifier, not the calling operator.')


class ManagedBotScope(ManagedBotTarget):
    """Template-scoped Bot address checked against its persisted snapshot."""
    template_key: BotMetadataId = Field(description='Expected creation template key; mismatches are refused.')


class RestartExtraConfigs(BaseModel):
    """Complete merged template snapshot for the existing restart lifecycle."""
    model_config = ConfigDict(extra='forbid')
    template_config: dict[str, Any] = Field(description='Complete snapshot, merged by the trusted caller; never a partial patch.')
    confirmed_template_update: Literal['true'] = Field(description='Explicit string confirmation to apply the snapshot.')
    expected_template_config_hash: str = Field(pattern=r'^[0-9a-f]{64}$', description='Hash from the snapshot read. A changed snapshot or downgrade is refused with 409 under the persistence lock.')


class ManagedBotRestart(ManagedBotScope):
    """Restart one draft Bot; acceptance is not readiness or completion."""
    stage: Literal['draft'] = Field(default='draft', description='Only the editable draft runtime can be restarted.')
    extra_configs: RestartExtraConfigs | None = Field(default=None, description='Omit for a normal restart without updating the snapshot.')


class ManagedBotStatusQueries(BaseModel):
    """Bounded batch of exact draft addresses under one template."""
    model_config = ConfigDict(extra='forbid')
    template_key: BotMetadataId = Field(description='Expected template key of every target.')
    bots: list[ManagedBotTarget] = Field(min_length=1, max_length=20, description='At most 20 targets; duplicate pairs are ignored.')


class ManagedBotStatus(ExtensibleResponse):
    """Runtime readiness and optional engine-owned restart operation progress."""
    bot_id: str = Field(description='Source Bot identifier.')
    owner_id: str = Field(description='Current Bot Owner identifier.')
    status: str = Field(description='Runtime lifecycle status, distinct from restart operation status.')
    is_ready: bool = Field(description='Existing runtime readiness policy; ACTIVE alone is not restart completion.')


class TemplateBotQueries(BaseModel):
    """Discover Bot identities by creation template without returning credentials."""
    model_config = ConfigDict(extra='forbid')
    template_key: BotMetadataId = Field(description='Creation template key, across template versions in the current environment.')
    offset: int = Field(default=0, ge=0, le=1000000, description='Zero-based result offset after filtering.')
    limit: int = Field(default=20, ge=1, le=100, description='Maximum number of stage rows, at most 100.')
    search: str = Field(default='', max_length=256, description='Literal substring of Bot name, Bot ID or Owner.')
    stage: Literal['', 'draft', 'prestable', 'online'] = Field(default='', description='Empty selects all; prestable denotes the verification runtime.')
    outdated_before: int | None = Field(default=None, ge=0, description='Only known snapshot version IDs below this ID; unknown versions are excluded.')


class TemplateBotRow(BaseModel):
    """Display-only metadata for one Bot runtime stage."""
    bot_id: str = Field(description='Source Bot identifier.')
    owner_id: str = Field(description='Current Bot Owner identifier.')
    owner_name: str | None = Field(default=None, description='Saved Owner display name, when known.')
    bot_name: str | None = Field(default=None, description='Saved Bot name.')
    engine: str = Field(description='Active engine type.')
    bot_type: str | None = Field(default=None, description='Bot kind, such as personal or service.')
    env: str = Field(description='Bot platform environment; independent of publication stage.')
    status: str = Field(description='Source Bot runtime lifecycle status; not a publication health probe.')
    stage: str = Field(description='draft, prestable or online; published stages are read-only.')
    publication_id: int | None = Field(default=None, description='Publication identity for published rows; absent on draft.')
    template_version_id: int | None = Field(default=None, description='This stage snapshot version ID, or unknown; never borrowed from draft.')
    template_version: str | None = Field(default=None, description='This stage snapshot version label, when recorded.')


class ManagedBotSnapshot(BaseModel):
    """Sensitive complete snapshot available only to approved server applications."""
    template_config: dict[str, Any] = Field(description='Persisted draft template snapshot, for server-side merging only; do not forward to a browser.')
    template_config_hash: str = Field(description='Opaque snapshot revision hash used to prevent stale replacement.')
    env: str = Field(description='Authoritative platform environment serving this snapshot.')
    bot_name: str | None = Field(default=None, description='Saved Bot name for legacy form migration.')
    active_engine: str | None = Field(default=None, description='Active engine for existing legacy migration policy.')
    template_type: str | None = Field(default=None, description='Creation template type for existing legacy migration policy.')
    bot_type: str | None = Field(default=None, description='Bot lifecycle kind; desktop lifecycle is managed separately.')
