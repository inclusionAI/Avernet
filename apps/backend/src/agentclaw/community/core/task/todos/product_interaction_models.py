"""Stable local models for Todo Product Runtime interactions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


MAX_PRODUCT_INTERACTION_ID_LENGTH = 128
MAX_PRODUCT_INTERACTION_MESSAGE_LENGTH = 5000


class TodoProductInteractionKind(StrEnum):
    """Who initiated the request retained in this local fact."""

    HUMAN_MESSAGE = "human_message"
    STOP_REQUEST = "stop_request"


class TodoProductInteractionDeliveryState(StrEnum):
    """Local delivery state; it never invents a Product runtime state."""

    SENDING = "sending"
    DELIVERY_UNKNOWN = "delivery_unknown"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


BLOCKING_PRODUCT_INTERACTION_STATES = frozenset(
    {
        TodoProductInteractionDeliveryState.SENDING,
        TodoProductInteractionDeliveryState.DELIVERY_UNKNOWN,
        TodoProductInteractionDeliveryState.CANCEL_REQUESTED,
    }
)


@dataclass(frozen=True)
class TodoProductInteractionResponse:
    """The bounded response returned synchronously by the Product protocol."""

    type: str
    message: str


@dataclass(frozen=True)
class TodoProductInteraction:
    """One Human request and, when available, the platform response."""

    interaction_id: str
    todo_id: str
    kind: TodoProductInteractionKind
    actor_id: str
    actor_name: str | None
    request_message: str
    delivery_state: TodoProductInteractionDeliveryState
    product_collet_id: int
    created_at: str
    updated_at: str
    collet_rollout_id: int | None = None
    expected_revision: int | None = None
    target_interaction_id: str | None = None
    response: TodoProductInteractionResponse | None = None
    failure_code: str | None = None


@dataclass(frozen=True)
class TodoProductInteractionPage:
    """A stable page plus the current send lock projection."""

    items: tuple[TodoProductInteraction, ...]
    can_send: bool
    blocking_interaction_id: str | None


def build_product_interaction_id() -> str:
    """Generate a local identity also used as the protocol request id."""

    import uuid

    return f"tpi_{uuid.uuid4().hex}"


def build_stop_request_id(interaction_id: str) -> str:
    """Build the one deterministic stop identity for a target interaction."""

    return f"{interaction_id}:shutdown:v1"
