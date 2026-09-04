"""Request and response shapes for a bot's CLI tools (W9, #1477).

Named ``schemas_*`` for the reason ``schemas_config_manifest_apply.py`` records:
that prefix is what the adapter layer's "this file is not a router" exemption
matches.

**No response field is a container path**, and there is nothing here to add one
to. The engine chooses where a tool lands and the platform never learns it, so
a caller is told the command's name, what it was built from, and what the
platform verified — never where it is.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CliToolInstall(BaseModel):
    """Install one command-line tool on a bot."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "mycli",
                "source": "https://cdn.example.com/mycli-1.4.2-linux-amd64.tar.gz",
                "digest": "sha256:3e7a1f9c2b8d4e6a0f5c7b9d1e3a5c7b9d1e3a5c7b9d1e3a5c7b9d1e3a5c7b9d",
                "unpack": "tar.gz",
                "subpath": "mycli-1.4.2/bin/mycli",
                "version": "1.4.2",
            }
        }
    )

    name: str = Field(
        description="The command the agent will run. A bare identifier — it "
        "carries no position, because where the tool lands is the engine's "
        "decision, not the caller's.",
        max_length=128,
    )
    source: str = Field(description="Where to fetch the tool from.")
    digest: str = Field(
        description="`sha256:<64 hex>`, and **mandatory**. The platform is "
        "distributing an executable on your behalf, so the supply chain is "
        "pinned or the request is refused. It covers the fetched object — the "
        "binary itself, or the whole archive — with `subpath` selecting the "
        "file inside."
    )
    unpack: str | None = Field(
        default=None,
        description="`zip` or `tar.gz` when the source is an archive. Omit it "
        "and the fetched object is the executable itself.",
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
    auth: str | None = Field(
        default=None,
        description="The name of a stored source credential to fetch under. "
        "Never a secret value.",
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
    digest: str = Field(description="The pinned digest of what was fetched.")
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


__all__ = ["CliTool", "CliToolInstall", "CliToolList"]
