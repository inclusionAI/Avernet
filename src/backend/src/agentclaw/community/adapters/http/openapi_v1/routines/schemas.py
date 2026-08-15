"""Request/response models for the routines group (was cron).

Docstrings and field descriptions here are published verbatim into the OpenAPI
document external tenants read — keep them caller-facing prose. Rationale and
internal names belong in ``#`` comments.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# `command` is a natural-language instruction handed to the bot as the user
# message of a fresh session at fire time — the name is legacy, it was never a
# shell command. Stated once here and reused so create/read cannot drift.
_COMMAND_DESC = (
    "The instruction the bot receives when the routine fires — free-form "
    "natural language, delivered as the user message of a fresh session per "
    "run. Not a shell command."
)

_TIMEZONE_DESC = (
    "IANA time-zone name the schedule is evaluated in, e.g. 'Asia/Shanghai' "
    "(the default when omitted on create). When updating the trigger, send "
    "the timezone alongside it — a trigger update without one resets the "
    "schedule to its default zone."
)


class ScheduleTrigger(BaseModel):
    """A schedule trigger. Modeled as a typed nested object so other trigger
    kinds (event, webhook) can be added later without a breaking change."""

    model_config = ConfigDict(
        json_schema_extra={"example": {"type": "schedule", "cron": "0 9 * * 1-5"}}
    )

    type: Literal["schedule"] = Field(
        default="schedule",
        description="Trigger kind; 'schedule' (a cron schedule) is the only "
        "kind today.",
    )
    cron: str = Field(
        description="Standard 5-field cron expression — minute, hour, "
        "day-of-month, month, day-of-week — e.g. '0 9 * * 1-5' for 09:00 on "
        "weekdays. Minute granularity; seconds are not supported. Evaluated "
        "in the routine's timezone."
    )


class Routine(BaseModel):
    """A scheduled task run by a bot."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "routine_id": "5f0f2c9a-b1c3-4f6e-9a0d-2e7c8b1d4a55",
                "name": "morning-brief",
                "trigger": {"type": "schedule", "cron": "0 9 * * 1-5"},
                "command": "Summarize yesterday's tickets and post the brief.",
                "enabled": True,
                "timezone": "Asia/Shanghai",
                "gmt_create": "2026-08-01T02:00:00+00:00",
                "gmt_modified": "2026-08-10T02:00:00+00:00",
            }
        }
    )

    routine_id: str = Field(
        description="Identifier of the routine — an opaque string assigned by "
        "the bot's engine; treat it as a token."
    )
    bot_id: str = Field(
        description="The bot the routine belongs to; may be empty on the "
        "detail read — keep the bot_id you queried with."
    )
    name: str = Field(description="Human-readable routine name.")
    trigger: ScheduleTrigger = Field(description="When the routine fires.")
    command: str = Field(description=_COMMAND_DESC)
    enabled: bool = Field(
        description="Whether the schedule is armed; a disabled routine keeps "
        "its definition but never fires."
    )
    timezone: str | None = Field(default=None, description=_TIMEZONE_DESC)
    gmt_create: str = Field(
        description="When the routine was created (ISO 8601 UTC); empty when "
        "the engine reports none."
    )
    gmt_modified: str = Field(
        description="When the routine last changed (ISO 8601 UTC); empty "
        "when the engine reports none."
    )


class RoutineCreate(BaseModel):
    """Create-a-routine request body."""

    model_config = ConfigDict(
        # No ``bot_id``: it moved to the path, and this model no longer declares
        # it. Pydantic ignores unknown fields rather than rejecting them, so an
        # example that still showed one would be copied into real requests and
        # silently dropped — the caller believing they had named a bot while the
        # path decided. ``LegacyRoutineCreate`` keeps it, because there it is
        # read.
        json_schema_extra={
            "example": {
                "name": "morning-brief",
                "trigger": {"type": "schedule", "cron": "0 9 * * 1-5"},
                "command": "Summarize yesterday's tickets and post the brief.",
                "timezone": "Asia/Shanghai",
                "enabled": True,
            }
        }
    )

    name: str = Field(description="Human-readable routine name.")
    trigger: ScheduleTrigger = Field(description="When the routine fires.")
    command: str = Field(description=_COMMAND_DESC)
    timezone: str | None = Field(default=None, description=_TIMEZONE_DESC)
    enabled: bool = Field(
        default=True,
        description="Whether the schedule starts armed; defaults to true.",
    )


class RoutineUpdate(BaseModel):
    """Partial update of a routine. Omitted fields are left unchanged."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "trigger": {"type": "schedule", "cron": "30 8 * * 1-5"},
                "timezone": "Asia/Shanghai",
            }
        }
    )

    name: str | None = Field(default=None, description="New name; omit to keep.")
    trigger: ScheduleTrigger | None = Field(
        default=None,
        description="New trigger; omit to keep. Send timezone alongside it — "
        "see that field's description.",
    )
    command: str | None = Field(
        default=None, description="New instruction; omit to keep."
    )
    timezone: str | None = Field(default=None, description=_TIMEZONE_DESC)
    enabled: bool | None = Field(
        default=None,
        description="Arm (true) or disarm (false) the schedule; omit to keep.",
    )


class RoutineRun(BaseModel):
    """One execution of a routine."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "run_id": "5f0f2c9a-b1c3-4f6e-9a0d-2e7c8b1d4a55",
                "routine_id": "5f0f2c9a-b1c3-4f6e-9a0d-2e7c8b1d4a55",
                "status": "ok",
                "started_at": "2026-08-13T01:00:00+00:00",
                "finished_at": "2026-08-13T01:04:12+00:00",
            }
        }
    )

    run_id: str = Field(
        description="Identifier of the run entry. Not guaranteed unique "
        "across a routine's history — treat the list positionally."
    )
    routine_id: str = Field(description="The routine that ran.")
    # Two producers, two vocabularies — the run-now endpoint synthesizes its
    # own three values, the history read passes the engine's through — so this
    # is documented as open rather than published as an enum either endpoint
    # would violate.
    status: str = Field(
        description="Outcome of the run. In the run history this is the "
        "engine's own label — today 'ok' (the run completed), 'error' (it "
        "failed or timed out) or 'skipped' (fired but not runnable), and "
        "empty when the engine reported none. The run-now endpoint instead "
        "answers 'completed', 'failed' or 'unknown' for the trigger attempt "
        "it just made. Treat unknown values as informational."
    )
    started_at: str | None = Field(
        default=None,
        description="When the run started (ISO 8601 UTC); null when not "
        "reported — including on the run-now response, which reports no "
        "timestamps.",
    )
    finished_at: str | None = Field(
        default=None,
        description="When the run finished (ISO 8601 UTC); null while the "
        "run is still in flight or when not reported.",
    )
