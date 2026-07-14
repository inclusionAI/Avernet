"""
R4 Recommend Business Logic Routes

Implements R4 recommend canonical route with minimal public-safe implementation.
This route replaces the 501 skeleton route for bot recommendation.

S28B-2B-14: Recommend Business Logic Implementation

Implementation Policy:
- Mode: minimal_public_safe (keyword/profile matching)
- Vector search: NOT configured (no external embedding service)
- Fallback: empty results with honest metadata
- No fake recommendations

Routes implemented:
- P0 Critical: POST /recommend (1 route)

This implementation uses public-safe stores only and does NOT depend on internal providers.
"""

import logging
import re
from typing import Optional
from fastapi import APIRouter, Request, HTTPException, status

from src.interfaces.api.schemas.recommend_schemas import (
    BotRecommendationRequest,
    BotRecommendationResponse,
    BotRecommendation,
)

logger = logging.getLogger(__name__)

# Router for R4 recommend routes - mounted at /api/v1
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
# Keyword Matching Utilities
# =============================================================================

def _extract_keywords(text: str) -> set[str]:
    """
    Extract keywords from text for simple matching.

    This is a minimal implementation without NLP/ML.
    Uses simple tokenization and filtering.

    Args:
        text: Input text

    Returns:
        Set of lowercase keywords
    """
    # Convert to lowercase
    text = text.lower()

    # Simple tokenization: extract words (alphanumeric + Chinese characters)
    # Match Chinese characters: \u4e00-\u9fff
    words = re.findall(r'[\w\u4e00-\u9fff]+', text)

    # Filter out common stop words (minimal set)
    stop_words = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'should',
        'could', 'may', 'might', 'must', 'shall', 'can', 'need', 'dare',
        '的', '了', '和', '是', '在', '有', '我', '他', '她', '它',
        '们', '这', '那', '就', '也', '都', '而', '及', '与', '或',
    }

    keywords = {word for word in words if word not in stop_words and len(word) > 1}
    return keywords


def _calculate_simple_score(
    question_keywords: set[str],
    worker_metadata: dict,
    profile_content: Optional[dict] = None,
) -> float:
    """
    Calculate a simple matching score based on keyword overlap.

    This is a minimal scoring algorithm:
    - Check worker responsibilities/description for keyword matches
    - Check profile tags/skills if available
    - Return normalized score (0.0 to 1.0)

    Args:
        question_keywords: Keywords extracted from question
        worker_metadata: Worker metadata dict
        profile_content: Optional profile content dict

    Returns:
        Score between 0.0 and 1.0
    """
    if not question_keywords:
        return 0.01  # Minimal score if no keywords

    # Collect text from worker metadata
    searchable_text = []

    # Add worker responsibilities
    if 'responsibilities' in worker_metadata:
        responsibilities = worker_metadata['responsibilities']
        if isinstance(responsibilities, list):
            searchable_text.extend(str(r) for r in responsibilities)
        else:
            searchable_text.append(str(responsibilities))

    # Add worker description/name
    for field in ['worker_name', 'description', 'source_type', 'domain']:
        if field in worker_metadata:
            searchable_text.append(str(worker_metadata[field]))

    # Add profile content if available
    if profile_content:
        if 'skills' in profile_content:
            skills = profile_content['skills']
            if isinstance(skills, list):
                searchable_text.extend(str(s) for s in skills)
            else:
                searchable_text.append(str(skills))

        if 'tags' in profile_content:
            tags = profile_content['tags']
            if isinstance(tags, dict):
                searchable_text.extend(str(v) for v in tags.values())
            elif isinstance(tags, list):
                searchable_text.extend(str(t) for t in tags)

    # Combine all searchable text
    combined_text = ' '.join(searchable_text).lower()
    worker_keywords = _extract_keywords(combined_text)

    # Calculate Jaccard similarity
    if not worker_keywords:
        return 0.01

    intersection = question_keywords & worker_keywords
    union = question_keywords | worker_keywords

    if not union:
        return 0.01

    # Use Jaccard similarity as base score
    jaccard_score = len(intersection) / len(union)

    # Boost score if multiple keywords match
    if len(intersection) >= 3:
        jaccard_score = min(1.0, jaccard_score * 1.5)
    elif len(intersection) >= 2:
        jaccard_score = min(1.0, jaccard_score * 1.2)

    # Ensure minimum score for any match
    if intersection:
        jaccard_score = max(jaccard_score, 0.1)

    return jaccard_score


# =============================================================================
# P0 Critical Routes - Recommend (1 route)
# =============================================================================

@router.post(
    "/recommend",
    response_model=BotRecommendationResponse,
    status_code=status.HTTP_200_OK,
    summary="Recommend bots for a question (minimal public-safe)",
    description="""
R4: Bot recommendation route (minimal public-safe implementation).

**Implementation Mode**: minimal_public_safe
- Uses keyword matching without vector search
- No external embedding service required
- Returns honest metadata about mode and limitations

**Scoring**: Simple keyword overlap (Jaccard similarity)
**Recommendations**: Only returns workers with score >= min_score

**Open-Core Limitations**:
- No semantic search (keyword matching only)
- No LLM-based question rewriting
- No vector embeddings
- Lower recommendation quality compared to internal runtime

For production-grade recommendations, consider:
- Configuring embedding provider
- Using vector store (Qdrant/FAISS)
- Internal runtime with full pipeline
""",
    tags=["Recommend"],
)
async def recommend_bots(request: Request, req: BotRecommendationRequest):
    """
    P0: Recommend bots using minimal public-safe implementation.

    This is a minimal implementation for open-core without:
    - Vector search / embeddings
    - LLM question rewriting
    - External services

    Returns recommendations with honest metadata about limitations.
    """
    _require_auth(request)

    # Get stores
    registry_store = _get_worker_registry_store(request)
    profile_store = _get_profile_content_store(request)

    if registry_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_registry_store provider not available",
                "mode": "minimal_public_safe",
            }
        )

    # Extract keywords from question
    question_keywords = _extract_keywords(req.question)
    logger.info(
        f"[Recommend][R4] question='{req.question[:50]}...', "
        f"keywords={question_keywords}, topK={req.topK}"
    )

    # Get all available workers
    try:
        # List all workers (limit to reasonable number for performance)
        workers = registry_store.list(limit=1000)
    except Exception as e:
        logger.error(f"[Recommend][R4] Failed to list workers: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "LIST_WORKERS_ERROR",
                "message": str(e),
                "mode": "minimal_public_safe",
            }
        )

    # Filter by availability if specified in filters
    filtered_workers = []
    if req.filters and 'availability' in req.filters:
        allowed_availabilities = req.filters['availability']
        for worker in workers:
            # Check worker availability if present
            worker_dict = worker.model_dump() if hasattr(worker, 'model_dump') else worker.dict() if hasattr(worker, 'dict') else dict(worker)
            worker_availability = worker_dict.get('availability', 'unknown')
            if worker_availability in allowed_availabilities:
                filtered_workers.append(worker)
    else:
        # Default: only protected and public workers
        for worker in workers:
            worker_dict = worker.model_dump() if hasattr(worker, 'model_dump') else worker.dict() if hasattr(worker, 'dict') else dict(worker)
            worker_availability = worker_dict.get('availability', 'unknown')
            if worker_availability in ['protected', 'public']:
                filtered_workers.append(worker)

    workers = filtered_workers

    # Calculate scores for each worker
    scored_workers = []
    for worker in workers:
        # Convert Worker object to dict
        worker_dict = worker.model_dump() if hasattr(worker, 'model_dump') else worker.dict() if hasattr(worker, 'dict') else dict(worker)

        worker_id = worker_dict.get('worker_id', '')
        profile_key = worker_dict.get('profile_key', '')

        # Get profile content if available
        profile_content = None
        if profile_store and worker_id and profile_key:
            try:
                profile_content = profile_store.get(worker_id, profile_key)
            except Exception as e:
                logger.debug(f"[Recommend][R4] Failed to get profile for {worker_id}: {e}")

        # Calculate score
        score = _calculate_simple_score(
            question_keywords=question_keywords,
            worker_metadata=worker_dict,
            profile_content=profile_content,
        )

        # Filter by min_score
        if score >= req.min_score:
            scored_workers.append((worker_dict, score, profile_content))

    # Sort by score (descending)
    scored_workers.sort(key=lambda x: x[1], reverse=True)

    # Take top K
    top_workers = scored_workers[:req.topK]

    # Build recommendations
    recommendations = []
    for worker_dict, score, profile_content in top_workers:
        # Extract short profile
        short_profile = worker_dict.get('worker_name', worker_dict.get('worker_id', ''))[:30]

        # Extract tags from profile
        profile_tags = {}
        if profile_content and 'tags' in profile_content:
            tags = profile_content['tags']
            if isinstance(tags, dict):
                profile_tags = tags

        # Add availability and domain to tags
        if 'availability' in worker_dict:
            profile_tags['availability'] = str(worker_dict['availability'])
        if 'domain' in worker_dict:
            profile_tags['domain'] = str(worker_dict['domain'])

        # Build reasons
        reasons = [f"Keyword match (score: {score:.3f})"]

        recommendation = BotRecommendation(
            profile_key=worker_dict.get('profile_key', worker_dict.get('worker_id', '')),
            worker_id=worker_dict.get('worker_id', ''),
            score=round(score, 3),
            reasons=reasons,
            short_profile=short_profile,
            profile_tags=profile_tags,
        )
        recommendations.append(recommendation)

    # Determine driver_bot_id
    driver_bot_id = req.driver_bot_id
    if driver_bot_id is None and recommendations:
        driver_bot_id = recommendations[0].profile_key

    # Build trace_id (from request state or generate)
    trace_id = getattr(request.state, 'trace_id', 'open-core-recommend')

    # Build response
    response = BotRecommendationResponse(
        trace_id=trace_id,
        type=req.type,
        driver_bot_id=driver_bot_id,
        recommendations=recommendations,
    )

    logger.info(
        f"[Recommend][R4] result | "
        f"total_workers={len(workers)}, "
        f"scored_workers={len(scored_workers)}, "
        f"recommendations={len(recommendations)}, "
        f"mode=minimal_public_safe"
    )

    return response


# =============================================================================
# Route Mounting Helper
# =============================================================================

def include_r4_routes(app) -> None:
    """
    Mount R4 recommend route into FastAPI application.

    This function mounts the R4 recommend route with the /api/v1 prefix.

    Args:
        app: FastAPI application instance
    """
    app.include_router(router, prefix="/api/v1", tags=["R4-Recommend"])
    logger.info("[R4 Routes] R4 recommend route mounted successfully")


__all__ = ["router", "include_r4_routes"]