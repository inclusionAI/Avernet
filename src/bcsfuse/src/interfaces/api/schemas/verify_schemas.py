"""
Verify API Schemas

Public-safe request/response models for the capability verification API.
Aligned with original contract for /api/v1/verify/batch and /api/v1/verify/batchAll.

S28B-2B-12: Public-safe contract models for route skeletons.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class BatchVerifyRequest(BaseModel):
    """
    Batch verify request.

    Verify capabilities for specific workers.

    Attributes:
        worker_ids: List of worker IDs to verify
        capabilities: Specific capabilities to verify (optional)
        verify_options: Additional verification options
    """

    worker_ids: list[str] = Field(
        min_length=1,
        max_length=100,
        description="List of worker IDs to verify",
    )

    capabilities: Optional[list[str]] = Field(
        default=None,
        description="Specific capabilities to verify (None = all claimed)",
    )

    verify_options: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional verification options",
    )


class BatchVerifyAllRequest(BaseModel):
    """
    Batch verify all workers request.

    Verify capabilities for all workers.

    Attributes:
        capabilities: Specific capabilities to verify (optional)
        filters: Filters to narrow down workers
        verify_options: Additional verification options
    """

    capabilities: Optional[list[str]] = Field(
        default=None,
        description="Specific capabilities to verify (None = all claimed)",
    )

    filters: dict[str, Any] = Field(
        default_factory=dict,
        description="Filters to narrow down workers",
    )

    verify_options: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional verification options",
    )


class DimensionResult(BaseModel):
    """
    Single dimension verification result.

    Attributes:
        capability_name: Capability name
        dimension: Dimension name
        probe_prompt: Verification prompt sent
        response_content: Bot response content
        failed: Whether technical failure occurred
    """

    capability_name: str = Field(
        description="Capability name",
    )

    dimension: str = Field(
        description="Dimension name",
    )

    probe_prompt: str = Field(
        description="Verification prompt sent",
    )

    response_content: str = Field(
        default="",
        description="Bot response content",
    )

    failed: bool = Field(
        default=False,
        description="Whether technical failure occurred (timeout/empty response)",
    )


class DimensionJudgment(BaseModel):
    """
    Single dimension LLM judgment result.

    Attributes:
        capability_name: Capability name
        dimension: Dimension name
        confidence: Confidence score (0-1)
        reasoning: Judgment reasoning
    """

    capability_name: str = Field(
        description="Capability name",
    )

    dimension: str = Field(
        description="Dimension name",
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score (0-1)",
    )

    reasoning: str = Field(
        default="",
        description="Judgment reasoning",
    )


class CapabilityVerificationResult(BaseModel):
    """
    Single capability verification result.

    Attributes:
        capability_name: Capability name
        overall_confidence: Overall confidence score
        dimensions: Dimension results
        judgments: Dimension judgments
        verified: Whether capability is verified
        notes: Additional notes
    """

    capability_name: str = Field(
        description="Capability name",
    )

    overall_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Overall confidence score (0-1)",
    )

    dimensions: list[DimensionResult] = Field(
        default_factory=list,
        description="Dimension results",
    )

    judgments: list[DimensionJudgment] = Field(
        default_factory=list,
        description="Dimension judgments",
    )

    verified: bool = Field(
        default=False,
        description="Whether capability is verified",
    )

    notes: Optional[str] = Field(
        default=None,
        description="Additional notes",
    )


class WorkerVerifyResult(BaseModel):
    """
    Single worker verification result.

    Attributes:
        worker_id: Worker ID
        profile_key: Profile key
        capabilities: Capability verification results
        overall_score: Overall verification score
        status: Verification status
        error: Error message if failed
    """

    worker_id: str = Field(
        description="Worker ID",
    )

    profile_key: str = Field(
        description="Profile key",
    )

    capabilities: list[CapabilityVerificationResult] = Field(
        default_factory=list,
        description="Capability verification results",
    )

    overall_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Overall verification score (0-1)",
    )

    status: str = Field(
        default="pending",
        description="Verification status (pending, verified, failed)",
    )

    error: Optional[str] = Field(
        default=None,
        description="Error message if failed",
    )


class BatchVerifyResponse(BaseModel):
    """
    Batch verify response.

    Attributes:
        results: Worker verification results
        total: Total workers processed
        verified: Successfully verified count
        failed: Failed count
        trace_id: Trace ID for tracking
    """

    results: list[WorkerVerifyResult] = Field(
        default_factory=list,
        description="Worker verification results",
    )

    total: int = Field(
        default=0,
        ge=0,
        description="Total workers processed",
    )

    verified: int = Field(
        default=0,
        ge=0,
        description="Successfully verified count",
    )

    failed: int = Field(
        default=0,
        ge=0,
        description="Failed count",
    )

    trace_id: str = Field(
        default="",
        description="Trace ID for tracking",
    )


__all__ = [
    "BatchVerifyRequest",
    "BatchVerifyAllRequest",
    "DimensionResult",
    "DimensionJudgment",
    "CapabilityVerificationResult",
    "WorkerVerifyResult",
    "BatchVerifyResponse",
]