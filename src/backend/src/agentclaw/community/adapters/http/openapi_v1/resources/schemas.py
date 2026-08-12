"""Request/response models for the resources group (unified files + links)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class ResourceType(StrEnum):
    """A resource is a file, an external link, or a folder."""

    FILE = "file"
    LINK = "link"
    FOLDER = "folder"


class Resource(BaseModel):
    """A resource: a file or folder in the bot's workspace, or an external link.

    The container's own storage layout is never exposed — ``path`` is relative to
    the workspace root, not the engine-view absolute path the device returns.
    """

    resource_id: str
    name: str
    type: ResourceType
    source: str | None = None  # e.g. "yuque" for a link resource
    url: str | None = None  # link URL for LINK resources
    size: int | None = None
    #: Workspace-relative path of a file or folder, e.g. ``docs/spec/a.txt``.
    #: This is the addressing key: it is what ``?path=`` takes on download,
    #: preview and delete, so a client can pass a listing entry straight back.
    #: It is the *whole* path, not the directory — ``name`` is its last segment,
    #: kept because a LINK has a name and no path. ``None`` for links.
    path: str | None = None
    gmt_create: str
    gmt_modified: str


class ResourceCreate(BaseModel):
    """Create a resource. ``url`` is required for a link, ``parent_id`` optional."""

    name: str
    type: ResourceType
    url: str | None = None
    parent_id: str | None = None


class ResourceUpdate(BaseModel):
    """Partial update of a resource."""

    name: str | None = None
    url: str | None = None


class Preview(BaseModel):
    """A resource preview."""

    resource_id: str
    content_type: str
    preview_url: str | None = None
    content: str | None = None
