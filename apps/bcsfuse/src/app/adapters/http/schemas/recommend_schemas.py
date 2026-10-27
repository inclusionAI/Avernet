"""
Recommend API Schemas

Public-safe request/response models for the bot recommendation API.
Aligned with original contract for /api/v1/recommend.

S28B-2B-12: Public-safe contract models for route skeletons.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class BotRecommendationRequest(BaseModel):
    """
    Bot recommendation request.

    Attributes:
        question: User's question or task description
        topK: Number of bot recommendations to return
        driver_bot_id: Optional driver bot profile key
        group_id: Optional group ID for context
        min_score: Minimum recommendation score threshold
        expand_factor: Expansion factor for vector search
        enable_rerank: Whether to enable reranking
        reranker_model: Optional reranker model name
        filters: Optional metadata filters
        type: Query type (search or recommend)
    """

    question: str = Field(
        min_length=1,
        description="User's question or task description for bot matching",
    )

    topK: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of bot recommendations to return",
    )

    driver_bot_id: Optional[str] = Field(
        default=None,
        description="Optional driver bot profile key for fusion",
    )

    group_id: Optional[str] = Field(
        default=None,
        description="Optional group ID for context optimization",
    )

    min_score: float = Field(
        default=0.01,
        ge=0.0,
        le=1.0,
        description="Minimum recommendation score threshold (0-1)",
    )

    expand_factor: int = Field(
        default=10,
        ge=1,
        le=10,
        description="Expansion factor for vector search retrieval",
    )

    enable_rerank: bool = Field(
        default=True,
        description="Whether to enable reranking",
    )

    reranker_model: Optional[str] = Field(
        default=None,
        description="Optional reranker model name (None = use global config)",
    )

    filters: Optional[dict[str, Any]] = Field(
        default=None,
        description="Optional metadata filters for recommendation",
    )

    type: Literal["search", "recommend"] = Field(
        default="recommend",
        description="Query type: search (keyword) or recommend (system)",
    )

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "examples": [
                {
                    "question": "E-commerce promotion technical risk assessment",
                    "topK": 5,
                    "driver_bot_id": "wrk_tech_lead_001:default",
                    "min_score": 0.01,
                    "group_id": "grp_abc123",
                    "type": "recommend",
                }
            ]
        },
    }


class BotRecommendation(BaseModel):
    """
    Single bot recommendation item.

    Attributes:
        profile_key: Profile unique key
        worker_id: Worker ID (staff_id)
        score: Recommendation score (0-1)
        reasons: List of recommendation reasons
        short_profile: Short profile summary (max 30 chars)
        profile_tags: Profile tags dictionary
    """

    profile_key: str = Field(
        min_length=1,
        description="Profile unique key",
    )

    worker_id: str = Field(
        min_length=1,
        description="Worker ID (staff_id)",
    )

    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Recommendation score (0-1)",
    )

    reasons: list[str | dict[str, Any]] = Field(
        default_factory=list,
        description="List of recommendation reasons",
    )

    short_profile: str = Field(
        default="",
        description="Short profile summary (max 30 chars)",
    )

    profile_tags: dict[str, str] = Field(
        default_factory=dict,
        description="Profile tags dictionary",
    )


class BotRecommendationResponse(BaseModel):
    """
    Bot recommendation response.

    Attributes:
        trace_id: Trace ID for tracking
        type: Query type echo back
        driver_bot_id: Suggested driver bot
        recommendations: List of recommendations
    """

    trace_id: str = Field(
        description="Trace ID for frontend tracking",
    )

    type: Literal["search", "recommend"] = Field(
        default="recommend",
        description="Query type echo back",
    )

    driver_bot_id: Optional[str] = Field(
        default=None,
        description="Suggested driver bot (first recommendation if not specified)",
    )

    recommendations: list[BotRecommendation] = Field(
        default_factory=list,
        description="List of bot recommendations",
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Diagnostic metadata for debugging and analysis",
    )

    model_config = {
        "extra": "forbid",
    }


__all__ = [
    "BotRecommendationRequest",
    "BotRecommendation",
    "BotRecommendationResponse",
]