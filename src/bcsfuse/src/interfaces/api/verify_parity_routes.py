"""
R6 Verify Business Logic Routes

Implements R6 verify canonical routes with minimal public-safe implementation.
These routes replace the 501 skeleton routes for capability verification.

S28B-2B-16: Verify Business Logic Implementation

Implementation Policy:
- Mode: minimal_public_safe (deterministic profile check)
- LLM: NOT used (no external LLM service)
- Probe executor: NOT used (no probe execution)
- Verification: Simple profile content validation only
- Scoring: Deterministic based on profile completeness
- No fake verification results

Routes implemented:
- P0 Critical: POST /verify/batch (1 route)
- P0 Critical: POST /verify/batchAll (1 route)

This implementation uses public-safe stores only and does NOT depend on internal providers.
"""

import logging
import uuid
from typing import Optional
from fastapi import APIRouter, Request, HTTPException, status

from src.interfaces.api.schemas.verify_schemas import (
    BatchVerifyRequest,
    BatchVerifyAllRequest,
    BatchVerifyResponse,
    WorkerVerifyResult,
    CapabilityVerificationResult,
    DimensionResult,
    DimensionJudgment,
)

logger = logging.getLogger(__name__)

# Router for R6 verify routes - mounted at /api/v1
router = APIRouter()


# =============================================================================
# Dependency Injection Helpers
# =============================================================================

def _get_worker_registry_store(request: Request):
    """Get worker registry store from provider registry."""
    return request.app.state.context.registry.get('worker_registry_store')


def _get_profile_content_store(request: Request):
    """Get profile content store from provider registry."""
    return request.app.state.context.registry.get('worker_profile_content_store')


def _require_auth(request: Request) -> None:
    """Require authentication for protected endpoints."""
    from src.bootstrap.oss_business_routes import require_oss_auth
    require_oss_auth(request)


# =============================================================================
# Minimal Verification Implementation
# =============================================================================

def _calculate_profile_complete_score(profile_content: Optional[dict]) -> float:
    """
    Calculate a simple profile completeness score.

    This is a minimal deterministic scoring based on profile content structure.
    Higher score for more complete profiles.

    Args:
        profile_content: Profile content dict or None

    Returns:
        Score between 0.0 and 1.0
    """
    if not profile_content:
        return 0.0

    score = 0.0

    # Check for skills
    if 'skills' in profile_content:
        skills = profile_content['skills']
        if isinstance(skills, list) and len(skills) > 0:
            score += 0.2

    # Check for tags
    if 'tags' in profile_content:
        tags = profile_content['tags']
        if isinstance(tags, (dict, list)) and len(tags) > 0:
            score += 0.2

    # Check for responsibilities
    if 'responsibilities' in profile_content:
        responsibilities = profile_content['responsibilities']
        if isinstance(responsibilities, list) and len(responsibilities) > 0:
            score += 0.2

    # Check for description
    if 'description' in profile_content and profile_content['description']:
        score += 0.2

    # Check for metadata
    if 'metadata' in profile_content and profile_content['metadata']:
        score += 0.2

    return min(1.0, score)


def _extract_claimed_capabilities(profile_content: Optional[dict]) -> list[str]:
    """
    Extract claimed capabilities from profile content.

    This is a minimal extraction without NLP/ML.
    Uses simple field extraction.

    Args:
        profile_content: Profile content dict or None

    Returns:
        List of claimed capability names
    """
    if not profile_content:
        return []

    capabilities = []

    # Skills are capabilities
    if 'skills' in profile_content:
        skills = profile_content['skills']
        if isinstance(skills, list):
            capabilities.extend(str(s) for s in skills)

    # Tags can indicate capabilities
    if 'tags' in profile_content:
        tags = profile_content['tags']
        if isinstance(tags, dict):
            capabilities.extend(str(v) for v in tags.values())
        elif isinstance(tags, list):
            capabilities.extend(str(t) for t in tags)

    # Responsibilities indicate capabilities
    if 'responsibilities' in profile_content:
        responsibilities = profile_content['responsibilities']
        if isinstance(responsibilities, list):
            capabilities.extend(str(r) for r in responsibilities[:3])  # Limit to 3

    return capabilities[:10]  # Limit to top 10


def _create_minimal_verification(
    worker_id: str,
    profile_key: str,
    profile_content: Optional[dict],
    requested_capabilities: Optional[list[str]] = None,
) -> WorkerVerifyResult:
    """
    Create a minimal deterministic verification result.

    This is a minimal implementation without:
    - LLM-based verification
    - Probe execution
    - External validation

    The verification is based on profile completeness and existence only.

    Args:
        worker_id: Worker ID
        profile_key: Profile key
        profile_content: Profile content dict or None
        requested_capabilities: Specific capabilities to verify (None = all claimed)

    Returns:
        WorkerVerifyResult with deterministic verification
    """
    # Calculate profile completeness score
    profile_score = _calculate_profile_complete_score(profile_content)

    # Extract claimed capabilities
    claimed_capabilities = _extract_claimed_capabilities(profile_content)

    # Filter to requested capabilities if specified
    if requested_capabilities:
        capabilities_to_verify = [c for c in claimed_capabilities if c in requested_capabilities]
    else:
        capabilities_to_verify = claimed_capabilities

    # Create capability verification results
    capability_results = []
    for capability_name in capabilities_to_verify:
        # For minimal verification, we just check existence
        # and assign a score based on profile completeness
        capability_result = CapabilityVerificationResult(
            capability_name=capability_name,
            overall_confidence=profile_score,
            dimensions=[
                DimensionResult(
                    capability_name=capability_name,
                    dimension="profile_existence",
                    probe_prompt="[minimal_public_safe] No probe executed",
                    response_content="",
                    failed=False,
                )
            ],
            judgments=[
                DimensionJudgment(
                    capability_name=capability_name,
                    dimension="profile_existence",
                    confidence=profile_score,
                    reasoning="[deterministic_minimal] Based on profile completeness only",
                )
            ],
            verified=profile_score > 0.5,  # Threshold for minimal verification
            notes="[deterministic_minimal] Verified by profile completeness only",
        )
        capability_results.append(capability_result)

    # Determine overall status
    if not profile_content:
        status_str = "failed"
        error_msg = "Profile not found"
    elif not capabilities_to_verify:
        status_str = "pending"
        error_msg = None
    elif all(cr.verified for cr in capability_results):
        status_str = "verified"
        error_msg = None
    else:
        status_str = "pending"
        error_msg = None

    return WorkerVerifyResult(
        worker_id=worker_id,
        profile_key=profile_key,
        capabilities=capability_results,
        overall_score=profile_score,
        status=status_str,
        error=error_msg,
    )


# =============================================================================
# P0 Critical Routes - Verify (2 routes)
# =============================================================================

@router.post(
    "/verify/batch",
    response_model=BatchVerifyResponse,
    status_code=status.HTTP_200_OK,
    summary="Batch verify worker capabilities (minimal public-safe)",
    description="""
R6: Batch verify route (minimal public-safe implementation).

**Implementation Mode**: minimal_public_safe
- Uses deterministic profile completeness check
- No LLM-based verification
- No probe execution
- No external validation services
- Returns honest metadata about mode and limitations

**Verification**: Based on profile existence and completeness only
**Confidence**": Determined by profile content structure
**Capabilities**: Extracted from profile skills/tags/responsibilities

**Open-Core Limitations**:
- No LLM-based capability verification
- No probe execution or testing
- No external validation
- No semantic understanding
- Lower verification confidence compared to internal runtime

For production-grade verification, consider:
- Configuring LLM provider
- Using internal runtime with full verification pipeline
""",
    tags=["Verify"],
)
async def verify_batch(request: Request, req: BatchVerifyRequest):
    """
    P0: Batch verify worker capabilities using minimal public-safe implementation.

    This is a minimal implementation for open-core without:
    - LLM-based verification
    - Probe execution
    - External validation services

    Returns deterministic results based on profile existence and completeness.
    """
    _require_auth(request)

    # Get stores
    worker_store = _get_worker_registry_store(request)
    profile_store = _get_profile_content_store(request)

    # Generate trace ID
    trace_id = str(uuid.uuid4())

    # Verify each worker
    results = []
    verified_count = 0
    failed_count = 0

    for worker_id in req.worker_ids:
        try:
            # Get worker from registry
            worker = worker_store.get_by_id(worker_id) if worker_store else None

            if not worker:
                # Worker not found
                result = WorkerVerifyResult(
                    worker_id=worker_id,
                    profile_key=worker_id,
                    capabilities=[],
                    overall_score=0.0,
                    status="failed",
                    error=f"Worker {worker_id} not found",
                )
                results.append(result)
                failed_count += 1
                continue

            # Get profile key from worker
            profile_key = worker.active_profile_key or worker.id

            # Get profile content
            profile_content = None
            if profile_store:
                profile_content = profile_store.get(profile_key)

            # Perform minimal verification
            result = _create_minimal_verification(
                worker_id=worker_id,
                profile_key=profile_key,
                profile_content=profile_content,
                requested_capabilities=req.capabilities,
            )

            results.append(result)

            if result.status == "verified":
                verified_count += 1
            elif result.status == "failed":
                failed_count += 1

        except Exception as e:
            logger.error(f"Error verifying worker {worker_id}: {e}", exc_info=True)
            result = WorkerVerifyResult(
                worker_id=worker_id,
                profile_key=worker_id,
                capabilities=[],
                overall_score=0.0,
                status="failed",
                error=str(e),
            )
            results.append(result)
            failed_count += 1

    return BatchVerifyResponse(
        results=results,
        total=len(results),
        verified=verified_count,
        failed=failed_count,
        trace_id=trace_id,
    )


@router.post(
    "/verify/batchAll",
    response_model=BatchVerifyResponse,
    status_code=status.HTTP_200_OK,
    summary="Batch verify all workers (minimal public-safe)",
    description="""
R6: Batch verify all route (minimal public-safe implementation).

**Implementation Mode**: minimal_public_safe
- Uses deterministic profile completeness check
- No LLM-based verification
- No probe execution
- No external validation services
- Returns honest metadata about mode and limitations

**Verification**: Based on profile existence and completeness only
**Confidence**": Determined by profile content structure
**Capabilities**: Extracted from profile skills/tags/responsibilities

**Open-Core Limitations**:
- No LLM-based capability verification
- No probe execution or testing
- No external validation
- No semantic understanding
- Lower verification confidence compared to internal runtime

For production-grade verification, consider:
- Configuring LLM provider
- Using internal runtime with full verification pipeline
""",
    tags=["Verify"],
)
async def verify_batch_all(request: Request, req: BatchVerifyAllRequest):
    """
    P0: Batch verify all workers using minimal public-safe implementation.

    This is a minimal implementation for open-core without:
    - LLM-based verification
    - Probe execution
    - External validation services

    Returns deterministic results based on profile existence and completeness.
    """
    _require_auth(request)

    # Get stores
    worker_store = _get_worker_registry_store(request)
    profile_store = _get_profile_content_store(request)

    # Generate trace ID
    trace_id = str(uuid.uuid4())

    # Get all workers from registry
    all_workers = []
    if worker_store:
        # Apply filters if provided
        all_workers = worker_store.list()

        if req.filters:
            # Simple filter matching
            filtered_workers = []
            for worker in all_workers:
                match = True
                for key, value in req.filters.items():
                    if worker.get(key) != value:
                        match = False
                        break
                if match:
                    filtered_workers.append(worker)
            all_workers = filtered_workers

    # Verify each worker
    results = []
    verified_count = 0
    failed_count = 0

    for worker in all_workers:
        try:
            worker_id = worker.id
            profile_key = worker.active_profile_key or worker_id

            # Get profile content
            profile_content = None
            if profile_store:
                profile_content = profile_store.get(profile_key)

            # Perform minimal verification
            result = _create_minimal_verification(
                worker_id=worker_id,
                profile_key=profile_key,
                profile_content=profile_content,
                requested_capabilities=req.capabilities,
            )

            results.append(result)

            if result.status == "verified":
                verified_count += 1
            elif result.status == "failed":
                failed_count += 1

        except Exception as e:
            logger.error(f"Error verifying worker: {e}", exc_info=True)
            worker_id = worker.id if hasattr(worker, 'id') else 'unknown'
            result = WorkerVerifyResult(
                worker_id=worker_id,
                profile_key=worker_id,
                capabilities=[],
                overall_score=0.0,
                status="failed",
                error=str(e),
            )
            results.append(result)
            failed_count += 1

    return BatchVerifyResponse(
        results=results,
        total=len(results),
        verified=verified_count,
        failed=failed_count,
        trace_id=trace_id,
    )


# =============================================================================
# Route Mounting Helper
# =============================================================================

def include_r6_routes(app) -> None:
    """
    Mount R6 verify routes into FastAPI application.

    This function mounts R6 verify routes with the /api/v1 prefix.

    Args:
        app: FastAPI application instance
    """
    app.include_router(router, prefix="/api/v1", tags=["R6-Verify"])
    logger.info("[R6 Routes] R6 verify routes mounted successfully")


__all__ = ["router", "include_r6_routes"]