"""
OSS Route Skeletons - Route Parity (S28B-2B-13)

Provides route skeletons for P0 critical operations in open-core.
All skeleton routes return HTTP 501 Not Implemented.

This module is part of the route parity effort (S28B-2B-13) to ensure
open-core OpenAPI schema matches the original canonical contract.

PREFIX ARCHITECTURE:
- P0 Critical routes (recommend/fusion/verify) use /api/v1 prefix

Routes in this file:
- P0 Critical: verify.batch, verify.batchAll (2 routes)

R3 Worker/Profile routes are implemented in worker_profile_parity_routes.py
R4 Recommend route is implemented in recommend_parity_routes.py
R5 Fusion route is implemented in fusion_parity_routes.py

IMPORTANT: These routes are skeletons only. No business logic implementation.
"""

from typing import Optional
from fastapi import APIRouter, Request

from src.interfaces.api.route_skeleton import raise_not_implemented
from src.interfaces.api.schemas import (
    # Verify schemas
    BatchVerifyRequest,
    BatchVerifyAllRequest,
)

# P0 Critical routes (recommend/fusion/verify) - mounted at /api/v1
p0_router = APIRouter()


# =============================================================================
# P0 Critical Routes (2 routes) - /api/v1 prefix
# =============================================================================

# NOTE: /recommend route is now implemented in recommend_parity_routes.py (S28B-2B-14)
# Removed recommend skeleton to avoid shadowing R4 implementation
# NOTE: /groups/{group_id}/fuse route is now implemented in fusion_parity_routes.py (S28B-2B-15)
# Removed fusion skeleton to avoid shadowing R5 implementation
# NOTE: /verify/batch and /verify/batchAll routes are now implemented in verify_parity_routes.py (S28B-2B-16)
# Removed verify skeletons to avoid shadowing R6 implementation


# =============================================================================
# P0 Critical Routes - ALL IMPLEMENTED (no more skeletons)
# =============================================================================
# All P0 routes (recommend, fusion, verify) are now implemented:
# - /recommend (R4) -> recommend_parity_routes.py
# - /groups/{group_id}/fuse (R5) -> fusion_parity_routes.py
# - /verify/batch (R6) -> verify_parity_routes.py
# - /verify/batchAll (R6) -> verify_parity_routes.py
#
# No P0 skeletons remain in this file.


# =============================================================================
# OSS Route Mounting Helper
# =============================================================================

def include_route_skeletons(app) -> None:
    """
    Mount route skeletons into FastAPI application.

    This function mounts P0 route skeletons with the /api/v1 prefix.
    R3 worker/profile routes are now implemented in worker_profile_parity_routes.py.

    Args:
        app: FastAPI application instance
    """
    # Mount P0 routes at /api/v1 (recommend, fusion, verify)
    app.include_router(p0_router, prefix="/api/v1", tags=["P0-Skeletons"])