"""Request/response models for the resources group (unified files + links)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ResourceType(StrEnum):
    """A resource is a file, an external link, or a folder."""

    FILE = "file"
    LINK = "link"
    FOLDER = "folder"


_TYPE_DESC = (
    "'file' for uploaded bytes, 'link' for an external URL, 'folder' for a "
    "container."
)


class Resource(BaseModel):
    """A resource record. Where the bytes are stored is not exposed."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "resource_id": "r-8231",
                "name": "Q3 notes",
                "type": "link",
                "source": "yuque",
                "url": "https://example.com/docs/q3-notes",
                "size": None,
                "gmt_create": "2026-07-30T09:00:00+00:00",
                "gmt_modified": "2026-07-30T09:12:04+00:00",
            }
        }
    )

    resource_id: str = Field(
        description="Identifier of this resource. Use it in the path of the "
        "per-resource endpoints."
    )
    name: str = Field(
        description="Resource name. Names are unique among resources of the "
        "same kind within the bot, not across the bot as a whole."
    )
    type: ResourceType = Field(description=_TYPE_DESC)
    source: str | None = Field(
        default=None, description="Where a link resource came from; null for "
        "anything else."
    )
    url: str | None = Field(
        default=None, description="Target URL of a link resource; null for "
        "anything else."
    )
    size: int | None = Field(
        default=None, description="Size in bytes of a file resource; null for "
        "anything else."
    )
    gmt_create: str = Field(description="Creation time (ISO 8601); may be empty.")
    gmt_modified: str = Field(
        description="Last-modified time (ISO 8601); may be empty."
    )


class ResourceCreate(BaseModel):
    """Create-a-resource request body.

    This operation creates link resources. Upload a file through the upload
    endpoint instead; folders are not creatable yet, and both are refused here.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Q3 notes",
                "type": "link",
                "url": "https://example.com/docs/q3-notes",
            }
        }
    )

    name: str = Field(
        description="Name for the new resource. A name already in use within "
        "the bot is refused with 409."
    )
    type: ResourceType = Field(description=_TYPE_DESC)
    url: str | None = Field(
        default=None, description="Target URL. Required for a link resource; "
        "omitting it is refused (400)."
    )
    parent_id: str | None = Field(
        default=None, description="Reserved for placing the resource under a "
        "folder. Not applied yet — resources are scoped by bot."
    )


class ResourceUpdate(BaseModel):
    """Partial update of a link resource. Omit a field to leave it unchanged."""

    model_config = ConfigDict(
        json_schema_extra={"example": {"name": "Q3 notes (final)"}}
    )

    name: str | None = Field(default=None, description="New name; omit to keep.")
    url: str | None = Field(default=None, description="New target URL; omit to keep.")


class Preview(BaseModel):
    """A readable rendering of a resource's content."""

    resource_id: str = Field(description="Resource this preview is of.")
    content_type: str = Field(
        description="Media type of the previewed content, e.g. 'text/markdown'."
    )
    preview_url: str | None = Field(
        default=None, description="Where the preview can be fetched instead of "
        "being read inline; null when the content is inline."
    )
    content: str | None = Field(
        default=None, description="The previewed content as text; null when only "
        "a preview URL is available."
    )
