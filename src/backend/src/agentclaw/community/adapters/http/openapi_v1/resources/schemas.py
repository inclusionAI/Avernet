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
    "'file' for a file in the bot's workspace, 'link' for an external URL, "
    "'folder' for a directory in the workspace."
)


class Resource(BaseModel):
    """A file or folder in the bot's workspace, or an external link.

    Files and folders are addressed by `path`, links by `resource_id`. Where the
    workspace is stored is not exposed: `path` is relative to the workspace root.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "resource_id": "r-8231",
                "name": "a.txt",
                "type": "file",
                "source": None,
                "url": None,
                "size": 2048,
                "path": "docs/spec/a.txt",
                "gmt_create": "2026-07-30T09:00:00+00:00",
                "gmt_modified": "2026-07-30T09:12:04+00:00",
            }
        }
    )

    resource_id: str = Field(
        description="Identifier of this resource record. This is the address of "
        "a link — use it in the path of the per-link endpoints. Files and "
        "folders are addressed by `path` instead."
    )
    name: str = Field(
        description="Resource name. For a file or folder this is the last "
        "segment of `path`; for a link it is the name it was created with."
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
    # The whole path, not the directory: `name` is its last segment, kept
    # because a link has a name and no path.
    path: str | None = Field(
        default=None,
        description="Workspace-relative path of a file or folder, e.g. "
        "'docs/spec/a.txt'. This is what the `path` query parameter takes on "
        "download, preview and delete, so an entry from a listing can be passed "
        "straight back. Null for a link.",
    )
    # Null for a file the bot created itself: the device's directory listing
    # carries no timestamps, so these come from the resource record — which
    # exists for an uploaded file and for a link, but not for a file that was
    # never uploaded through this API. The names stay DB-flavoured because that
    # is the prevailing openapi_v1 convention (sessions, routines); renaming
    # only this group would deepen the split with skills, which uses
    # created_at / updated_at.
    gmt_create: str | None = Field(
        default=None,
        description="Creation time (ISO 8601). Null for a file the bot created "
        "itself rather than one uploaded through this API.",
    )
    gmt_modified: str | None = Field(
        default=None,
        description="Last-modified time (ISO 8601). Null for a file the bot "
        "created itself rather than one uploaded through this API.",
    )


class ResourceCreate(BaseModel):
    """Create-a-resource request body.

    This operation creates link resources. Files go to the upload endpoint and
    folders to the mkdir endpoint; both are refused here.
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
    """A file's content, read as text.

    Read `content`; the other three fields are constants this endpoint does not
    vary, kept on the schema for compatibility. They are described for what they
    actually are rather than what their names suggest.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "resource_id": "",
                "content_type": "application/octet-stream",
                "preview_url": None,
                "content": "# Q3 notes\n\nRevenue is up 4%.\n",
            }
        }
    )

    # All three constants below are what the handler passes, verbatim. Describing
    # them as varying — which they did before files moved to the workspace —
    # would publish a contract no response can satisfy.
    resource_id: str = Field(
        description="Always empty. A preview is addressed by `path`, not by a "
        "resource identifier."
    )
    content_type: str = Field(
        description="Always 'application/octet-stream'. This endpoint does not "
        "detect the file's real media type — do not branch on this value."
    )
    preview_url: str | None = Field(
        default=None,
        description="Always null. The content is returned inline, never as a "
        "URL to fetch it from.",
    )
    # Left nullable rather than tightened to required. It is always populated on
    # a 200, but moving a response property into `required` is what the
    # compatibility gate calls `property-now-required`, and this change is not
    # entitled to spend an --allow-breaking on a description fix. The
    # description states the guarantee the shape no longer carries.
    content: str | None = Field(
        default=None,
        description="The file's content, decoded as UTF-8. Always present on a "
        "200 — never null, despite being nullable in the schema. Bytes that are "
        "not valid UTF-8 are replaced rather than refused, so a mostly-text "
        "file still previews.",
    )
