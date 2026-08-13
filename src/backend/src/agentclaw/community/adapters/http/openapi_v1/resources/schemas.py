"""Request/response models for the resources group (unified files + links).

Docstrings and field descriptions here are published verbatim into the OpenAPI
document external tenants read — keep them caller-facing prose. Rationale and
internal names belong in ``#`` comments.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.enums import _DocumentedEnum


class ResourceType(_DocumentedEnum):
    """A resource is a file, an external link, or a folder."""

    FILE = "file"
    LINK = "link"
    FOLDER = "folder"

    __descriptions__ = {
        "file": "A file in the bot's workspace, addressed by its "
        "workspace-relative path.",
        "link": "An external URL saved for the bot, addressed by its "
        "resource id.",
        "folder": "A directory in the bot's workspace, addressed by its "
        "workspace-relative path.",
    }


class Resource(BaseModel):
    """A resource: a file or folder in the bot's workspace, or an external link.

    The container's own storage layout is never exposed — `path` is relative to
    the workspace root, not to any real filesystem the bot runs on.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "resource_id": "",
                "name": "spec.md",
                "type": "file",
                "source": None,
                "url": None,
                "size": 2048,
                "path": "docs/spec.md",
                "gmt_create": None,
                "gmt_modified": None,
            }
        }
    )

    resource_id: str = Field(
        description="Identifier of the resource's stored record — decimal "
        "digits for a link, e.g. '42'. Empty for files and folders, which "
        "have no record: address those by `path` instead."
    )
    name: str = Field(
        description="Display name. For a file or folder this is the last "
        "segment of `path`; a link has only a name."
    )
    type: ResourceType = Field(description="What kind of resource this is.")
    # Pass-through of the record's provenance column; only links surface a
    # record on this API, so files/folders read null here.
    source: str | None = Field(
        default=None,
        description="How the resource record came to exist — e.g. 'manual' "
        "for a link created through this API, 'upload' for an uploaded "
        "file's record. Null for files and folders read straight from the "
        "workspace, which have no record.",
    )
    url: str | None = Field(
        default=None,
        description="The link's URL; null for files and folders.",
    )
    size: int | None = Field(
        default=None,
        description="Size in bytes; null when not reported (links, and "
        "entries whose listing carries no size).",
    )
    path: str | None = Field(
        default=None,
        description="Workspace-relative path of a file or folder, e.g. "
        "'docs/spec/a.txt' — the whole path, not the directory; `name` is "
        "its last segment. This is the addressing key: pass it as `path` on "
        "download, preview, upload and delete exactly as listed. Null for "
        "links.",
    )
    gmt_create: str | None = Field(
        default=None,
        description="When the record was created (ISO 8601, no timezone "
        "designator). Null for files and folders read straight from the "
        "workspace — their listing carries no timestamps.",
    )
    gmt_modified: str | None = Field(
        default=None,
        description="When the record last changed (ISO 8601, no timezone "
        "designator); null for files and folders read straight from the "
        "workspace.",
    )
    # Timestamp field names kept DB-flavoured on purpose: they are the
    # prevailing openapi_v1 convention (sessions, routines), and renaming only
    # this group would deepen the split with `skills`, which uses
    # created_at/updated_at.


class ResourceCreate(BaseModel):
    """Create a link resource.

    Only links are created here: files are created through the upload
    endpoint (a file request answers 400 pointing there), and folders through
    the mkdir endpoint (a folder request answers 501).
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Team wiki",
                "type": "link",
                "url": "https://wiki.example.com/team",
            }
        }
    )

    name: str = Field(
        description="Display name for the link; a duplicate of an existing "
        "link answers 409."
    )
    type: ResourceType = Field(
        description="What to create. Only 'link' is accepted here — see the "
        "model description for where files and folders are created."
    )
    url: str | None = Field(
        default=None,
        description="The URL the link points to. Required when type is "
        "'link' (400 when missing).",
    )
    parent_id: str | None = Field(
        default=None,
        description="Accepted for compatibility and currently ignored — "
        "links are not placed in folders.",
    )


class ResourceUpdate(BaseModel):
    """Partial update of a link resource. Omitted fields are left unchanged."""

    model_config = ConfigDict(
        json_schema_extra={"example": {"name": "Team wiki (new)"}}
    )

    name: str | None = Field(
        default=None, description="New display name; omit to keep."
    )
    url: str | None = Field(default=None, description="New URL; omit to keep.")


class Preview(BaseModel):
    """A file's preview content."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "resource_id": "",
                "content_type": "application/octet-stream",
                "preview_url": None,
                "content": "# Spec\nThe gateway forwards…",
            }
        }
    )

    resource_id: str = Field(
        description="Always empty — previews address files by their `path`."
    )
    content_type: str = Field(
        description="MIME label of the returned content; currently always "
        "'application/octet-stream' — the server does not classify further."
    )
    preview_url: str | None = Field(
        default=None,
        description="Reserved for a URL-based preview; currently always null "
        "— the content is returned inline instead.",
    )
    content: str | None = Field(
        default=None,
        description="The file's content decoded as UTF-8, with undecodable "
        "bytes replaced. Files over the preview size limit (1 MB) are "
        "refused with 413 rather than truncated; an empty file previews as "
        "an empty string.",
    )
