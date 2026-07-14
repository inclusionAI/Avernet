"""Poolab instance list models.

Defines models for the instanceList API response items returned by
the Poolab REST API (GET /openapi/antclaw/instanceList).
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PoolabInstanceSummary(BaseModel):
    """Summary item from the Poolab instanceList API response.

    Matches the shape of items in the instanceList ``data.data`` array.
    Only ``poolab_instance_list_id`` and ``instance_id`` are required;
    all other fields are optional and default to None when absent.

    Attributes:
        poolab_instance_list_id: Poolab's internal tracking ID (string UUID like "inst_mock_001").
        instance_id: Integer machine instance ID.
        host_name: Machine hostname (e.g., "mock-host-name.example.com").
        status: Machine status (e.g., "OPENED").
        poolab_type: Machine type (e.g., "OpenClaw").
        network_type: Network configuration type (e.g., "PUBLIC_ONLY").
        created_at: ISO-8601 creation timestamp string.
        image: Image URL string.
        operations_url: Operations/O&M page URL with embedded token.
        remote_url: Remote desktop (VNC) access URL.
        model_config_data: Machine model configuration dict (e.g., {"type": "PUBLIC"}).
        user_id: Owner user ID string.
        user_nick: Owner user display/nickname.
        env: Environment label (e.g., "TEST").
        tenant_id: Tenant ID integer.
        passwd_config: Password configuration dict containing vncUser and vncPasswd.
    """

    poolab_instance_list_id: str = Field(
        ..., alias="id", description="Poolab internal tracking ID (string UUID)"
    )
    instance_id: int = Field(
        ..., alias="instanceId", description="Integer machine instance ID"
    )
    host_name: str | None = Field(
        default=None, alias="hostName", description="Machine hostname"
    )
    status: str | None = Field(
        default=None, description="Machine status (e.g., OPENED)"
    )
    poolab_type: str | None = Field(
        default=None, alias="type", description="Machine type (e.g., OpenClaw)"
    )
    network_type: str | None = Field(
        default=None,
        alias="networkType",
        description="Network type (e.g., PUBLIC_ONLY)",
    )
    created_at: str | None = Field(
        default=None, alias="createdAt", description="Creation timestamp (ISO-8601)"
    )
    image: str | None = Field(default=None, description="Image URL")
    operations_url: str | None = Field(
        default=None, alias="operationsUrl", description="Operations page URL"
    )
    remote_url: str | None = Field(
        default=None, alias="remoteUrl", description="Remote desktop URL"
    )
    model_config_data: dict[str, Any] | None = Field(
        default=None, alias="modelConfig", description="Model configuration dict"
    )
    user_id: str | None = Field(
        default=None, alias="userId", description="Owner user ID"
    )
    user_nick: str | None = Field(
        default=None, alias="userNick", description="Owner user nickname"
    )
    env: str | None = Field(default=None, description="Environment label (e.g., TEST)")
    tenant_id: int | None = Field(
        default=None, alias="tenantId", description="Tenant ID"
    )
    passwd_config: dict[str, Any] | None = Field(
        default=None, alias="passwdConfig", description="Password configuration dict"
    )

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("instance_id", mode="before")
    @classmethod
    def coerce_instance_id(cls, v: Any) -> int:
        """Coerce instance_id from string to int for Pydantic v2 compatibility."""
        if isinstance(v, str):
            return int(v)
        return v
