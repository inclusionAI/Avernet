"""
OSS Route Mount Layer

Provides safe route mounting for OSS deployment:
1. Mounts import-safe routes directly
2. Creates thin wrapper routes for blocked routes
3. Uses OSS dependency layer for dependency injection
4. Does NOT import or mount routes with forbidden internal dependencies

This module is part of the S6 OSS migration.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def include_oss_routes(app):
    """
    Include OSS-safe routes into the FastAPI application.

    This function:
    1. Mounts routes that are safe to import (fusion, verify)
    2. Creates thin wrapper routes for blocked routes (workers, profiles, search)
    3. Does NOT mount routes with forbidden internal imports

    Args:
        app: FastAPI application instance
    """
    logger.info("[OSS Routes] Mounting OSS-safe routes...")

    # Create main API router
    api_router = APIRouter(prefix="/v1")

    # Mount safe routes directly
    _mount_safe_routes(api_router)

    # Create thin wrapper routes for blocked routes
    _create_thin_wrapper_routes(api_router)

    # Include router in app
    app.include_router(api_router)

    logger.info("[OSS Routes] OSS routes mounted successfully")


def _mount_safe_routes(router: APIRouter):
    """
    Mount routes that are safe to import.

    According to route_import_inventory.py:
    - fusion_routes.py: SAFE
    - verify_routes.py: SAFE
    """
    try:
        # Import safe routes
        from src.interfaces.api.fusion_routes import router as fusion_router
        from src.interfaces.api.verify_routes import router as verify_router

        router.include_router(fusion_router, prefix="/v1", tags=["Fusion"])
        logger.info("[OSS Routes] ✓ Mounted fusion_routes")

        router.include_router(verify_router, prefix="/v1", tags=["Verify"])
        logger.info("[OSS Routes] ✓ Mounted verify_routes")

    except ImportError as e:
        logger.warning(f"[OSS Routes] Failed to import safe route: {e}")


def _create_thin_wrapper_routes(router: APIRouter):
    """
    Create thin wrapper routes for routes that are blocked by internal imports.

    Blocked routes (need thin wrappers):
    - worker_routes.py (DRM imports)
    - profile_routes.py (ZDAS imports)
    - recommend_routes.py (DRM imports)

    These wrappers use OSS dependency layer instead of internal dependencies.
    """
    from src.interfaces.api.oss_dependencies import (
        get_provider_registry,
        check_provider_availability,
    )

    # Health/status endpoint for providers
    @router.get("/providers/status", tags=["Debug"])
    async def providers_status(request: Request):
        """Check provider availability status."""
        availability = check_provider_availability(request)
        return {
            "providers": availability,
            "all_available": all(availability.values()),
        }

    # Workers thin wrapper routes
    @router.get("/workers", tags=["Workers"])
    async def list_workers_oss(request: Request):
        """
        List workers (OSS wrapper).

        Uses provider registry instead of internal dependencies.
        """
        from src.interfaces.api.oss_dependencies import get_worker_store

        store = get_worker_store(request)
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": "worker_store provider not available in OSS mode"
                }
            )

        # Delegate to store
        try:
            workers = store.list()
            return {
                "success": True,
                "items": [
                    {
                        "id": w.id,
                        "name": w.identity.name if hasattr(w, 'identity') else w.get('name', 'unknown'),
                        "type": str(w.type) if hasattr(w, 'type') else w.get('type', 'bot'),
                    }
                    for w in workers[:100]  # Limit to 100 for safety
                ],
                "total": len(workers),
            }
        except Exception as e:
            logger.error(f"[Workers OSS] Failed to list workers: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "LIST_WORKERS_ERROR", "message": str(e)}
            )

    @router.get("/workers/{worker_id}", tags=["Workers"])
    async def get_worker_oss(worker_id: str, request: Request):
        """
        Get worker by ID (OSS wrapper).

        Uses provider registry instead of internal dependencies.
        """
        from src.interfaces.api.oss_dependencies import get_worker_store

        store = get_worker_store(request)
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": "worker_store provider not available in OSS mode"
                }
            )

        try:
            worker = store.get_by_id(worker_id)
            if worker is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "WORKER_NOT_FOUND", "message": f"Worker {worker_id} not found"}
                )

            # Convert to dict for safe serialization
            if hasattr(worker, 'model_dump'):
                worker_dict = worker.model_dump()
            elif hasattr(worker, 'dict'):
                worker_dict = worker.dict()
            else:
                worker_dict = dict(worker)

            return {
                "success": True,
                "worker": worker_dict,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[Workers OSS] Failed to get worker {worker_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "GET_WORKER_ERROR", "message": str(e)}
            )

    # Profiles thin wrapper routes
    @router.get("/workers/{worker_id}/profiles", tags=["Profiles"])
    async def list_profiles_oss(worker_id: str, request: Request):
        """
        List profiles for a worker (OSS wrapper).

        Uses provider registry instead of internal dependencies.
        """
        from src.interfaces.api.oss_dependencies import get_profile_store

        store = get_profile_store(request)
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": "profile_store provider not available in OSS mode"
                }
            )

        try:
            result = store.list_profiles(worker_id)
            return {
                "success": True,
                "items": [
                    {
                        "worker_id": p.worker_id if hasattr(p, 'worker_id') else p.get('worker_id'),
                        "profile_id": p.profile_id if hasattr(p, 'profile_id') else p.get('profile_id'),
                    }
                    for p in result.items[:100]  # Limit to 100
                ],
                "total": result.total,
            }
        except Exception as e:
            logger.error(f"[Profiles OSS] Failed to list profiles for worker {worker_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "LIST_PROFILES_ERROR", "message": str(e)}
            )

    # Search thin wrapper route
    class SearchRequestOSS(BaseModel):
        """Search request (OSS wrapper)"""
        query: str = Field(..., min_length=1, description="Search query")
        top_k: int = Field(default=10, ge=1, le=50, description="Number of results")
        mode: str = Field(default="auto", description="Search mode")
        min_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Minimum score threshold")

    @router.post("/search", tags=["Search"])
    async def search_oss(request: Request, search_req: SearchRequestOSS):
        """
        Search profiles (OSS wrapper).

        Uses provider registry instead of internal dependencies.
        """
        import time
        from src.interfaces.api.oss_dependencies import (
            get_embedding_provider,
            get_vector_store,
        )

        start_time = time.time()

        embedding_provider = get_embedding_provider(request)
        vector_store = get_vector_store(request)

        if embedding_provider is None or vector_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": "embedding_provider or vector_store not available in OSS mode"
                }
            )

        try:
            # Generate embedding
            logger.info(f"[Search OSS] Generating embedding for query: {search_req.query[:50]}...")
            query_embedding = embedding_provider.embed(search_req.query)
            logger.info(f"[Search OSS] Query embedding dimension: {len(query_embedding) if query_embedding else 0}")

            # Search vector store
            logger.info(f"[Search OSS] Searching vector store for top_k={search_req.top_k}")
            results = vector_store.search(query_embedding, top_k=search_req.top_k)
            logger.info(f"[Search OSS] Search returned {len(results)} results")

            # Calculate timing
            elapsed_ms = (time.time() - start_time) * 1000

            # Format results with proper fields
            formatted_results = []
            for r in results:
                # Extract from payload (priority) or fallback to ID parsing
                payload = r.payload if hasattr(r, 'payload') else r.get('payload', {})
                raw_id = r.id if hasattr(r, 'id') else r.get('id', '')
                score = r.score if hasattr(r, 'score') else r.get('score', 0.0)

                # Priority 1: Extract from payload
                worker_id = payload.get('worker_id', '')
                profile_id = payload.get('profile_id', '')
                profile_key = payload.get('profile_key', '')
                fragment_type = payload.get('fragment_type', '')

                # Priority 2: Fallback to parsing from fragment ID if payload missing
                # Fragment ID format: {worker_id}:{profile_id}:{fragment_type}
                if not worker_id or not profile_id or not profile_key:
                    parts = raw_id.split(":")
                    if len(parts) >= 3:
                        # Format: worker_id:profile_id:fragment_type
                        worker_id = worker_id or parts[0]
                        profile_id = profile_id or parts[1]
                        fragment_type = fragment_type or parts[2]
                        profile_key = profile_key or f"{worker_id}:{profile_id}"
                    elif len(parts) == 2:
                        # Format: worker_id:profile_id
                        worker_id = worker_id or parts[0]
                        profile_id = profile_id or parts[1]
                        profile_key = profile_key or f"{worker_id}:{profile_id}"

                # Structured logging for debugging
                logger.info(
                    f"[SEARCH_RESULT_TRANSFORM] "
                    f"raw_id={raw_id}, "
                    f"payload_worker_id={payload.get('worker_id', 'N/A')}, "
                    f"payload_profile_id={payload.get('profile_id', 'N/A')}, "
                    f"payload_profile_key={payload.get('profile_key', 'N/A')}, "
                    f"resolved_worker_id={worker_id}, "
                    f"resolved_profile_id={profile_id}, "
                    f"resolved_profile_key={profile_key}, "
                    f"fragment_type={fragment_type}, "
                    f"source={'payload' if payload.get('worker_id') else 'fallback_id_parse'}"
                )

                formatted_results.append({
                    "profile_key": profile_key,
                    "worker_id": worker_id,
                    "profile_id": profile_id,
                    "score": score,
                })

            return {
                "query": search_req.query,
                "top_k": search_req.top_k,
                "mode": search_req.mode,
                "results_count": len(formatted_results),
                "results": formatted_results,
                "timing_ms": round(elapsed_ms, 2),
            }
        except Exception as e:
            logger.error(f"[Search OSS] Search failed: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "SEARCH_ERROR", "message": str(e)}
            )

    logger.info("[OSS Routes] ✓ Created thin wrapper routes for workers, profiles, search")


# =============================================================================
# Blocked Routes Registry
# =============================================================================

BLOCKED_ROUTES = {
    "worker_routes": {
        "reason": "Contains DRM imports (src.infrastructure.drm.drm_resource)",
        "endpoints": [
            "POST /v1/workers",
            "GET /v1/workers",
            "GET /v1/workers/{worker_id}",
            "PUT /v1/workers/{worker_id}/online",
            "PUT /v1/workers/{worker_id}/offline",
            "DELETE /v1/workers/{worker_id}",
        ],
        "oss_alternative": "Thin wrapper routes using provider registry",
    },
    "profile_routes": {
        "reason": "Contains ZDAS imports (src.infra.adapters.zdas_worker_profile_content_store)",
        "endpoints": [
            "PUT /v1/workers/{worker_id}/profiles/{profile_id}",
            "GET /v1/workers/{worker_id}/profiles/{profile_id}",
            "DELETE /v1/workers/{worker_id}/profiles/{profile_id}",
            "GET /v1/workers/{worker_id}/profiles",
        ],
        "oss_alternative": "Thin wrapper routes using provider registry",
    },
    "recommend_routes": {
        "reason": "Contains DRM imports (src.infrastructure.drm.drm_resource)",
        "endpoints": [
            "POST /v1/recommend",
        ],
        "oss_alternative": "Not implemented in S6",
    },
}


def get_blocked_routes_info() -> dict:
    """
    Get information about blocked routes.

    Returns:
        Dict mapping blocked route module to reason and alternatives
    """
    return BLOCKED_ROUTES


__all__ = [
    'include_oss_routes',
    'get_blocked_routes_info',
    'BLOCKED_ROUTES',
]