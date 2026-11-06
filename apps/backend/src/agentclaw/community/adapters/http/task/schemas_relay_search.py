"""Typed HTTP contract for Relay candidate search."""

from __future__ import annotations

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    ValidationInfo,
)


from agentclaw.community.core.task.domain.search_viewer import (
    SearchViewerType,
    normalize_search_viewer,
)


class TaskSearchRequestDTO(BaseModel):
    """Search candidates for the current network-admitted Relay baton."""

    model_config = ConfigDict(extra="ignore")

    query: str = Field(..., min_length=1)
    task_id: str = Field(..., min_length=1)
    node_id: str = Field(..., min_length=1)
    relay_token: str = Field(..., min_length=1)
    viewer_actor_type: SearchViewerType | None = None
    viewer_actor_id: StrictStr | None = Field(default=None, validate_default=True)

    @field_validator("viewer_actor_id", mode="after")
    @classmethod
    def _validate_viewer(cls, value: str | None, info: ValidationInfo) -> str | None:
        # Field-level errors do not echo the whole body (including relay_token).
        viewer = normalize_search_viewer(info.data.get("viewer_actor_type"), value)
        return viewer.get("viewer_actor_id")

    @field_validator("query", "task_id", "node_id", "relay_token", mode="after")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be a non-empty string")
        return value


class BotCandidateDTO(BaseModel):
    """HTTP projection of a Relay bot candidate."""

    bot_id: str
    name: str
    description: str = ""
    score: float | None = None


class TaskSearchResultDTO(BaseModel):
    """Search response for the current Relay baton."""

    candidates: list[BotCandidateDTO] = Field(default_factory=list)
