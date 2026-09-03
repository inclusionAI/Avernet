"""Response shapes for applying a bot's configuration manifest (W4, #1472).

Named ``schemas_*`` rather than ``*_schemas``: that prefix is what the adapter
layer's "this file is not a router" exemption matches, and it is the
convention ``service_bot/schemas_publish.py`` already follows.

Split out of ``bots/schemas.py`` rather than added to it: the apply report is
four nested models with a worked example, and the module it came from is one
file away from the size cap. Their own file keeps the report's shape readable
next to the routes that return it.

The report is assembled field by field from ``ApplyReport.as_payload()`` — there
is no dict passthrough anywhere on this path, so nothing the engine holds can
reach a caller without being named here first.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ConfigManifestApplyAccepted(BaseModel):
    """The 202 body: a handle, and the state the apply starts in.

    Deliberately not a report. The work has not happened yet — that is the point
    of the shape — so returning anything report-shaped would invite a caller to
    read outcomes that do not exist.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"apply_id": "9c1f4ae2b83d4f6a9e1c7d5b2a8f0e34", "result": "RUNNING"}
        }
    )

    apply_id: str = Field(
        description="Poll this apply with `GET .../config-manifest/applies/{apply_id}`."
    )
    result: str = Field(description="Always `RUNNING` here.")


class ConfigManifestApplyEntry(BaseModel):
    """What happened to one declared entry."""

    category: str = Field(description="The category or section this entry is in.")
    name: str = Field(
        description="How the entry names itself — a skill `name`, an identity "
        "`type`, a resource `path`, an mcp `server_code`."
    )
    action: str = Field(
        description="`created` / `updated` / `unchanged` / `skipped` / `failed`. "
        "`skipped` means the entry was not written because its category was "
        "aborted — never that it was optional."
    )
    error: str | None = Field(
        default=None, description="Why, when `action` is `failed` or `skipped`."
    )
    note: str | None = Field(
        default=None,
        description="Something true of a successful entry a caller would "
        "otherwise have to infer — today, when a `script` actually executes.",
    )


class ConfigManifestApplyCategory(BaseModel):
    """One category's summary, including what overwriting it removed."""

    category: str = Field(description="The category or section.")
    aborted: bool = Field(
        description="True when the category did not converge, because at least "
        "one declared entry could not be materialized. Read it together with "
        "`partially_written`: on its own it does not promise the area is "
        "untouched."
    )
    partially_written: bool = Field(
        default=False,
        description="True when the failure happened partway through writing, so "
        "part of the category's area may already have changed. `aborted` with "
        "this false means nothing was written and there is nothing to undo; "
        "with this true, re-apply to converge the area.",
    )
    removed: list[str] = Field(
        default_factory=list,
        description="Entries that existed in this category's area and are no "
        "longer declared, so overwriting removed them. Reported separately from "
        "`entries` because a removal has no declared entry.",
    )


class ConfigManifestApply(BaseModel):
    """One apply's report — what was delivered, and what was not."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "apply_id": "9c1f4ae2b83d4f6a9e1c7d5b2a8f0e34",
                "bot_id": "20260813_a7k2m9p1",
                "trigger": "explicit",
                "result": "SUCCEEDED",
                "started_at": "2026-08-31T09:12:04+00:00",
                "finished_at": "2026-08-31T09:12:06+00:00",
                "sources": [],
                "categories": [
                    {"category": "mcp", "aborted": False, "removed": ["mcp.old"]}
                ],
                "entries": [
                    {
                        "category": "mcp",
                        "name": "mcp.ant.homistudio.meetmcp",
                        "action": "created",
                        "error": None,
                        "note": None,
                    }
                ],
                "notes": [],
            }
        }
    )

    apply_id: str = Field(description="Empty when the bot has never been applied.")
    bot_id: str = Field(description="The bot this apply targeted.")
    trigger: str = Field(description="What started it; `explicit` for this API.")
    result: str = Field(
        description="`RUNNING` while the work is in flight, then `SUCCEEDED` / "
        "`PARTIAL` / `FAILED`, derived from the entries below."
    )
    started_at: datetime | None = Field(
        default=None, description="When the apply began. Null when the bot has never been applied."
    )
    finished_at: datetime | None = Field(
        default=None, description="Null exactly while `result` is `RUNNING`."
    )
    sources: list[dict] = Field(
        default_factory=list,
        description="Provenance for the manifest's named remote sources: what "
        "each `source` name actually resolved to, including the exact "
        "`resolved_sha`, since a moving `ref` like `main` means something "
        "different next week. Always empty in this release — nothing is fetched "
        "yet — and filled once remote sources are supported. A credential "
        "appears by name only, never by value.",
    )
    categories: list[ConfigManifestApplyCategory] = Field(
        default_factory=list,
        description="One summary per category the document declared. A category "
        "the document did not mention does not appear, because it was not touched.",
    )
    entries: list[ConfigManifestApplyEntry] = Field(
        default_factory=list,
        description="One row per declared entry, across every category. Removals "
        "are not here — they have no declared entry, so they are reported under "
        "the category's `removed`.",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Apply-level notes that belong to no entry. Today only the "
        "delivery strategy's closing step writes one: on teclaw, a whole-artifact "
        "redeliver that failed after every category was written is recorded here "
        "rather than failing the apply. Empty on ARCA.",
    )
