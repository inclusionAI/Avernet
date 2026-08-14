"""Request/response models for the resources group (workspace files).

Docstrings and field descriptions here are published verbatim into the OpenAPI
document external tenants read — keep them caller-facing prose. Rationale and
internal names belong in ``#`` comments.

Every model here describes something read from the bot's workspace, so none of
them carries a record id: the engine is the source of truth, and `path` is the
address. Link resources are not part of this surface.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.enums import _DocumentedEnum


class ResourceType(_DocumentedEnum):
    """A resource is a file or a folder in the bot's workspace."""

    FILE = "file"
    FOLDER = "folder"

    __descriptions__ = {
        "file": "A file in the bot's workspace.",
        "folder": "A directory in the bot's workspace.",
    }


class FileEntry(BaseModel):
    """A file or folder in the bot's workspace.

    The container's own storage layout is never exposed — `path` is relative to
    the workspace root, not to any real filesystem the bot runs on.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "path": "docs/spec.md",
                "name": "spec.md",
                "type": "file",
                "size": 2048,
            }
        }
    )

    # Required, unlike every other field: it is the address every endpoint in
    # this group accepts, so an entry without one describes something the
    # caller cannot then act on.
    path: str = Field(
        description="Workspace-relative path, e.g. 'docs/spec/a.txt' — the "
        "whole path, not the directory. This is the addressing key: pass it "
        "as `path` on stat, download, preview, upload, mkdir and delete, "
        "exactly as listed."
    )
    name: str = Field(
        description="Display name — the last segment of `path`."
    )
    type: ResourceType = Field(description="What kind of entry this is.")
    size: int | None = Field(
        default=None,
        description="Size in bytes. Null for folders, and for files whose "
        "listing carries no size — not every engine reports one.",
    )
    # Deliberately absent: ``modified_at`` and ``readonly``.
    #
    # ``ResourceFileService.list_dir`` computes both, and both are dropped here
    # on purpose. ``modified_at`` is provider-dependent — the local device's
    # listing returns name/path/is_dir only — and a field populated on some
    # engines and null on others is the shape the engine-backed spec (G6)
    # rejected. ``readonly`` is always false in a listing: ``is_readonly``
    # matches dotfiles and workspace-root identity files, and ``list_dir``
    # filters both out before it is ever consulted.


class Preview(BaseModel):
    """A file's content, decoded as text."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "path": "docs/spec.md",
                "content_type": "application/octet-stream",
                "content": "# Spec\nThe gateway forwards…",
            }
        }
    )

    path: str = Field(
        description="Workspace-relative path of the previewed file, as given."
    )
    content_type: str = Field(
        description="MIME label of the returned content; currently always "
        "'application/octet-stream' — the server does not classify further."
    )
    content: str | None = Field(
        default=None,
        description="The file's content decoded as UTF-8, with undecodable "
        "bytes replaced. Files over the preview size limit (1 MB) are "
        "refused with 413 rather than truncated; an empty file previews as "
        "an empty string.",
    )
