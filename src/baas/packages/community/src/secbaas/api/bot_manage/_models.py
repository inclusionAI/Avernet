"""
Bot domain models.

Contains all bot-related models: BotConfig, BotClusterCreate, BotResponse,
BotListResponse, BotQuery, and all response schemas.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from secbaas.api import WithRequestId
from secbaas.api.device_manage import DeployConfig, DeviceInfo

from ._enums import SlaGrade


class BotConfig(BaseModel):
    """Typed model for baas_bot.extra_config JSON column."""

    model_config = ConfigDict(extra="allow")

    share_policy: dict | None = None
    sla_grade: str = SlaGrade.STANDARD

    @field_validator("sla_grade")
    @classmethod
    def validate_sla_grade(cls, v: str) -> str:
        allowed = {SlaGrade.STANDARD, SlaGrade.ENTERPRISE}
        if v not in allowed:
            msg = f"sla_grade must be one of {allowed}, got '{v}'"
            raise ValueError(msg)
        return v

    deploy_config: DeployConfig | None = None
    callback_timeout_seconds: int | None = Field(
        default=None,
        gt=0,
        le=3600,
        description="Max seconds to wait for device callback before marking record as failed",
    )
    entity_id: str = ""
    entity_type: str = "staff"
    auto_approve_publish: bool = Field(
        default=False,
        description="When True, auto-approve all publish stage gates after bot creation",
    )


class BotClusterCreate(BaseModel):
    """Create Bot Cluster Request (specify device count and template)"""

    bot_name: str = Field(..., min_length=1, max_length=1024, description="Bot name")
    bot_desc: str | None = Field(None, max_length=1024, description="Bot description")
    template_uuid: str = Field(
        ..., min_length=1, max_length=64, description="Device template UUID"
    )
    device_count: int = Field(default=1, ge=1, le=100, description="Device count")
    env: str = Field(default="prod", max_length=64, description="Environment")
    domain: str = Field(default="default", max_length=128, description="Domain")
    operator: str = Field(..., min_length=1, max_length=1024, description="Operator ID")
    config: BotConfig | None = Field(
        default=None,
        description="Bot configuration (entity_id, entity_type, deploy_config)",
    )


class BotResponse(BaseModel):
    """Bot Response - aligned with baas_bot table schema (BotRecord)."""

    id: int
    bot_uuid: str
    tenant: str
    env: str
    domain: str
    is_deleted: int
    creator: str
    modifier: str
    status: str
    name: str
    description: str | None
    template_uuid: str | None
    replica_desired: int
    replica_minimum: int
    replica_maximum: int
    auto_scaling_enabled: int
    sla_grade: str
    gmt_create: datetime
    gmt_modified: datetime
    config: BotConfig | None = None
    devices: list[DeviceInfo] = Field(
        default_factory=list, description="Associated devices"
    )

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class BotListResponse(BaseModel):
    """Bot List Response"""

    items: list[BotResponse]
    total: int
    page: int
    page_size: int


class BotQuery(BaseModel):
    """Bot Query Parameters"""

    entity_id: str | None = Field(None, description="Entity ID")
    entity_type: str | None = Field(None, description="Entity type")
    creator_id: str | None = Field(None, description="Creator user ID")
    owner_id: str | None = Field(None, description="Owner user ID")
    status: str | None = Field(None, description="Bot status")
    is_delete: int | None = Field(default=0, description="Soft delete flag")
    env: str | None = Field(None, description="Environment")
    public: str | None = Field(None, description="is public")


# ------------------------------------------------------------------ #
# Response schemas
# ------------------------------------------------------------------ #


class UpdateBotResponse(BotResponse):
    """Bot update response with optional publish_id for config-change updates."""

    publish_id: int | None = None


class CreateBotResponse(BotResponse, WithRequestId):
    """Bot creation response with publish workflow tracking."""

    publish_id: int | None = Field(
        default=None, description="ID of the publish workflow for device provisioning"
    )


class RestartBotResponse(BotResponse, WithRequestId):
    """Restart bot response with publish workflow tracking."""

    publish_id: int = Field(..., description="ID of the publish workflow")


class ScaleBotResponse(BotResponse, WithRequestId):
    """Scale bot response with publish workflow tracking."""

    target_count: int = Field(..., description="Target device count")
    publish_id: int = Field(..., description="ID of the publish workflow")


class BotDeviceStatusResponse(BaseModel):
    """Aggregate device status response for a bot.

    Provides a point-in-time snapshot of all devices attached to a bot,
    including the computed aggregate status and detailed device counts.
    """

    bot_uuid: str = Field(..., description="Bot UUID")
    bot_id: int = Field(..., description="Internal bot database ID")
    bot_status: str = Field(..., description="Current BotStatus of the bot record")
    device_status: str = Field(
        ...,
        description="Aggregate device status (ALL_ONLINE, ALL_OFFLINE, PARTIAL_ONLINE)",
    )
    device_count: int = Field(0, description="Total number of devices")
    active_count: int = Field(0, description="Devices in ACTIVE status")
    failed_count: int = Field(0, description="Devices in FAILED status")
    pending_count: int = Field(0, description="Devices in PENDING status")
    offline_count: int = Field(0, description="Devices in OFFLINE status")
    other_count: int = Field(0, description="Devices in any other status")


class UpdateDevicesResponse(BotResponse, WithRequestId):
    """Targeted device update response with publish workflow tracking."""

    publish_id: int = Field(..., description="ID of the publish workflow")


class StopBotResponse(BotResponse, WithRequestId):
    """Stop bot response with publish workflow tracking."""

    publish_id: int = Field(..., description="ID of the publish workflow")


class DestroyBotResponse(BotResponse, WithRequestId):
    """Destroy bot response with publish workflow tracking."""

    publish_id: int = Field(..., description="ID of the publish workflow")


class BotStartProgressResponse(BaseModel):
    """HTTP response model for device start progress query.

    Returned by GET /api/v1/bots/{bot_uuid}/start-progress.
    Contains the required progress field and optional dynamically-provided
    fields from the mng daemon passed through via extra="allow".

    BaaS only validates that ``progress`` is present; its type and value
    are defined by the mng daemon. All other fields in the response are
    mng-daemon-defined and passed through transparently.
    """

    model_config = ConfigDict(extra="allow")

    progress: str = Field(
        ...,
        description="Progress information — required field, value defined by mng daemon",
    )

    @field_validator("progress", mode="before")
    @classmethod
    def coerce_progress_to_str(cls, v: object) -> str:
        """Coerce non-string progress values (e.g., integer from mng daemon) to string.

        Explicitly rejects None to avoid silently converting a missing/null
        progress value from the mng daemon into the literal string "None".
        """
        if v is None:
            raise ValueError("progress must not be None")
        if not isinstance(v, str):
            return str(v)
        return v


class FetchStartProgressResult(BaseModel):
    """PaaS-layer result type for fetch_start_progress.

    Returned by PaasService.fetch_start_progress() and
    PaasServiceFacade.fetch_start_progress(). Structurally identical to
    BotStartProgressResponse but separate: the HTTP layer maps
    FetchStartProgressResult -> BotStartProgressResponse.

    BaaS only validates that ``progress`` is present; its value
    is defined by the mng daemon. All other fields are mng-daemon-defined
    and passed through via extra="allow".
    """

    model_config = ConfigDict(extra="allow")

    progress: str = Field(
        ...,
        description="Progress information — required field, value defined by mng daemon",
    )

    @field_validator("progress", mode="before")
    @classmethod
    def coerce_progress_to_str(cls, v: object) -> str:
        """Coerce non-string progress values (e.g., integer from mng daemon) to string.

        Explicitly rejects None to avoid silently converting a missing/null
        progress value from the mng daemon into the literal string "None".
        """
        if v is None:
            raise ValueError("progress must not be None")
        if not isinstance(v, str):
            return str(v)
        return v
