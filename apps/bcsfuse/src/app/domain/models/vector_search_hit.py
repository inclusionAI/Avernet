"""VectorSearchHit domain model for vector search results.

This model represents a single search result from a vector store,
including the vector ID, similarity score, and optional payload.
"""

from typing import Any

from pydantic import BaseModel, Field


class VectorSearchHit(BaseModel):
    """Represents a single search result from vector store search.

    This is returned when searching a VectorStore. It contains:
    - id: The unique identifier of the matched vector
    - score: The similarity score (higher = more similar for most metrics)
    - payload: Optional metadata associated with the vector

    The score interpretation depends on the distance metric used:
    - Cosine similarity: [-1, 1], higher is more similar
    - Inner product: unbounded, higher is more similar
    - L2 distance: [0, ∞), lower is more similar

    Attributes:
        id: Unique identifier of the matched vector
        score: Similarity/distance score (interpretation depends on metric)
        payload: Optional metadata associated with the vector

    Example:
        >>> hit = VectorSearchHit(
        ...     id="wrk_test_architect:default",
        ...     score=0.95,
        ...     payload={"staff_id": "wrk_test_architect", "profile_type": "default"}
        ... )
    """

    id: str = Field(..., min_length=1, description="Unique identifier of the matched vector")
    score: float = Field(..., description="Similarity/distance score")
    payload: dict[str, Any] = Field(default_factory=dict, description="Optional metadata")

    model_config = {"extra": "forbid"}