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

    # A file entry, which is what most reads of this model return — so the
    # example shows the empty `resource_id` and null timestamps a workspace
    # entry actually carries, rather than a tidier record that no response
    # matches.
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "resource_id": "",
                "name": "a.txt",
                "type": "file",
                "source": None,
                "url": None,
                "size": 2048,
                "path": "docs/spec/a.txt",
                "gmt_create": None,
                "gmt_modified": None,
            }
        }
    )

    resource_id: str = Field(
        description="Identifier of this resource record. This is the address of "
        "a link — use it in the path of the per-link endpoints. Empty for a "
        "file or folder, which is addressed by `path` instead."
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
    # Links only, and not because a file has no record — an uploaded file does.
    # Every file and folder in a response is built from the workspace listing,
    # which carries no timestamps, and neither the listing nor the upload
    # response joins the record back in. So an uploaded file reports null here
    # exactly like one the bot created itself. Only `_to_openapi_resource`,
    # which serves links, has a record to read them from.
    #
    # The names stay DB-flavoured because that is the prevailing openapi_v1
    # convention (sessions, routines); renaming only this group would deepen the
    # split with skills, which uses created_at / updated_at.
    gmt_create: str | None = Field(
        default=None,
        description="Creation time (ISO 8601). Populated for a link. Null for "
        "every file and folder — whether you uploaded it or the bot created it "
        "— because those are read from the workspace, which records no "
        "timestamps.",
    )
    gmt_modified: str | None = Field(
        default=None,
        description="Last-modified time (ISO 8601). Populated for a link. Null "
        "for every file and folder, for the same reason as `gmt_create`.",
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

    # Narrower than it looks: `create_url_resource` checks the name against URL
    # rows only — see _LINK_TYPE_SPLIT in router.py.
    name: str = Field(
        description="Name for the new resource. A name already used by another "
        "link created here is refused with 409, but the check does not span "
        "the whole bot: a file, a folder, or a link recorded by another path "
        "can hold the same name without blocking this create."
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

    # Neither field is usefully guarded: `update_link_resource` never compares
    # the name, and its URL guard is `check_link_url_exists`, hard-coded to LINK
    # while every link created here is a URL row — see _LINK_TYPE_SPLIT in
    # router.py.
    name: str | None = Field(
        default=None,
        description="New name; omit to keep. Not checked for uniqueness at "
        "all — a name another link already uses is accepted.",
    )
    url: str | None = Field(
        default=None,
        description="New target URL; omit to keep. Checked for uniqueness, but "
        "not against the links this API creates — so a URL another such link "
        "already uses is accepted. A clash with a record created elsewhere is "
        "still refused (409).",
    )


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
