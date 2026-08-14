"""Request/response models for the routines group (was cron)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ScheduleTrigger(BaseModel):
    """A schedule trigger. Modeled as a typed nested object so other trigger
    kinds (event, webhook) can be added later without a breaking change."""

    type: Literal["schedule"] = "schedule"
    cron: str  # cron expression, e.g. "0 9 * * *"


class Routine(BaseModel):
    """A scheduled/triggered task run by an agent."""

    routine_id: str
    bot_id: str
    name: str
    trigger: ScheduleTrigger
    command: str
    enabled: bool
    timezone: str | None = None
    gmt_create: str
    gmt_modified: str


class RoutineCreate(BaseModel):
    """Create-a-routine request body."""

    bot_id: str
    name: str
    trigger: ScheduleTrigger
    command: str
    timezone: str | None = None
    enabled: bool = True


class RoutineUpdate(BaseModel):
    """Partial update of a routine."""

    name: str | None = None
    trigger: ScheduleTrigger | None = None
    command: str | None = None
    timezone: str | None = None
    enabled: bool | None = None


class RoutineRun(BaseModel):
    """One execution of a routine."""

    run_id: str
    routine_id: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
