"""Request and response shapes for a bot's CLI tools (W9, #1477).

Named ``schemas_*`` for the reason ``schemas_config_manifest_apply.py`` records:
that prefix is what the adapter layer's "this file is not a router" exemption
matches.

**The install is an upload.** A caller hands the platform the executable — or
the archive it is inside — in the request itself, the way a skill package
arrives. There is no URL here and no credential: the platform fetches nothing
on this road, so there is nothing to fetch under. A tool that should come from
a *declared* source (an object store, a git repository) is declared in the
bot's manifest instead, which is the only place a source belongs.

**No response field is a container path**, and there is nothing here to add one
to. The engine chooses where a tool lands and the platform never learns it, so
a caller is told the command's name, what it was built from, and what the
platform verified — never where it is.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field


class CliToolUpload(BaseModel):
    """The multipart install: one file part, and what to make of it.

    Sent as ``multipart/form-data`` — ``file`` is the binary itself or a
    ``zip`` / ``tar.gz`` archive, and the rest are ordinary form fields.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "description": "The tool's bytes, and what the platform should "
            "make of them.",
            "example": {
                # The file part is binary, so the example can only name it.
                # It is here rather than omitted because it is required, and
                # an example that leaves out a required field is the first
                # thing an integrator copies into a request the schema rejects.
                "file": "<the tool's bytes: mycli-1.4.2-linux-amd64.tar.gz>",
                "name": "mycli",
                "digest": "sha256:3e7a1f9c2b8d4e6a0f5c7b9d1e3a5c7b9d1e3a5c7b9d1e3a5c7b9d1e3a5c7b9d",
                "unpack": "tar.gz",
                "subpath": "mycli-1.4.2/bin/mycli",
                "version": "1.4.2",
            },
        }
    )

    file: UploadFile = Field(
        description="The tool's bytes: the executable itself, or the `zip` / "
        "`tar.gz` archive it is packed in. Up to 200 MiB — a larger body is "
        "refused while it is still being read, not after."
    )
    name: str = Field(
        description="The command the agent will run. A bare identifier — it "
        "carries no position, because where the tool lands is the engine's "
        "decision, not the caller's.",
        max_length=128,
    )
    digest: str = Field(
        description="`sha256:<64 hex>` over the uploaded bytes, and "
        "**mandatory**. The platform is distributing an executable on your "
        "behalf, so the supply chain is pinned or the request is refused: the "
        "digest is your statement of what you meant to send, and a truncated "
        "or swapped upload fails it instead of being installed. It covers the "
        "uploaded object — the binary itself, or the whole archive — with "
        "`subpath` selecting the file inside."
    )
    unpack: str | None = Field(
        default=None,
        description="`zip` or `tar.gz` when the uploaded file is an archive. "
        "Omit it and the uploaded file is the executable itself.",
    )
    subpath: str | None = Field(
        default=None,
        description="Which file inside the archive is the command. Required "
        "with `unpack`, and refused without it — one entry is one command is "
        "one file.",
    )
    version: str | None = Field(
        default=None,
        description="Metadata only. It never decides whether a reinstall "
        "happens: two installs of the same bytes under different version "
        "strings are the same tool.",
    )


class CliTool(BaseModel):
    """One installed tool, as the platform records it."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "mycli",
                "version": "1.4.2",
                "digest": "sha256:3e7a1f9c2b8d4e6a0f5c7b9d1e3a5c7b9d1e3a5c7b9d1e3a5c7b9d1e3a5c7b9d",
                "subpath": "mycli-1.4.2/bin/mycli",
                "md5": "9f2c4a1b6d8e0f3a5c7b9d1e3a5c7b9d",
                "size_bytes": 8123456,
                "installed_by": "u1",
                "gmt_modified": "2026-09-03T12:00:00Z",
            }
        }
    )

    name: str = Field(description="The command.")
    version: str | None = Field(default=None, description="As declared. Metadata only.")
    digest: str = Field(
        description="The pinned digest of what the platform was given — the "
        "uploaded object on this road, the fetched one when a manifest apply "
        "installed the tool."
    )
    subpath: str | None = Field(
        default=None, description="The archive member that became the command."
    )
    md5: str = Field(
        description="Of the delivered file, computed by the platform after "
        "unpacking and selection — so it is the executable's, not the archive's."
    )
    size_bytes: int = Field(description="Of the delivered file.")
    installed_by: str = Field(
        description="`manifest` when a manifest apply put it there, otherwise "
        "the user id that did. A manifest apply is a full override and will "
        "remove a tool installed here that it does not declare."
    )
    gmt_modified: datetime = Field(
        description="When the record last changed."
    )


class CliToolList(BaseModel):
    """Every tool the platform records for a bot, in name order."""

    tools: list[CliTool] = Field(
        default_factory=list,
        description="The bot's installed CLI tools, ordered by name. Empty for "
        "a bot that has none.",
    )


__all__ = ["CliTool", "CliToolList", "CliToolUpload"]
