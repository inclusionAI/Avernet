"""HTTP schemas for BBS interaction writes and summaries."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

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
    model_config = ConfigDict(extra="forbid")

    actor_type: InteractionActorTypeWire = Field(
        description="Actor type: HUMAN or BOT."
    )
    actor_id: str = Field(
        min_length=1,
        max_length=256,
        description="Stable HUMAN work-no or BOT id performing the action.",
    )
    action_type: InteractionActionTypeWire = Field(
        description="Append-only interaction event type."
    )
    target_type: InteractionTargetTypeWire = Field(
        description="Target type: TOPIC, POST, HUMAN, or BOT."
    )
    target_id: str = Field(
        min_length=1,
        max_length=MAX_TARGET_ID_LENGTH,
        description="Stable target identifier.",
    )


class InteractionRecorded(BaseModel):
    activity: Literal["recorded"] = "recorded"
    actor_type: InteractionActorTypeWire
    actor_id: str
    action_type: InteractionActionTypeWire
    target_type: InteractionTargetTypeWire
    target_id: str
    recorded: bool

    @classmethod
    def from_record(cls, record) -> InteractionRecorded:
        return cls(
            actor_type=record.actor_type.value,
            actor_id=record.actor_id,
            action_type=record.action_type.value,
            target_type=record.target_type.value,
            target_id=record.target_id,
            recorded=True,
        )


class InteractionSummaryItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_type: Optional[InteractionActorTypeWire] = None
    actor_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    target_type: Optional[InteractionTargetTypeWire] = None
    target_id: Optional[str] = Field(
        default=None, min_length=1, max_length=MAX_TARGET_ID_LENGTH
    )


class InteractionSummaryBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[InteractionSummaryItemRequest] = Field(
        max_length=MAX_SUMMARY_BATCH_ITEMS,
        description="Actor-only, target-only, and actor-target summary requests.",
    )


class InteractionStats(BaseModel):
    view_count: int = Field(ge=0)
    like_count: int = Field(ge=0)
    favorite_count: int = Field(ge=0)
    follow_count: int = Field(ge=0)
    accept_count: int = Field(ge=0)
    topic_count: int = Field(ge=0)
    reply_count: int = Field(ge=0)
    accepted_reply_count: int = Field(ge=0)
    reply_participant_count: int = Field(ge=0)
    helpful_reply_count: int = Field(ge=0)


class InteractionSummaryResponse(BaseModel):
    scope: Literal["ACTOR", "TARGET", "ACTOR_TARGET"]
    actor_type: Optional[InteractionActorTypeWire] = None
    actor_id: Optional[str] = None
    target_type: Optional[InteractionTargetTypeWire] = None
    target_id: Optional[str] = None
    stats: InteractionStats

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
    items: list[InteractionSummaryResponse]
