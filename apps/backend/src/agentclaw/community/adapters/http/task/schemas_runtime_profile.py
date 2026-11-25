"""Typed task runtime-profile request contract."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RuntimeProfileDTO(BaseModel):
    """Create-time runtime strategy snapshot for `execution_config.runtime_profile`."""

    model_config = ConfigDict(extra="allow")

    allowed_run_modes: list[str] | None = Field(
        None,
        description=(
            "Task-level execution-mode allowlist for Relay successors. Omitting or "
            "supplying an empty list keeps the default single_bot/coop_group/bbs modes."
        ),
    )
