"""HTTP schemas for BBS interaction writes and summaries."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentclaw.community.core.task.forum.interaction.models import (
    MAX_SUMMARY_BATCH_ITEMS,
    MAX_TARGET_ID_LENGTH,
    ForumInteractionSummaryQuery,
    ForumInteractionSummaryResult,
    ForumSummaryScope,
)

InteractionActorTypeWire = Literal["HUMAN", "BOT"]
InteractionActionTypeWire = Literal["VIEW", "LIKE", "FAVORITE", "FOLLOW", "ACCEPT"]
InteractionTargetTypeWire = Literal["TOPIC", "POST", "HUMAN", "BOT"]


class CreateInteractionRequest(BaseModel):
    """Request to record one +1 action or cancel one action with -1."""

    model_config = ConfigDict(extra="forbid")

    actor_type: InteractionActorTypeWire = Field(
        description="The type of identity performing the interaction: HUMAN or BOT."
    )
    actor_id: str = Field(
        min_length=1,
        max_length=256,
        description="The stable HUMAN work-no or BOT id performing the interaction.",
    )
    action_type: InteractionActionTypeWire = Field(
        description="The interaction action: VIEW, LIKE, FAVORITE, FOLLOW, or ACCEPT."
    )
    target_type: InteractionTargetTypeWire = Field(
        description="The target type: TOPIC, POST, HUMAN, or BOT."
    )
    target_id: str = Field(
        min_length=1,
        max_length=MAX_TARGET_ID_LENGTH,
        description="The stable id of the Topic, Post, Human, or Bot being interacted with.",
    )
    active: Literal[1, -1] = Field(
        description="The +1 delta records this action; -1 cancels a like/favorite/follow."
    )

    @field_validator("active", mode="before")
    @classmethod
    def _active_must_be_integer_delta(cls, value: object) -> int:
        """Reject non-integer inputs before bool/int equality accepts them."""
        if not isinstance(value, int) or isinstance(value, bool) or value not in (1, -1):
            raise ValueError("active must be 1 or -1")
        return value


class InteractionRecorded(BaseModel):
    """Confirmation that one interaction action state was persisted."""

    activity: Literal["recorded"] = Field(
        default="recorded", description="Constant activity discriminator."
    )
    actor_type: InteractionActorTypeWire = Field(
        description="The identity type that performed the interaction."
    )
    actor_id: str = Field(
        description="The stable identity id that performed the interaction."
    )
    action_type: InteractionActionTypeWire = Field(
        description="The interaction action that was recorded."
    )
    target_type: InteractionTargetTypeWire = Field(
        description="The target type that was recorded."
    )
    target_id: str = Field(description="The stable target id that was recorded.")
    active: int = Field(
        ge=0,
        description="The accumulated active amount after this interaction request. Zero is inactive; positive values contribute to counts.",
    )
    recorded: bool = Field(
        description="Whether the action state was persisted for this unique actor/action/target."
    )

    @classmethod
    def from_record(cls, record) -> InteractionRecorded:
        return cls(
            activity="recorded",
            actor_type=record.actor_type.value,
            actor_id=record.actor_id,
            action_type=record.action_type.value,
            target_type=record.target_type.value,
            target_id=record.target_id,
            active=record.active,
            recorded=True,
        )


class InteractionSummaryItemRequest(BaseModel):
    """One actor-only, target-only, or actor-target summary dimension."""

    model_config = ConfigDict(extra="forbid")

    actor_type: Optional[InteractionActorTypeWire] = Field(
        default=None,
        description="Optional actor filter type: HUMAN or BOT. Required with actor_id.",
    )
    actor_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="Optional stable HUMAN work-no or BOT id used as the actor dimension.",
    )
    target_type: Optional[InteractionTargetTypeWire] = Field(
        default=None,
        description="Optional target filter type: TOPIC, POST, HUMAN, or BOT. Required with target_id.",
    )
    target_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=MAX_TARGET_ID_LENGTH,
        description="Optional stable target id used as the target dimension.",
    )


class InteractionSummaryBatchRequest(BaseModel):
    """Request for an ordered batch of interaction summary dimensions."""

    model_config = ConfigDict(extra="forbid")

    items: list[InteractionSummaryItemRequest] = Field(
        max_length=MAX_SUMMARY_BATCH_ITEMS,
        description="Mixed summary dimensions in request order; at most 100 items are accepted.",
    )


class InteractionStats(BaseModel):
    """Aggregated counters for one interaction summary scope."""

    view_count: int = Field(
        ge=0, description="Number of active VIEW amount in scope."
    )
    like_count: int = Field(
        ge=0, description="Number of active LIKE amount in scope."
    )
    favorite_count: int = Field(
        ge=0, description="Number of active FAVORITE amount in scope."
    )
    follow_count: int = Field(
        ge=0, description="Number of active FOLLOW amount in scope."
    )
    accept_count: int = Field(
        ge=0, description="Number of active ACCEPT amount in scope."
    )
    topic_count: int = Field(
        ge=0, description="Topics authored by an actor; zero outside actor scope."
    )
    reply_count: int = Field(
        ge=0, description="Replies authored by an actor; zero outside actor scope."
    )
    accepted_reply_count: int = Field(
        ge=0,
        description="Currently helpful first-level replies authored by an actor; zero outside actor scope.",
    )
    reply_participant_count: int = Field(
        ge=0,
        description="Unique reply authors for a Topic target; zero outside Topic target scope.",
    )
    helpful_reply_count: int = Field(
        ge=0,
        description="Currently helpful first-level replies for a Topic target; zero outside Topic target scope.",
    )


class InteractionSummaryResponse(BaseModel):
    """Interaction counters for the selected actor, target, or actor-target scope."""

    scope: Literal["ACTOR", "TARGET", "ACTOR_TARGET"] = Field(
        description="The summary scope selected by the supplied dimensions."
    )
    actor_type: Optional[InteractionActorTypeWire] = Field(
        description="Echoed actor type when the summary includes an actor dimension."
    )
    actor_id: Optional[str] = Field(
        description="Echoed actor id when the summary includes an actor dimension."
    )
    target_type: Optional[InteractionTargetTypeWire] = Field(
        description="Echoed target type when the summary includes a target dimension."
    )
    target_id: Optional[str] = Field(
        description="Echoed target id when the summary includes a target dimension."
    )
    stats: InteractionStats = Field(
        description="Aggregated interaction and content counters."
    )

    @classmethod
    def from_result(
        cls, result: ForumInteractionSummaryResult
    ) -> InteractionSummaryResponse:
        return cls(
            scope=result.scope.value
            if isinstance(result.scope, ForumSummaryScope)
            else result.scope,
            actor_type=result.actor_type.value
            if result.actor_type is not None
            else None,
            actor_id=result.actor_id,
            target_type=result.target_type.value
            if result.target_type is not None
            else None,
            target_id=result.target_id,
            stats=InteractionStats(
                view_count=result.stats.view_count,
                like_count=result.stats.like_count,
                favorite_count=result.stats.favorite_count,
                follow_count=result.stats.follow_count,
                accept_count=result.stats.accept_count,
                topic_count=result.stats.topic_count,
                reply_count=result.stats.reply_count,
                accepted_reply_count=result.stats.accepted_reply_count,
                reply_participant_count=result.stats.reply_participant_count,
                helpful_reply_count=result.stats.helpful_reply_count,
            ),
        )


def domain_summary_query(
    item: InteractionSummaryItemRequest,
) -> ForumInteractionSummaryQuery:
    """Translate one HTTP schema item into the transport-agnostic query."""

    return ForumInteractionSummaryQuery(
        actor_type=None if item.actor_type is None else item.actor_type,
        actor_id=item.actor_id,
        target_type=None if item.target_type is None else item.target_type,
        target_id=item.target_id,
    )


class InteractionSummaryBatchResponse(BaseModel):
    """Ordered responses for a batch interaction summary request."""

    items: list[InteractionSummaryResponse] = Field(
        description="Summary results in the same order as the request items."
    )
