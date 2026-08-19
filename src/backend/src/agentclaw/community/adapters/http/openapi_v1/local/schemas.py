"""Request and response models for public local bot routes."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

_STRICT = ConfigDict(extra="forbid")


class LocalBot(BaseModel):
    """A local bot running on one of the user's desktop devices."""

    bot_id: str = Field(description="Unique identifier of the local bot.")
    bot_name: str = Field(description="Display name of the local bot.")
    bot_desc: str = Field(description="Description of what the local bot is for; may be empty.")
    engine: str = Field(description="Engine used by the local bot.")
    status: str = Field(description="Current lifecycle status reported for the local bot.")
    owner_entity_id: str = Field(description="User who owns the local bot.")
    machine_id: str | None = Field(default=None, description="Host device identifier, when assigned.")
    mount_path: str | None = Field(default=None, description="Workspace path mounted for the bot, when configured.")
    avatar_url: str | None = Field(default=None, description="Avatar URL for the bot, when configured.")


class LocalBotCreate(BaseModel):
    """Start creating a local bot through the user-authorization flow."""

    model_config = _STRICT

    bot_name: str = Field(description="Display name for the new local bot.")
    machine_id: str = Field(description="Device on which the local bot will run.")
    bot_desc: str | None = Field(default=None, description="Optional description of the bot's purpose.")
    mount_path: str | None = Field(default=None, description="Optional workspace path to mount for the bot.")
    avatar_url: str | None = Field(default=None, description="Optional avatar URL for the bot.")
    engine: str = Field(default="openclaw", description="Engine to run on the local device.")


class LocalBotAuthPending(BaseModel):
    """Authorization details returned while local bot creation is pending."""

    bot_id: str = Field(description="Identifier reserved for the pending bot.")
    iframe_url: str = Field(default="", description="URL to embed for completing authorization; empty when unavailable.")
    redirect_url: str = Field(default="", description="URL to open for completing authorization; empty when unavailable.")


class LocalBotAuthStatus(BaseModel):
    """Current authorization state for a pending local bot."""

    status: str = Field(description="Current authorization state.")
    message: str | None = Field(default=None, description="Additional status detail, when available.")
    bot: LocalBot | None = Field(default=None, description="Created bot after authorization completes; otherwise null.")


class LocalDevice(BaseModel):
    """A desktop device available for hosting local bots."""

    machine_id: str = Field(description="Unique identifier of the device.")
    machine_name: str = Field(default="", description="User-facing device name; may be empty.")
    hostname: str = Field(default="", description="Device hostname; may be empty.")
    status: str = Field(description="Current connectivity or readiness state.")
    ip_address: str = Field(default="", description="Reported device IP address; may be empty.")
    last_alive_at: str | None = Field(default=None, description="Most recent heartbeat time, when reported.")
    created_at: str | None = Field(default=None, description="Device registration time, when reported.")


class LocalDirectoryEntry(BaseModel):
    """A directory tree node returned by a local device."""

    name: str = Field(description="File or directory name.")
    children: list["LocalDirectoryEntry"] | None = Field(default=None, description="Child entries for a directory; null for a leaf.")


class LocalOpenFolder(BaseModel):
    """Request to open a local bot folder on its host device."""

    model_config = _STRICT

    folder_path: str | None = Field(default=None, max_length=512, description="Folder to open, relative to the bot workspace; omit for the workspace root.")

    @field_validator("folder_path")
    @classmethod
    def validate_no_traversal(cls, value: str | None) -> str | None:
        if value is not None and ".." in value:
            raise ValueError("folder_path must not contain directory traversal (..)")
        return value


class LocalOpenFolderResult(BaseModel):
    """Confirmation that an open-folder command was accepted."""

    bot_id: str = Field(description="Bot whose folder was opened.")


class LocalLifecycleResult(BaseModel):
    """Result of a local bot lifecycle operation."""

    bot_id: str = Field(description="Bot affected by the operation.")
    status: str | None = Field(default=None, description="Resulting lifecycle status, when reported.")
    result: dict | None = Field(default=None, description="Additional engine result fields, when reported.")
