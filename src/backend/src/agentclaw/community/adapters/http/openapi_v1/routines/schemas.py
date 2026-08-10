"""Request/response models for the routines group (was cron)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_CRON_DESC = (
    "Standard 5-field cron expression — minute, hour, day-of-month, month, "
    "day-of-week. For example '0 9 * * 1' runs at 09:00 every Monday."
)

_TIMEZONE_DESC = (
    "IANA time zone the schedule is interpreted in, e.g. 'Asia/Shanghai'. "
    "Defaults to 'Asia/Shanghai' when omitted on create."
)

_COMMAND_DESC = (
    "What the bot is asked to do when the routine fires, written as an "
    "instruction to the bot."
)


class ScheduleTrigger(BaseModel):
    """What makes a routine fire. Today that is always a schedule."""

    # A typed nested object rather than a bare cron string so other trigger
    # kinds (event, webhook) can be added without a breaking change.

    model_config = ConfigDict(
        json_schema_extra={"example": {"type": "schedule", "cron": "0 9 * * 1"}}
    )

    type: Literal["schedule"] = Field(
        default="schedule", description="Trigger kind. Only 'schedule' exists today."
    )
    cron: str = Field(description=_CRON_DESC)


class Routine(BaseModel):
    """A task the bot runs on a trigger."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "routine_id": "rt-4471",
                "bot_id": "20260810_q5o4c89g",
                "name": "Monday digest",
                "trigger": {"type": "schedule", "cron": "0 9 * * 1"},
                "command": "Summarise last week's commits and post the digest.",
                "enabled": True,
                "timezone": "Asia/Shanghai",
                "gmt_create": "2026-07-30T09:00:00+00:00",
                "gmt_modified": "2026-07-30T09:12:04+00:00",
            }
        }
    )

    routine_id: str = Field(
        description="Identifier of this routine. Use it in the path of the "
        "per-routine endpoints."
    )
    bot_id: str = Field(description="Bot that runs this routine.")
    name: str = Field(description="Human-readable routine name.")
    trigger: ScheduleTrigger = Field(description="What makes the routine fire.")
    command: str = Field(description=_COMMAND_DESC)
    enabled: bool = Field(
        description="False while the routine is paused; a disabled routine keeps "
        "its schedule but does not fire."
    )
    timezone: str | None = Field(
        default=None, description="Time zone the schedule runs in; null when the "
        "routine records none."
    )
    gmt_create: str = Field(description="Creation time (ISO 8601); may be empty.")
    gmt_modified: str = Field(
        description="Last-modified time (ISO 8601); may be empty."
    )


class RoutineCreate(BaseModel):
    """Create-a-routine request body."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "bot_id": "20260810_q5o4c89g",
                "name": "Monday digest",
                "trigger": {"type": "schedule", "cron": "0 9 * * 1"},
                "command": "Summarise last week's commits and post the digest.",
                "timezone": "Asia/Shanghai",
                "enabled": True,
            }
        }
    )

    bot_id: str = Field(description="Bot that will run this routine.")
    name: str = Field(description="Human-readable routine name.")
    trigger: ScheduleTrigger = Field(description="What makes the routine fire.")
    command: str = Field(description=_COMMAND_DESC)
    timezone: str | None = Field(default=None, description=_TIMEZONE_DESC)
    enabled: bool = Field(
        default=True, description="Send false to create the routine paused."
    )


class RoutineUpdate(BaseModel):
    """Partial update of a routine. Omit a field to leave it unchanged."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"trigger": {"type": "schedule", "cron": "0 8 * * 1"}}
        }
    )

    name: str | None = Field(default=None, description="New name; omit to keep.")
    trigger: ScheduleTrigger | None = Field(
        default=None, description="New trigger; omit to keep."
    )
    command: str | None = Field(default=None, description="New command; omit to keep.")
    timezone: str | None = Field(
        default=None, description="New time zone; omit to keep."
    )
    enabled: bool | None = Field(
        default=None, description="Pause or resume the routine; omit to keep."
    )


class RoutineRun(BaseModel):
    """One execution of a routine."""

    run_id: str = Field(description="Identifier of this execution.")
    routine_id: str = Field(description="Routine that was executed.")
    # Deliberately not a closed set, and not the run-now vocabulary. This model
    # serves two endpoints: run-now synthesizes its status here, while run
    # history forwards whatever the bot's engine recorded — `succeeded` and
    # `success` both occur in practice. Publishing the synthesized three as if
    # they were the whole set told history readers that the value they actually
    # receive cannot happen.
    status: str = Field(
        description="Outcome of the execution, as reported by the bot. Not a "
        "closed set — engines record their own values, so match leniently. The "
        "run-now endpoint reports one of completed, failed, or unknown; run "
        "history reports whatever the bot recorded."
    )
    started_at: str | None = Field(
        default=None, description="When the run started (ISO 8601); null when the "
        "bot did not report it."
    )
    finished_at: str | None = Field(
        default=None, description="When the run finished (ISO 8601); null when the "
        "bot did not report it."
    )
