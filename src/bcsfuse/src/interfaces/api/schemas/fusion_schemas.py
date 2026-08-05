"""
Fusion API Schemas

Public-safe request/response models for the group fusion API.
Aligned with original contract for /api/v1/groups/{group_id}/fuse.

S28B-2B-12: Public-safe contract models for route skeletons.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


# Timeout limits
DEFAULT_TIMEOUT_MS = 120000  # 120 seconds
MIN_TIMEOUT_MS = 1000
MAX_TIMEOUT_MS_NORMAL = 600000  # 10 minutes for G1/G2
MAX_TIMEOUT_MS_EXPERT_DIAGNOSIS = 600000  # 10 minutes for G5


class FuseOptions(BaseModel):
    """
    Fusion options.

    Controls fusion behavior configuration.

    Attributes:
        timeout_ms: Fusion operation timeout in milliseconds
        parallel: Whether to collect participant perspectives in parallel
        include_recommendation: Whether to generate recommendation
        include_transcript: Whether to include full transcript
        strict_participants: Whether to fail hard on participant parsing errors
        fail_fast: Whether to return immediately on first failure
        detect_conflicts: Whether to enable conflict detection (G2)
        extract_alignment_points: Whether to extract alignment points (G2)
        enable_risk_assessment: Whether to enable risk assessment (G5)
        enable_expert_recommendations: Whether to generate expert recommendations (G5)
        enable_go_live_conditions: Whether to generate go-live conditions (G5)
        refresh: Whether to force refresh and skip cache
    """

    timeout_ms: int = Field(
        default=DEFAULT_TIMEOUT_MS,
        ge=MIN_TIMEOUT_MS,
        le=MAX_TIMEOUT_MS_EXPERT_DIAGNOSIS,
        description="Fusion operation timeout (ms). G1/G2/G5 max 600000ms (10 min)",
    )

    parallel: bool = Field(
        default=True,
        description="Whether to collect participant perspectives in parallel",
    )

    include_recommendation: bool = Field(
        default=True,
        description="Whether to generate recommendation",
    )

    include_transcript: bool = Field(
        default=False,
        description="Whether to include full transcript",
    )

    strict_participants: bool = Field(
        default=True,
        description="Whether to fail hard on participant parsing errors",
    )

    fail_fast: bool = Field(
        default=False,
        description="Whether to return immediately on first failure",
    )

    detect_conflicts: bool = Field(
        default=False,
        description="Whether to enable conflict detection (G2)",
    )

    extract_alignment_points: bool = Field(
        default=False,
        description="Whether to extract alignment points (G2)",
    )

    enable_risk_assessment: bool = Field(
        default=True,
        description="Whether to enable risk assessment (G5)",
    )

    enable_expert_recommendations: bool = Field(
        default=True,
        description="Whether to generate expert recommendations (G5)",
    )

    enable_go_live_conditions: bool = Field(
        default=True,
        description="Whether to generate go-live conditions (G5)",
    )

    refresh: bool = Field(
        default=False,
        description="Whether to force refresh and skip cache",
    )

    model_config = {"extra": "forbid"}


class FuseMetadata(BaseModel):
    """
    Fusion metadata.

    Request tracing and source information.

    Attributes:
        request_id: Request ID
        source: Request source
        operator: Operator
        trace_id: Trace ID
    """

    request_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Request ID",
    )

    source: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Request source",
    )

    operator: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Operator",
    )

    trace_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Trace ID",
    )

    model_config = {"extra": "forbid"}


class FusionRequest(BaseModel):
    """
    Fusion request.

    Initiates multi-participant perspective fusion for a question in a group.

    Attributes:
        question: Question requiring multi-party collaborative evaluation
        participants: List of participant identifiers
        driver_bot_id: Explicit driver bot ID
        mode: Fusion mode (backward compatible, fixed to "agent")
        fusion_mode: Fusion mode (G1/G2/G5)
        options: Fusion options
        metadata: Request metadata
    """

    question: str = Field(
        min_length=1,
        max_length=2000,
        description="Question requiring multi-party collaborative evaluation",
    )

    participants: list[str] = Field(
        min_length=1,
        max_length=20,
        description="List of participant identifiers",
    )

    driver_bot_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Explicit driver bot ID",
    )

    mode: Literal["agent"] = Field(
        default="agent",
        description="Fusion mode (backward compatible)",
    )

    fusion_mode: Literal["agent", "conflict_alignment", "expert_diagnosis", "bot_profile_fuse"] = Field(
        default="agent",
        description="Fusion mode: agent (G1), conflict_alignment (G2), expert_diagnosis (G5), bot_profile_fuse (G9)",
    )

    session_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Session identifier (accepted for caller compatibility; not "
        "used — Avernet G9 scopes context by the path group_id).",
    )

    options: FuseOptions = Field(
        default_factory=FuseOptions,
        description="Fusion options",
    )

    metadata: Optional[FuseMetadata] = Field(
        default=None,
        description="Request metadata",
    )

    @model_validator(mode="after")
    def validate_timeout_for_mode(self) -> "FusionRequest":
        """
        Validate timeout_ms based on fusion_mode.

        G1/G2/G5 all allow max 600000ms (10 minutes).
        """
        timeout_ms = self.options.timeout_ms

        if self.fusion_mode == "expert_diagnosis":
            max_timeout = MAX_TIMEOUT_MS_EXPERT_DIAGNOSIS
            mode_name = "G5 expert_diagnosis"
        else:
            max_timeout = MAX_TIMEOUT_MS_NORMAL
            mode_name = f"G1/G2 ({self.fusion_mode})"

        if timeout_ms > max_timeout:
            raise ValueError(
                f"timeout_ms={timeout_ms} exceeds maximum allowed for {mode_name} mode "
                f"(max={max_timeout}ms). "
                f"G1/G2 modes allow up to {MAX_TIMEOUT_MS_NORMAL}ms, "
                f"G5 expert_diagnosis allows up to {MAX_TIMEOUT_MS_EXPERT_DIAGNOSIS}ms."
            )

        return self

    model_config = {"extra": "forbid"}


class FusionPerspective(BaseModel):
    """
    Single participant's perspective in fusion.

    Phase 2.7: Add status field to match OpenAPI PerspectiveResponse contract.
    Status values: "completed", "timed_out", "failed", "skipped"

    Attributes:
        participant_id: Participant identifier
        profile_key: Profile key
        perspective: Generated perspective content
        confidence: Confidence score (0-1)
        status: Perspective collection status (completed, timed_out, failed, skipped)
        participant_type: Participant type (bot, human, etc.)
        role: Participant role (consultant, expert, etc.)
        evidence: Evidence items
        key_points: Key points (G2)
        concerns: Concerns (G2)
    """

    participant_id: str = Field(
        description="Participant identifier",
    )

    profile_key: str = Field(
        description="Profile key",
    )

    perspective: str = Field(
        description="Generated perspective content",
    )

    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence score (0-1)",
    )

    # Phase 2.7: Add status field (OpenAPI contract requirement)
    status: Optional[str] = Field(
        default=None,
        description="Perspective collection status: completed, timed_out, failed, skipped",
    )

    # Phase 2.7: Add optional fields for G2/G5 compatibility
    participant_type: Optional[str] = Field(
        default=None,
        description="Participant type (bot, human, etc.)",
    )

    role: Optional[str] = Field(
        default=None,
        description="Participant role (consultant, expert, etc.)",
    )

    evidence: Optional[list[str]] = Field(
        default_factory=list,
        description="Evidence items",
    )

    key_points: Optional[list[str]] = Field(
        default_factory=list,
        description="Key points (G2)",
    )

    concerns: Optional[list[str]] = Field(
        default_factory=list,
        description="Concerns (G2)",
    )


class ConflictPoint(BaseModel):
    """
    Conflict point detected in G2 mode.

    Attributes:
        topic: Conflict topic
        participants: Participants with conflicting views
        description: Conflict description
        severity: Conflict severity
    """

    topic: str = Field(
        description="Conflict topic",
    )

    participants: list[str] = Field(
        default_factory=list,
        description="Participants with conflicting views",
    )

    description: str = Field(
        description="Conflict description",
    )

    severity: str = Field(
        default="medium",
        description="Conflict severity (low, medium, high, critical)",
    )


class AlignmentPoint(BaseModel):
    """
    Alignment point in G2 mode.

    Attributes:
        topic: Alignment topic
        participants: Participants in agreement
        description: Alignment description
        strength: Alignment strength
    """

    topic: str = Field(
        description="Alignment topic",
    )

    participants: list[str] = Field(
        default_factory=list,
        description="Participants in agreement",
    )

    description: str = Field(
        description="Alignment description",
    )

    strength: str = Field(
        default="moderate",
        description="Alignment strength (weak, moderate, strong)",
    )


class RiskAssessment(BaseModel):
    """
    Risk assessment for G5 expert diagnosis.

    Attributes:
        risk_id: Risk identifier
        category: Risk category
        description: Risk description
        probability: Probability score
        impact: Impact score
        mitigation: Mitigation suggestions
    """

    risk_id: str = Field(
        description="Risk identifier",
    )

    category: str = Field(
        description="Risk category",
    )

    description: str = Field(
        description="Risk description",
    )


class TimingResponse(BaseModel):
    """
    Timing information for fusion operation.

    OpenAPI contract required fields.

    Attributes:
        started_at: Fusion start timestamp
        finished_at: Fusion finish timestamp
        duration_ms: Fusion duration in milliseconds
    """

    started_at: datetime = Field(
        description="Fusion start timestamp",
    )

    finished_at: datetime = Field(
        description="Fusion finish timestamp",
    )

    duration_ms: int = Field(
        description="Fusion duration in milliseconds",
    )

    probability: str = Field(
        default="medium",
        description="Probability (low, medium, high)",
    )

    impact: str = Field(
        default="medium",
        description="Impact (low, medium, high)",
    )

    mitigation: Optional[str] = Field(
        default=None,
        description="Mitigation suggestions",
    )


class FusionResult(BaseModel):
    """
    Fusion result.

    Contains the fusion output based on mode.

    OpenAPI Contract Required Fields ( FuseResponse):
    - group_id: str
    - fusion_id: str
    - question: str
    - perspectives: array
    - partial_success: bool
    - warnings: array
    - errors: array
    - timing: TimingResponse

    Attributes:
        group_id: Group identifier (OpenAPI required)
        fusion_id: Fusion operation ID
        question: Original question
        perspectives: Generated perspectives
        partial_success: Whether fusion succeeded partially (OpenAPI required)
        warnings: Warning messages (OpenAPI required)
        errors: Error messages (OpenAPI required)
        timing: Timing information (OpenAPI required)
        driver_bot_id: Driver bot ID (optional)
        fusion_mode: Fusion mode used
        conflicts: Detected conflicts (G2)
        alignments: Detected alignments (G2)
        risks: Risk assessments (G5)
        recommendation: Final recommendation
        metadata: Result metadata
    """

    # OpenAPI required fields
    group_id: str = Field(
        description="Group identifier",
    )

    fusion_id: str = Field(
        description="Fusion operation ID",
    )

    question: str = Field(
        description="Original question",
    )

    perspectives: list[FusionPerspective] = Field(
        default_factory=list,
        description="Generated perspectives",
    )

    partial_success: bool = Field(
        default=False,
        description="Whether fusion succeeded partially",
    )

    warnings: list[str] = Field(
        default_factory=list,
        description="Warning messages",
    )

    errors: list[str] = Field(
        default_factory=list,
        description="Error messages",
    )

    timing: TimingResponse = Field(
        description="Timing information",
    )

    # Optional fields
    driver_bot_id: Optional[str] = Field(
        default=None,
        description="Driver bot ID",
    )

    fusion_mode: str = Field(
        default="agent",
        description="Fusion mode used",
    )

    conflicts: list[ConflictPoint] = Field(
        default_factory=list,
        description="Detected conflicts (G2)",
    )

    alignments: list[AlignmentPoint] = Field(
        default_factory=list,
        description="Detected alignments (G2)",
    )

    risks: list[RiskAssessment] = Field(
        default_factory=list,
        description="Risk assessments (G5)",
    )

    recommendation: Optional[str] = Field(
        default=None,
        description="Final recommendation",
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Result metadata",
    )


__all__ = [
    "FuseOptions",
    "FuseMetadata",
    "FusionRequest",
    "FusionPerspective",
    "ConflictPoint",
    "AlignmentPoint",
    "RiskAssessment",
    "TimingResponse",
    "FusionResult",
]