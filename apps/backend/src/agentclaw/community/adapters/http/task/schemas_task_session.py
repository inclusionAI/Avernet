"""Session-keyed relay task reference DTOs for the collaboration surface."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class BySessionTaskDTO(BaseModel):
    """A lightweight task-node reference linked to a chat session id."""

    task_id: str = Field(..., description="The id of the task owning the relay node")
    owner_user_id: str = Field(
        ..., description="The user id owning the task in the current environment"
    )
    node_id: str = Field(
        ..., description="The task node id executing in the chat session"
    )
    updated_at: datetime | None = Field(
        default=None,
        description="The newest persisted timestamp for the linked run row, if available",
    )


class BySessionDTO(BaseModel):
    """The ordered set of lightweight task references for one chat session."""

    session_id: str = Field(..., description="The chat session id used for lookup")
    items: list[BySessionTaskDTO] = Field(
        default_factory=list,
        description="Task references ordered newest first and deduplicated by task id",
    )
    total: int = Field(..., ge=0, description="The number of returned task references")


def by_session_refs_to_dto(session_id: str, refs) -> BySessionDTO:
    """Translate core session task references to the public list DTO."""
    items = [
        BySessionTaskDTO(
            task_id=ref.task_id,
            owner_user_id=ref.owner_user_id,
            node_id=ref.node_id,
            updated_at=ref.updated_at,
        )
        for ref in refs
    ]
    return BySessionDTO(session_id=session_id, items=items, total=len(items))
