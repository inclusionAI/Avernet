"""Request/response models for public local Bot routes."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

_STRICT = ConfigDict(extra="forbid")


class LocalBot(BaseModel):
    """Public local Bot summary."""

    bot_id: str
    bot_name: str
    bot_desc: str
    engine: str
    status: str
    owner_entity_id: str
    machine_id: str | None = None
    mount_path: str | None = None
    avatar_url: str | None = None


class LocalBotCreate(BaseModel):
    """Start creating a local Bot through the two-step Passport flow."""

    model_config = _STRICT

    bot_name: str
    machine_id: str
    bot_desc: str | None = None
    mount_path: str | None = None
    avatar_url: str | None = None
    engine: str = Field(default="openclaw")


class LocalBotAuthPending(BaseModel):
    """Returned when local Bot creation is waiting for user authorization."""

    bot_id: str
    iframe_url: str = ""
    redirect_url: str = ""


class LocalBotAuthStatus(BaseModel):
    """Passport authorization status for local Bot creation."""

    status: str
    message: str | None = None
    bot: LocalBot | None = None


class LocalDevice(BaseModel):
    """Local desktop device visible to the owner."""

    machine_id: str
    machine_name: str = ""
    hostname: str = ""
    status: str
    ip_address: str = ""
    last_alive_at: str | None = None
    created_at: str | None = None


class LocalDirectoryEntry(BaseModel):
    """A directory tree node from the local desktop daemon."""

    name: str
    children: list["LocalDirectoryEntry"] | None = None


class LocalOpenFolder(BaseModel):
    """Open a local Bot folder on its host device."""

    model_config = _STRICT

    folder_path: str | None = Field(default=None, max_length=512)

    @field_validator("folder_path")
    @classmethod
    def validate_no_traversal(cls, value: str | None) -> str | None:
        if value is not None and ".." in value:
            raise ValueError("folder_path must not contain directory traversal (..)")
        return value


class LocalOpenFolderResult(BaseModel):
    """Result of an open-folder command."""

    bot_id: str


class LocalLifecycleResult(BaseModel):
    """Result of a local Bot lifecycle operation."""

    bot_id: str
    status: str | None = None
    result: dict | None = None
