"""Device API Pydantic schemas — 对外 API 契约.

All Pydantic models for the Device API are defined here.
This is the single source of truth for frontend ↔ backend contracts.

According to README.md:
- Only Pydantic BaseModel + Field
- NO business logic
- Descriptions for OpenAPI docs
- Do NOT import core/plugin_api/plugins
"""

import json
from datetime import datetime
from typing import Any, Generic, TypeVar

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


# =============================================================================
# API-layer Enums — 重新定义，不依赖 core 层
# 根据 README.md 规范：schemas.py 不 import core/plugin_api/plugins
# =============================================================================

class EntityType(StrEnum):
    """Entity type enumeration."""
    STAFF = "staff"
    TEAM = "team"
    PROJ = "proj"


class Env(StrEnum):
    """Environment enumeration."""
    DEV = "dev"
    PRE = "pre"
    PROD = "prod"

    @classmethod
    def from_string(cls, value: str) -> "Env":
        """Create Env from string."""
        value = value.lower()
        if value == "dev":
            return cls.DEV
        elif value == "pre":
            return cls.PRE
        elif value == "prod":
            return cls.PROD
        raise ValueError(f"Invalid env value: {value}. Must be 'dev', 'pre', or 'prod'.")


class DeviceBindingStatus(StrEnum):
    """Device binding status enumeration."""
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    RELEASED = "RELEASED"


T = TypeVar("T")

from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE  # noqa: E402,F401


# =============================================================================
# Base Response
# =============================================================================

class ApiResponse(BaseModel, Generic[T]):
    """Unified API response format."""
    success: bool = Field(..., description="Request success status")
    message: str = Field(..., description="Response message")
    error_code: int = Field(..., description="Error code (200 for success)")
    data: T | None = Field(None, description="Response data")


# =============================================================================
# Request Models
# =============================================================================

class EngineOptions(BaseModel):
    """Engine startup options."""
    model_config = ConfigDict(extra="forbid")

    skills_tar_url: str | None = Field(None, description="Skills tarball URL")
    workspace_tar_url: str | None = Field(None, description="Workspace tarball URL")


class ApplyDeviceRequest(BaseModel):
    """Request to apply/allocate a device."""
    model_config = ConfigDict(extra="forbid")

    apply_reason: str | None = Field(None, description="Reason for applying device")
    entity_id: str | None = Field(None, description="Entity ID (user or team)")
    entity_type: EntityType | None = Field(None, description="Entity type")
    bot_id: str | None = Field("default", description="Bot ID")
    engine: str = Field(DEFAULT_ENGINE_TYPE, description="Engine type (openclaw/moltis/hermes/aicoding)")
    engine_options: EngineOptions | None = Field(None, description="Engine options")


class ReleaseDeviceRequest(BaseModel):
    """Request to release a device."""
    model_config = ConfigDict(extra="forbid")

    release_reason: str | None = Field(None, description="Reason for releasing device")


class ConfirmDeviceAliveRequest(BaseModel):
    """Device alive callback request."""
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., description="Device ID")


class ReportDeviceStatusRequest(BaseModel):
    """Device status report request."""
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., description="Device ID")
    status: str = Field(..., description="Status (STARTING, SUCCEEDED, FAILED)")
    message: str | None = Field(None, description="Status message")


class ExecShellRequest(BaseModel):
    """Execute shell command request."""
    model_config = ConfigDict(extra="forbid")

    client_ids: list[str] = Field(..., description="List of client IDs")
    shell_cmd: str = Field(..., description="Shell command to execute")


class BatchSetDeviceEnvRequest(BaseModel):
    """Batch update device environment request."""
    binding_ids: list[int] = Field(..., description="List of binding IDs")
    env: str = Field(..., description="Target environment (dev/pre/prod)")


class BootstrapDeviceAuthRequest(BaseModel):
    """Device bootstrap auth callback request."""
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., description="Device ID (client_id)")
    bot_id: str = Field(..., description="Bot ID")
    owner_id: str = Field(..., description="Owner ID")


class RestartDeviceRequest(BaseModel):
    """Restart a specific device instance (§2 frontend-api-contract)."""
    model_config = ConfigDict(extra="forbid")

    device_uuid: str = Field(..., description="Instance uuid to restart (from instance list §1)")


# =============================================================================
# Response Data Models
# =============================================================================

class ExecShellResult(BaseModel):
    """Shell execution result."""
    total: int = Field(..., description="Total number of commands")
    successful: int = Field(..., description="Successful executions")
    failed: int = Field(..., description="Failed executions")
    results: list[dict[str, Any]] = Field(default_factory=list, description="Execution details")


class NasMapping(BaseModel):
    """NAS mount mapping."""
    nas_remote: str = Field(..., description="Remote NAS path")
    nas_local: str = Field(..., description="Local mount point")
    nas_param: str | None = Field(None, description="Mount parameters")
    nas_permission: str | None = Field(None, description="Access permission")


class SynlinkMapping(BaseModel):
    """Symbolic link mapping."""
    source: str = Field(..., description="Source path")
    target: str = Field(..., description="Target path")


class DeviceConnectionResponse(BaseModel):
    """Device connection information response."""
    type: str = Field(..., description="Connection type (local/remote)")
    target: str = Field(..., description="Connection target (host:port)")
    token: str = Field(..., description="Connection token")
    engine_type: str = Field(..., description="Engine type")
    available: bool = Field(True, description="Whether the device is reachable")
    message: str = Field("", description="Human-readable status message when unavailable")
    url: str = Field("", description="Connection URL (HTTP or WebSocket, depending on connection mode)")


class DeviceProps(BaseModel):
    """Device properties - compatible with daas and arca device types.

    Common fields:
        client_id: Client ID
        nas_mappings: NAS mount mappings
        callback_token: Callback token
        bolt_id: Bolt ID

    Daas-specific fields:
        exe_unit_uuid, daas_session_id, protocol, version, etc.

    Arca-specific fields:
        sandbox_id, template_id, device_from, symbol
    """
    model_config = ConfigDict(extra="allow")

    # Common fields
    client_id: str = Field("", description="Client ID")
    nas_mappings: str = Field("", description="NAS mappings (JSON string)")
    callback_token: str = Field("", description="Callback token")
    bolt_id: str = Field("", description="Bolt ID")

    # Daas-specific
    exe_unit_uuid: str = Field("", description="Execution unit UUID")
    daas_session_id: str = Field("", description="DaaS session ID")
    protocol: str = Field("", description="Protocol type")
    version: str = Field("", description="Version")
    device_region: str = Field("", description="Device region")
    device_locator: str = Field("", description="Device locator")
    device_locator_str: str = Field("", description="Device locator (Base64)")
    device_number: str = Field("", description="Device number")

    # Arca-specific
    sandbox_id: str = Field("", description="Arca sandbox ID")
    template_id: str = Field("", description="Arca template ID")
    device_from: str = Field("", description="Device source")
    symbol: list[dict[str, Any]] | str = Field("", description="Symlink mappings")

    @field_validator("nas_mappings", mode="before")
    @classmethod
    def _coerce_nas_mappings(cls, v: Any) -> str:
        if isinstance(v, (list, dict)):
            return json.dumps(v, ensure_ascii=False)
        return v


# =============================================================================
# Main Response Models
# =============================================================================

class DeviceBindingResponse(BaseModel):
    """Device binding response."""
    id: int = Field(..., description="Binding ID")
    entity_id: str = Field(..., description="Entity ID (user/team)")
    entity_type: EntityType = Field(..., description="Entity type")
    device_id: str = Field(..., description="Device ID")
    device_provider: str = Field(..., description="Device provider (local/arca/daas)")
    env: Env = Field(..., description="Environment")
    device_props: dict[str, Any] = Field(..., description="Device properties")
    status: DeviceBindingStatus = Field(..., description="Binding status")
    last_alive_at: datetime | None = Field(None, description="Last alive timestamp")


class DeviceBindingWithConnectionResponse(DeviceBindingResponse):
    """Device binding with optional connection info."""
    connection: DeviceConnectionResponse | None = Field(None, description="Connection details")


class BatchSetDeviceEnvResult(BaseModel):
    """Batch set environment result."""
    total: int = Field(..., description="Total requested")
    updated: int = Field(..., description="Successfully updated")
    updated_ids: list[int] = Field(default_factory=list, description="Updated binding IDs")


# =============================================================================
# Multi-instance — instance list (§1 frontend-api-contract)
# =============================================================================

class DeviceInstanceItem(BaseModel):
    """Single device instance entry (BaaS raw 6 fields + backend synthesized).

    Passes through raw BaaS ``status`` so the frontend can distinguish
    STOPPED/FAILED (both collapse to ``ABNORMAL`` in ``health_status``).
    """
    device_uuid: str = Field(..., description="Instance anchor; pass to conn-info/restart")
    status: str | None = Field(None, description="BaaS raw status: ACTIVE/PENDING/UPDATING/STOPPED/FAILED")
    health: str | None = Field(None, description="BaaS raw health probe: 'true'/'false'/null")
    provider_type: str | None = Field(None, description="Container provider type")
    provider_device_id: str | None = Field(None, description="Provider-side device id")
    gmt_create: str | None = Field(None, description="Instance creation time")
    health_status: str = Field(..., description="Health four-state: ACTIVE/RESTARTING/ABNORMAL/UNKNOWN")
    engine_type: str = Field(..., description="Engine type (e.g. openclaw)")
    bot_uuid: str = Field(..., description="Owning bot uuid")


class DeviceInstancesResponse(BaseModel):
    """Instance list + container info response (`data` of ApiResponse)."""
    bot_uuid: str = Field(..., description="Owning bot uuid")
    devices: list[DeviceInstanceItem] = Field(
        default_factory=list, description="Instance list"
    )
