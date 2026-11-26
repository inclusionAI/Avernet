"""Contract models for the user-level Agent Coding work-items aggregate."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SessionSource = Literal["mine", "others", "collab"]
WorkItemBotStatus = Literal["ok", "unavailable", "error"]

SOURCE_TOKENS = frozenset({"mine", "others", "collab"})
RUN_STATUS_TOKENS = frozenset(
    {
        "non_idle",
        "running",
        "awaitingApproval",
        "awaitingLlmEvaluation",
        "awaitingHuman",
        "completed",
        "failed",
        "aborted",
        "quarantined",
    }
)


class WorkItemsFilterError(ValueError):
    """A caller supplied a token that is not part of the work-items contract."""


def parse_optional_token_filter(
    raw: str | None, allowed_tokens: frozenset[str], field: str
) -> list[str] | None:
    """Parse one optional comma-separated token filter.

    Omitting or sending a blank value means "no filter". Duplicates are removed
    while preserving request order. This normalization belongs to the shared
    contract so the public and internal HTTP adapters cannot drift while they
    still own their transport-specific 400/422 mappings.
    """
    if raw is None or not raw.strip():
        return None
    tokens: list[str] = []
    for token in raw.split(","):
        value = token.strip()
        if not value:
            raise WorkItemsFilterError(f"invalid {field} token")
        if value not in allowed_tokens:
            raise WorkItemsFilterError(f"invalid {field} token '{value}'")
        if value not in tokens:
            tokens.append(value)
    return tokens or None


class WorkItem(BaseModel):
    """One non-idle session surfaced by the user-level work-items card."""

    bot_id: str = Field(description="Target Bot identifier.")
    bot_name: str = Field(description="Target Bot display name.")
    owner_id: str = Field(
        description=(
            "Bot owner identifier. For a collaborator Bot this is the Bot "
            "owner, not the session creator."
        )
    )
    session_key: str = Field(description="Relay session identifier or full key.")
    session_user_id: str | None = Field(
        default=None,
        description="Session creator parsed from the session key, when available.",
    )
    title: str | None = Field(default=None, description="Session display title.")
    source: SessionSource = Field(description="Session source classification.")
    run_status: str = Field(
        description=(
            "Latest DevFlow run status. This is an open set: the known "
            "non-idle statuses may be extended by Relay, and unknown values "
            "must be passed through to the caller."
        )
    )
    updated_at: str = Field(
        description=("Latest session update time returned by Relay, normally RFC3339.")
    )
    updated_at_ms: int = Field(
        description="Latest session update time in epoch milliseconds."
    )
    cwd: str | None = Field(
        default=None, description="Workspace root used for DDF detail lookups."
    )
    model: str | None = Field(default=None, description="Session model name.")
    ext_info: dict[str, Any] | None = Field(
        default=None,
        description="Relay ext_info projection, or null when Relay did not return it.",
    )


class WorkItemBotSummary(BaseModel):
    """Fan-out status for one target Bot."""

    bot_id: str = Field(description="Target Bot identifier.")
    bot_name: str = Field(description="Target Bot display name.")
    owner_id: str = Field(description="Bot owner identifier.")
    count: int = Field(
        description="Number of work items returned for this Bot after filtering."
    )
    status: WorkItemBotStatus = Field(
        description=(
            "ok means Relay returned a usable response; unavailable means this "
            "Bot has no active binding; error means Relay probing failed."
        )
    )
    error_code: str | None = Field(
        default=None, description="Machine-readable Bot probe failure code."
    )
    error_message: str | None = Field(
        default=None, description="Safe human-readable Bot probe failure detail."
    )


class WorkItemsFilters(BaseModel):
    """Filters normalized by the service; omitted filters remain explicitly null."""

    source: list[SessionSource] | None = Field(
        default=None, description="Selected session-source tokens."
    )
    since_ms: int | None = Field(
        default=None,
        description="Inclusive lower bound on session updated_at_ms.",
    )
    run_status: list[str] | None = Field(
        default=None,
        description=(
            "Selected DevFlow run-status tokens; non_idle means non-empty and not idle."
        ),
    )


class WorkItemsData(BaseModel):
    """One work-items snapshot across all Bots reachable by the current user."""

    items: list[WorkItem] = Field(description="Merged and sorted non-idle work items.")
    bots: list[WorkItemBotSummary] = Field(
        description="Per-Bot fan-out result and error summaries."
    )
    partial: bool = Field(
        description=(
            "True when at least one Bot probe failed; an unavailable Bot does "
            "not set this value."
        )
    )
    scan_limit: int = Field(
        description=(
            "Per-Bot Relay scan window. It does not limit the aggregate item count."
        )
    )
    filters: WorkItemsFilters = Field(
        description="Normalized request filters, with omitted filters as null."
    )


__all__ = [
    "RUN_STATUS_TOKENS",
    "SOURCE_TOKENS",
    "WorkItemsFilterError",
    "parse_optional_token_filter",
    "SessionSource",
    "WorkItem",
    "WorkItemBotStatus",
    "WorkItemBotSummary",
    "WorkItemsData",
    "WorkItemsFilters",
]
