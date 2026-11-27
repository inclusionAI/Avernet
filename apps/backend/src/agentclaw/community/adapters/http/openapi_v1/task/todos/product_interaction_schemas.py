"""Stable HTTP schemas for Todo Product interactions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agentclaw.community.core.task.todos.product_interaction_models import (
    TodoProductInteraction,
    TodoProductInteractionDeliveryState,
    TodoProductInteractionKind,
)

TodoProductInteractionKindSchema = Literal["human_message", "stop_request"]
TodoProductInteractionDeliveryStateSchema = Literal[
    "sending",
    "delivery_unknown",
    "succeeded",
    "failed",
    "cancel_requested",
    "cancelled",
]


class CreateTodoProductInteractionRequest(BaseModel):
    """Human-owned request body; identity and request id are never accepted."""

    message: str = Field(
        min_length=1,
        max_length=5000,
        description="Human instruction sent to the current Product rollout.",
    )
    reply_to_interaction_id: str | None = Field(
        default=None,
        max_length=128,
        description="Optional identity of the latest interaction being replied to.",
    )


class TodoProductPlatformResponseSchema(BaseModel):
    """Bounded protocol response mapped only through its stable enum."""

    type: Literal["DO_NOTHING", "OPTIMIZE", "RE_DO", "SHUTDOWN"] = Field(
        description="Product runtime action.",
    )
    message: str = Field(description="Safe response text retained by Todo.")

    @classmethod
    def from_record(cls, record) -> "TodoProductPlatformResponseSchema":
        return cls(type=record.type, message=record.message)


class TodoProductInteractionResponseSchema(BaseModel):
    """One request/response dialog; protocol objects are never leaked."""

    interaction_id: str = Field(description="Local identity and protocol request id.")
    todo_id: str = Field(description="Owning Todo identity.")
    kind: TodoProductInteractionKindSchema = Field(description="Request kind.")
    actor_id: str = Field(description="Authenticated Human actor identity.")
    actor_name: str | None = Field(
        default=None,
        description="Human display-name snapshot when one is available.",
    )
    request_message: str = Field(description="Stored Human request text.")
    delivery_state: TodoProductInteractionDeliveryStateSchema = Field(
        description="Local delivery state, never a Product runtime state."
    )
    product_collet_id: int = Field(
        gt=0,
        description="Product Collet bound to this Todo interaction.",
    )
    collet_rollout_id: int | None = Field(
        default=None,
        description="Current rollout captured before the protocol call.",
    )
    expected_revision: int | None = Field(
        default=None,
        description="Revision fence captured by the Todo backend.",
    )
    target_interaction_id: str | None = Field(
        default=None,
        description="Previous interaction targeted by a reply or deterministic stop request.",
    )
    response: TodoProductPlatformResponseSchema | None = Field(
        default=None,
        description="Bounded synchronized platform response.",
    )
    failure_code: str | None = Field(
        default=None,
        description="Stable internal delivery failure code.",
    )
    created_at: str = Field(description="Local creation timestamp.")
    updated_at: str = Field(description="Last local delivery-state update timestamp.")

    @classmethod
    def from_record(cls, record: TodoProductInteraction) -> "TodoProductInteractionResponseSchema":
        return cls(
            interaction_id=record.interaction_id,
            todo_id=record.todo_id,
            kind=record.kind.value,
            actor_id=record.actor_id,
            actor_name=record.actor_name,
            request_message=record.request_message,
            delivery_state=record.delivery_state.value,
            product_collet_id=record.product_collet_id,
            collet_rollout_id=record.collet_rollout_id,
            expected_revision=record.expected_revision,
            target_interaction_id=record.target_interaction_id,
            response=(
                TodoProductPlatformResponseSchema.from_record(record.response)
                if record.response is not None
                else None
            ),
            failure_code=record.failure_code,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class TodoProductInteractionPageResponse(BaseModel):
    """Interaction page plus the product send lock."""

    items: list[TodoProductInteractionResponseSchema] = Field(
        description="Interactions in creation order.",
    )
    can_send: bool = Field(description="Whether a new Human request can be sent.")
    blocking_interaction_id: str | None = Field(
        default=None,
        description="Interaction currently blocking new input, if any.",
    )

    @classmethod
    def from_record(cls, record) -> "TodoProductInteractionPageResponse":
        return cls(
            items=[TodoProductInteractionResponseSchema.from_record(item) for item in record.items],
            can_send=record.can_send,
            blocking_interaction_id=record.blocking_interaction_id,
        )
