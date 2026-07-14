"""VectorPoint domain model for vector storage.

This model represents a single vector point in a vector store,
consisting of an identifier, the vector itself, and optional payload metadata.
"""

from typing import Any

from pydantic import BaseModel, Field


class VectorPoint(BaseModel):
    """Represents a single vector point in vector storage.

    This is the fundamental unit stored in a VectorStore. It contains:
    - id: A unique identifier for the vector (usually mapped from profile_key)
    - vector: The actual embedding vector (list of floats)
    - payload: Optional metadata that can be stored alongside the vector

    Attributes:
        id: Unique identifier for this vector point
        vector: The embedding vector as a list of floats
        payload: Optional metadata dictionary

    Example:
        >>> point = VectorPoint(
        ...     id="wrk_test_architect:default",
        ...     vector=[0.1, 0.2, 0.3, 0.4],
        ...     payload={"staff_id": "wrk_test_architect", "profile_type": "default"}
        ... )
    """

    id: str = Field(..., min_length=1, description="Unique identifier for this vector point")
    vector: list[float] = Field(..., description="The embedding vector")
    payload: dict[str, Any] = Field(default_factory=dict, description="Optional metadata")

    model_config = {"extra": "forbid"}