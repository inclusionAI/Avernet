"""Public contracts for Bot render-screen CDN mappings."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


_STRICT = ConfigDict(extra="forbid")


class RenderScreenCreate(BaseModel):
    """One component-library mapping to add to the addressed Bot."""

    model_config = _STRICT

    name: str = Field(
        min_length=1,
        max_length=256,
        description="Component-library name, unique within the Bot.",
    )
    cdn_url: HttpUrl = Field(
        max_length=1024,
        description="HTTP(S) URL of the component library's UMD bundle.",
    )


class RenderScreenUpdate(BaseModel):
    """Replacement fields for one render-screen mapping."""

    model_config = _STRICT

    name: str = Field(
        min_length=1,
        max_length=256,
        description="Replacement component-library name.",
    )
    cdn_url: HttpUrl = Field(
        max_length=1024,
        description="Replacement HTTP(S) URL of the UMD bundle.",
    )


class RenderScreen(BaseModel):
    """One render-screen component-library mapping."""

    id: int = Field(description="Stable mapping identifier used by mutations.")
    name: str = Field(description="Component-library name.")
    cdn_url: str = Field(description="HTTP(S) URL of the UMD bundle.")
    creator_id: str = Field(description="User who created the mapping.")
    created_at: datetime | None = Field(
        default=None, description="When the mapping was created."
    )
    updated_at: datetime | None = Field(
        default=None, description="When the mapping was last modified."
    )


class RenderScreenList(BaseModel):
    """All live render-screen mappings on the addressed Bot."""

    total: int = Field(description="Number of mappings returned.")
    items: list[RenderScreen] = Field(
        description="Mappings ordered by creation time, newest first."
    )
