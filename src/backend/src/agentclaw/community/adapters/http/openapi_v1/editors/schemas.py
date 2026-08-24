"""Public contracts for Bot editor management."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.enums import _DocumentedEnum


_STRICT = ConfigDict(extra="forbid")


class EditorRole(_DocumentedEnum):
    """Permission granted to a Bot editor."""

    ADMIN = "admin"
    MEMBER = "member"

    __descriptions__ = {
        "admin": "May manage editors and perform administrative Bot operations.",
        "member": "May edit and operate the Bot without managing editors.",
    }


class EditorCreate(BaseModel):
    """Editor identity and role to add to the addressed Bot."""

    model_config = _STRICT

    editor_user_id: str = Field(
        min_length=1,
        max_length=256,
        description="User identifier to add as an editor.",
    )
    user_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Optional display name stored with the editor relation.",
    )
    role: EditorRole = Field(
        default=EditorRole.MEMBER,
        description="Permission granted to the editor.",
    )


class EditorUpdate(BaseModel):
    """Mutable fields of an existing editor relation."""

    model_config = _STRICT

    role: EditorRole = Field(description="Replacement editor permission.")


class Editor(BaseModel):
    """One editor relation on a Bot."""

    id: int = Field(description="Stable editor-relation identifier.")
    user_id: str = Field(description="Editor user identifier.")
    user_name: str | None = Field(
        default=None, description="Stored editor display name, when available."
    )
    role: EditorRole = Field(description="Current editor permission.")
    created_at: datetime = Field(description="When the editor was added.")
    updated_at: datetime = Field(description="When the relation last changed.")


class EditorList(BaseModel):
    """All editor relations on the addressed Bot."""

    total: int = Field(description="Number of editor relations returned.")
    items: list[Editor] = Field(description="Editors ordered by creation time.")
