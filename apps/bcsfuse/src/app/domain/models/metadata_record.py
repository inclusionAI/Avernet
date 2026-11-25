"""MetadataRecord domain model for worker profile metadata storage.

This model represents the metadata associated with a worker profile
that is stored separately from the vector representation.
"""

from typing import Any

from pydantic import BaseModel, Field


class MetadataRecord(BaseModel):
    """Represents metadata for a worker profile in the metadata store.

    This record is stored separately from vector embeddings and contains
    structured information needed for filtering and retrieval.

    Key responsibilities:
    - Store profile identification and classification
    - Support domain/skill/role based filtering
    - Maintain profile_key <-> vector_id mapping
    - Store additional metadata in payload

    Attributes:
        profile_key: Unique identifier for the profile (e.g., "wrk_123:default")
        vector_id: Corresponding vector ID in VectorStore (None if not indexed)
        staff_id: Worker ID
        profile_id: Profile identifier (e.g., "default", "bot_name")
        profile_type: Type of profile (e.g., "default", "bot")
        domains: List of domains this profile covers (e.g., ["backend", "frontend"])
        active_skill_names: List of active skills (e.g., ["python", "java"])
        suitable_roles: List of suitable roles (e.g., ["developer", "architect"])
        source_root: Origin location of the profile data
        payload: Additional metadata dictionary

    Example:
        >>> record = MetadataRecord(
        ...     profile_key="wrk_test_architect:default",
        ...     vector_id=42,
        ...     staff_id="wrk_test_architect",
        ...     profile_id="default",
        ...     profile_type="default",
        ...     domains=["backend", "devops"],
        ...     active_skill_names=["python", "kubernetes"],
        ...     suitable_roles=["developer", "architect"],
        ...     source_root="/data/workers"
        ... )
    """

    profile_key: str = Field(..., min_length=1, description="Unique profile identifier")
    vector_id: int | None = Field(None, description="Corresponding vector ID in VectorStore")
    staff_id: str = Field(..., min_length=1, description="Worker ID")
    profile_id: str = Field(..., min_length=1, description="Profile identifier")
    profile_type: str = Field(..., min_length=1, description="Type of profile")
    domains: list[str] = Field(default_factory=list, description="List of domains")
    active_skill_names: list[str] = Field(default_factory=list, description="List of active skills")
    suitable_roles: list[str] = Field(default_factory=list, description="List of suitable roles")
    source_root: str = Field(..., min_length=1, description="Origin location of profile data")
    payload: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    short_profile: str = Field(default="", description="精简画像（30字以内），用于快速展示")

    model_config = {"extra": "forbid"}