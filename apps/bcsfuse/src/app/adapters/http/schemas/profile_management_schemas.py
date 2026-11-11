"""
Profile Management API Schemas

Public-safe request/response models for profile management routes.
Aligned with original contract for profile search, quality, analyze, patch routes.

S28B-2B-12: Public-safe contract models for route skeletons.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Profile Search Schemas
# =============================================================================

class ProfileSearchRequest(BaseModel):
    """
    Profile search request.

    Attributes:
        query: Search query string
        top_k: Number of results to return
        filters: Optional metadata filters
        min_score: Minimum similarity score
        search_type: Search type (vector, keyword, hybrid)
    """

    query: str = Field(
        min_length=1,
        max_length=2000,
        description="Search query string",
    )

    top_k: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Number of results to return",
    )

    filters: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional metadata filters",
    )

    min_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum similarity score",
    )

    search_type: str = Field(
        default="hybrid",
        description="Search type (vector, keyword, hybrid)",
    )


class ProfileSearchResult(BaseModel):
    """
    Single profile search result.

    Attributes:
        profile_key: Profile unique key
        worker_id: Worker ID
        score: Similarity score
        matched_content: Matched content preview
        highlights: Highlighted matches
    """

    profile_key: str = Field(
        description="Profile unique key",
    )

    worker_id: str = Field(
        description="Worker ID",
    )

    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Similarity score",
    )

    matched_content: Optional[str] = Field(
        default=None,
        description="Matched content preview",
    )

    highlights: list[str] = Field(
        default_factory=list,
        description="Highlighted matches",
    )


class ProfileSearchResponse(BaseModel):
    """
    Profile search response.

    Attributes:
        results: Search results
        total: Total matching profiles
        query: Original query
        search_type: Search type used
        trace_id: Trace ID for tracking
    """

    results: list[ProfileSearchResult] = Field(
        default_factory=list,
        description="Search results",
    )

    total: int = Field(
        default=0,
        ge=0,
        description="Total matching profiles",
    )

    query: str = Field(
        description="Original query",
    )

    search_type: str = Field(
        default="hybrid",
        description="Search type used",
    )

    trace_id: str = Field(
        default="",
        description="Trace ID for tracking",
    )


# =============================================================================
# Active Profiles Schemas
# =============================================================================

class ActiveProfileItem(BaseModel):
    """
    Active profile item.

    Attributes:
        profile_key: Profile unique key
        worker_id: Worker ID
        profile_id: Profile ID
        display_name: Display name
        is_active: Whether profile is active
    """

    profile_key: str = Field(
        description="Profile unique key",
    )

    worker_id: str = Field(
        description="Worker ID",
    )

    profile_id: str = Field(
        description="Profile ID",
    )

    display_name: Optional[str] = Field(
        default=None,
        description="Display name",
    )

    is_active: bool = Field(
        default=True,
        description="Whether profile is active",
    )


class ActiveProfilesResponse(BaseModel):
    """
    Active profiles response.

    Attributes:
        profiles: List of active profiles
        total: Total active profiles
    """

    profiles: list[ActiveProfileItem] = Field(
        default_factory=list,
        description="List of active profiles",
    )

    total: int = Field(
        default=0,
        ge=0,
        description="Total active profiles",
    )


# =============================================================================
# Profile Quality Schemas
# =============================================================================

class ProfileQualityScore(BaseModel):
    """
    Profile quality score breakdown.

    Attributes:
        dimension: Quality dimension
        score: Score for this dimension
        weight: Weight in overall score
        details: Additional details
    """

    dimension: str = Field(
        description="Quality dimension",
    )

    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Score for this dimension",
    )

    weight: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Weight in overall score",
    )

    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional details",
    )


class ProfileQualityResponse(BaseModel):
    """
    Profile quality response.

    Attributes:
        profile_key: Profile unique key
        worker_id: Worker ID
        overall_score: Overall quality score
        scores: Dimension scores
        recommendations: Improvement recommendations
        last_analyzed: Last analysis timestamp
    """

    profile_key: str = Field(
        description="Profile unique key",
    )

    worker_id: str = Field(
        description="Worker ID",
    )

    overall_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Overall quality score (0-1)",
    )

    scores: list[ProfileQualityScore] = Field(
        default_factory=list,
        description="Dimension scores",
    )

    recommendations: list[str] = Field(
        default_factory=list,
        description="Improvement recommendations",
    )

    last_analyzed: Optional[datetime] = Field(
        default=None,
        description="Last analysis timestamp",
    )


# =============================================================================
# Profile Analyze Schemas
# =============================================================================

class ProfileAnalyzeRequest(BaseModel):
    """
    Profile analyze request.

    Attributes:
        analyze_type: Analysis type (quality, capability, completeness)
        options: Analysis options
    """

    analyze_type: str = Field(
        default="quality",
        description="Analysis type (quality, capability, completeness)",
    )

    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Analysis options",
    )


class ProfileCapabilityAnalysis(BaseModel):
    """
    Profile capability analysis result.

    Attributes:
        capability: Detected capability
        confidence: Detection confidence
        evidence: Supporting evidence
    """

    capability: str = Field(
        description="Detected capability",
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Detection confidence",
    )

    evidence: list[str] = Field(
        default_factory=list,
        description="Supporting evidence",
    )


class ProfileAnalyzeResponse(BaseModel):
    """
    Profile analyze response.

    Attributes:
        profile_key: Profile unique key
        worker_id: Worker ID
        analyze_type: Analysis type performed
        capabilities: Detected capabilities
        quality_score: Overall quality score
        completeness: Completeness score
        suggestions: Improvement suggestions
        analysis_metadata: Analysis metadata
    """

    profile_key: str = Field(
        description="Profile unique key",
    )

    worker_id: str = Field(
        description="Worker ID",
    )

    analyze_type: str = Field(
        description="Analysis type performed",
    )

    capabilities: list[ProfileCapabilityAnalysis] = Field(
        default_factory=list,
        description="Detected capabilities",
    )

    quality_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Overall quality score",
    )

    completeness: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Completeness score",
    )

    suggestions: list[str] = Field(
        default_factory=list,
        description="Improvement suggestions",
    )

    analysis_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Analysis metadata",
    )


# =============================================================================
# Profile Patch Schemas
# =============================================================================

class ProfilePatchRequest(BaseModel):
    """
    Profile partial update request.

    Attributes:
        content: Profile content updates (SOUL.md, AGENTS.md, etc.)
        metadata: Metadata updates
        skill_sets: Skill set updates
        display_name: Display name update
        description: Description update
        is_active: Active status update
    """

    content: Optional[dict[str, str]] = Field(
        default=None,
        description="Profile content updates (SOUL.md, AGENTS.md, etc.)",
    )

    metadata: Optional[dict[str, Any]] = Field(
        default=None,
        description="Metadata updates",
    )

    skill_sets: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="Skill set updates",
    )

    display_name: Optional[str] = Field(
        default=None,
        description="Display name update",
    )

    description: Optional[str] = Field(
        default=None,
        description="Description update",
    )

    is_active: Optional[bool] = Field(
        default=None,
        description="Active status update",
    )


class ProfilePatchResponse(BaseModel):
    """
    Profile patch response.

    Attributes:
        profile_key: Profile unique key
        worker_id: Worker ID
        updated_fields: List of updated fields
        updated_at: Update timestamp
        version: New version number
        is_active: Current active status
    """

    profile_key: str = Field(
        description="Profile unique key",
    )

    worker_id: str = Field(
        description="Worker ID",
    )

    updated_fields: list[str] = Field(
        default_factory=list,
        description="List of updated fields",
    )

    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Update timestamp",
    )

    version: int = Field(
        default=1,
        ge=1,
        description="New version number",
    )

    is_active: bool = Field(
        default=False,
        description="Current active status",
    )


# =============================================================================
# Profile Activate Schemas
# =============================================================================

class ProfileActivateResponse(BaseModel):
    """
    Profile activate response.

    Attributes:
        profile_key: Activated profile key
        worker_id: Worker ID
        previous_active: Previously active profile key
        activated_at: Activation timestamp
    """

    profile_key: str = Field(
        description="Activated profile key",
    )

    worker_id: str = Field(
        description="Worker ID",
    )

    previous_active: Optional[str] = Field(
        default=None,
        description="Previously active profile key",
    )

    activated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Activation timestamp",
    )

    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata about activation (e.g., embedding indexing status)",
    )


class ActivateResponse(BaseModel):
    """
    Activate response (OpenAPI P1 contract-aligned).

    Response for PUT /v1/workers/{worker_id}/profiles/{profile_id}/activate.

    Attributes:
        worker_id: Worker ID
        profile_id: Profile ID
        is_active: Whether profile is now active
        binding_updated: Whether profile binding was updated
        worker_updated: Whether worker record was updated
        message: Human-readable message
    """

    worker_id: str = Field(
        description="Worker ID",
    )

    profile_id: str = Field(
        description="Profile ID",
    )

    is_active: bool = Field(
        description="Whether profile is now active",
    )

    binding_updated: bool = Field(
        description="Whether profile binding was updated",
    )

    worker_updated: bool = Field(
        description="Whether worker record was updated",
    )

    message: str = Field(
        description="Human-readable message",
    )


__all__ = [
    "ProfileSearchRequest",
    "ProfileSearchResult",
    "ProfileSearchResponse",
    "ActiveProfileItem",
    "ActiveProfilesResponse",
    "ProfileQualityScore",
    "ProfileQualityResponse",
    "ProfileAnalyzeRequest",
    "ProfileCapabilityAnalysis",
    "ProfileAnalyzeResponse",
    "ProfilePatchRequest",
    "ProfilePatchResponse",
    "ProfileActivateResponse",
    "ActivateResponse",
]