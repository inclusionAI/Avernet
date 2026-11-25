"""Read-only, process-local execution observation; not Relay task state."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SessionRuntimeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    session_id: str
    found: bool
    run_id: str | None = None
    state: Literal["starting", "running", "final", "error", "aborted"] | None = None
    started_at: int | None = Field(default=None, ge=0)
    last_event_seq: int | None = Field(default=None, ge=0)
    reason: str | None = None

    @model_validator(mode="after")
    def validate_observation(self) -> "SessionRuntimeStatus":
        if self.found and (
            not self.run_id
            or self.state is None
            or self.started_at is None
            or self.last_event_seq is None
        ):
            raise ValueError(
                "A found runtime requires run identity, state and sequence"
            )
        if not self.found and not self.reason:
            raise ValueError("A missing observation requires a reason")
        return self
