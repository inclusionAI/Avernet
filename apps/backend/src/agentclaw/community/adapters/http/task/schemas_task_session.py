"""Session-keyed relay task reference DTO for the public collaboration surface."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BySessionDTO(BaseModel):
    """A relay task node reference resolved from a chat session id."""

    task_id: str = Field(..., description="The id of the task owning the relay node")
    owner_user_id: str = Field(
        ..., description="The user id owning the task in the current environment"
    )
    node_id: str = Field(
        ..., description="The task node id executing in the chat session"
    )


def by_session_ref_to_dto(ref) -> BySessionDTO:
    """Translate a core session task reference to its public DTO."""
    return BySessionDTO(
        task_id=ref.task_id,
        owner_user_id=ref.owner_user_id,
        node_id=ref.node_id,
    )
