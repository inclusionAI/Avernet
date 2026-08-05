"""
OSS Business Routes Module

Provides OSS-safe business routes that avoid importing from src.interfaces.api.*
This module is placed in src/bootstrap/ to avoid the import chain triggered by
src.interfaces.api.__init__.py.

IMPORTANT: DO NOT import from src.interfaces.api.* as it triggers:
    __init__.py -> app.py -> recommend_routes.py -> drm_resource.py -> Layotto init

Routes provided:
    - External product (/api/v1): sync, availability, online, offline, search, recommend, fuse
    - Management platform (/v1): Workers GET/list, Profiles CRUD, verify, trust-level, etc.
    - Privileged admin (/v1/admin, conditional): Workers POST/DELETE, Profiles DELETE, config PUT

Authentication:
    All business routes require authentication via Bearer token.
    Token source: BCSFUSE_AUTH_TOKEN environment variable.
    Public routes (health/ready/openapi) are defined in opensource_app.py.

Environment Variables:
    BCSFUSE_EXPOSE_ADMIN: Set to "true" to mount admin routes at /v1/admin

Authentication:
    All business routes require authentication via Bearer token.
    Token source: BCSFUSE_AUTH_TOKEN environment variable.
    Public routes (health/ready/openapi) are defined in opensource_app.py.
"""
import os
from typing import Optional, Any
import logging
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Import schemas directly (safe - no import chain)
from src.interfaces.api.schemas.worker_management_schemas import (
    ProfileRequest,
    ProfileResponse,
)

logger = logging.getLogger(__name__)

# Request models
class SearchRequestOSS(BaseModel):
    """Search request (OSS wrapper)"""
    query: str = Field(..., min_length=1, description="Search query")
    top_k: int = Field(default=10, ge=1, le=50, description="Number of results")
    mode: str = Field(default="auto", description="Search mode")
    min_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Minimum score threshold")
    filters: Optional[dict] = Field(default=None, description="Search filters")


class WorkerCreateRequestOSS(BaseModel):
    """Worker create request (OSS wrapper)"""
    worker_id: str = Field(..., min_length=1, description="Worker ID")
    name: str = Field(..., min_length=1, description="Worker name")
    description: Optional[str] = Field(None, description="Worker description")
    skills: list[str] = Field(default_factory=list, description="Worker skills")
    is_public: bool = Field(default=True, description="Public visibility")


class ProfileUpsertRequestOSS(BaseModel):
    """
    Profile upsert request (OSS wrapper).

    DEPRECATED: This schema is kept for backwards compatibility but should not be used.
    Use ProfileRequest from worker_management_schemas instead.
    """
    profile_id: str = Field(..., min_length=1, description="Profile ID")
    content: str = Field(..., min_length=1, description="Profile content")
    metadata: Optional[dict] = Field(default_factory=dict, description="Profile metadata")


# Dependency injection helpers (use app.state.context, NOT global imports)
def _get_provider_registry(request: Request):
    """Get provider registry from ApplicationContext."""
    return request.app.state.context.registry


def _get_provider(request: Request, name: str) -> Optional[Any]:
    """Get a specific provider from the registry."""
    registry = _get_provider_registry(request)
    return registry.get(name)


# ========================================
# Provider Keys - MUST match registry keys in opensource.py
# ========================================
# Registry keys (from opensource.py):
# - worker_registry_store
# - worker_profile_content_store
# - vector_store
# - embedding_provider
# ========================================

def _get_worker_registry_store(request: Request):
    """Get worker registry store from provider registry.

    NOTE: Registry key is 'worker_registry_store', NOT 'worker_store'
    """
    return _get_provider(request, 'worker_registry_store')


def _get_profile_content_store(request: Request):
    """Get profile content store from provider registry.

    NOTE: Registry key is 'worker_profile_content_store', NOT 'profile_store'
    """
    return _get_provider(request, 'worker_profile_content_store')


def _get_profile_binding_store(request: Request):
    """Get profile binding store from provider registry.

    Phase B2 Fix: Ensures same instance across all paths (Profile CRUD, Fusion, etc.)
    """
    return _get_provider(request, 'worker_profile_binding_store')


def _get_runtime_state_store(request: Request):
    """Get runtime state store from provider registry."""
    return _get_provider(request, 'worker_runtime_state_store')


def _get_audit_log_store(request: Request):
    """Get audit log store from provider registry."""
    return _get_provider(request, 'worker_audit_log_store')


def _get_index_sync_adapter(request: Request):
    """Get index sync adapter from provider registry."""
    return _get_provider(request, 'worker_index_sync_adapter')


def _get_vector_store(request: Request):
    """Get vector store from provider registry (optional)."""
    return _get_provider(request, 'vector_store')


# ========================================
# Response Conversion Helpers (Contract-First)
# ========================================

def _enum_value(value):
    """Extract enum value safely."""
    if value is None:
        return None
    if hasattr(value, "value"):
        return value.value
    return str(value)


def _dt_iso(value):
    """Convert datetime to ISO format safely."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _model_dump(value):
    """Convert model to dict safely."""
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return value


def _list_model_dump(values):
    """Convert list of models to list of dicts safely."""
    if not values:
        return []
    return [_model_dump(v) for v in values]


def _worker_to_contract_response(worker) -> dict:
    """Convert worker object/dict to flat WorkerResponse contract.

    OpenAPI WorkerResponse fields:
    - success, id, type, name, handle, description
    - lifecycle_state, runtime_state, source_type, version
    - responsibilities, domains, availability, trust_level
    - created_at, updated_at
    """
    # Handle both dict and object
    if isinstance(worker, dict):
        w = worker
    elif hasattr(worker, 'model_dump'):
        w = worker.model_dump()
    else:
        w = dict(worker)

    # Extract identity
    identity = w.get('identity', {})
    if hasattr(identity, 'model_dump'):
        identity = identity.model_dump()
    elif not isinstance(identity, dict):
        identity = dict(identity) if identity else {}

    # Extract state
    state = w.get('state', {})
    if hasattr(state, 'model_dump'):
        state = state.model_dump()
    elif not isinstance(state, dict):
        state = dict(state) if state else {}

    return {
        "success": True,
        "id": w.get('id'),
        "type": _enum_value(w.get('type')),
        "name": identity.get('name'),
        "handle": identity.get('handle'),
        "description": identity.get('description'),
        "lifecycle_state": _enum_value(w.get('lifecycle_state')),
        "runtime_state": _enum_value(state.get('runtime_state')),
        "source_type": _enum_value(w.get('source_type')),
        "version": w.get('version'),
        "responsibilities": w.get('responsibilities', []),
        "domains": w.get('domains', []),
        "availability": _enum_value(state.get('availability')),
        "trust_level": _enum_value(state.get('trust_level')),
        "created_at": _dt_iso(w.get('created_at')),
        "updated_at": _dt_iso(w.get('updated_at')),
    }


def _profile_to_contract_response(content, *, is_active: bool = False) -> dict:
    """Convert profile content to flat ProfileResponse contract.

    OpenAPI ProfileResponse fields:
    - worker_id, profile_id, display_name, soul_md, agents_md, tools_md
    - boot_md, heartbeat_md, contents, skill_sets, metadata
    - content_type, is_active, version, quality_score, quality_issues
    - created_at, updated_at
    """
    # Handle both dict and object
    if isinstance(content, dict):
        c = content
    elif hasattr(content, 'model_dump'):
        c = content.model_dump()
    else:
        c = dict(content) if content else {}

    # Debug logging
    logger.debug(f"[PROFILE_TO_CONTRACT] content_type={type(content).__name__} keys={list(c.keys()) if c else []} worker_id={c.get('worker_id')} profile_id={c.get('profile_id')}")

    # Extract skill_sets
    skill_sets = c.get('skill_sets', [])
    if skill_sets and not isinstance(skill_sets, list):
        skill_sets = []
    elif skill_sets:
        skill_sets = _list_model_dump(skill_sets)

    return {
        "worker_id": c.get('worker_id'),
        "profile_id": c.get('profile_id'),
        "display_name": c.get('display_name'),
        "soul_md": c.get('soul_md'),
        "agents_md": c.get('agents_md'),
        "tools_md": c.get('tools_md'),
        "boot_md": c.get('boot_md'),
        "heartbeat_md": c.get('heartbeat_md'),
        "contents": c.get('contents') or {},
        "skill_sets": skill_sets,
        "metadata": c.get('metadata') or {},
        "content_type": _enum_value(c.get('content_type')) or 'api',
        "is_active": is_active,  # MUST be bool, not None
        "version": c.get('version', 1),
        "quality_score": c.get('quality_score'),
        "quality_issues": c.get('quality_issues') or [],
        "created_at": _dt_iso(c.get('created_at')),
        "updated_at": _dt_iso(c.get('updated_at')),
    }


def _get_runtime_state_service(request: Request):
    """Get WorkerRuntimeStateService with all required dependencies."""
    from src.application.services.worker_runtime_state_service import WorkerRuntimeStateService

    registry_store = _get_worker_registry_store(request)
    runtime_state_store = _get_runtime_state_store(request)
    audit_log_store = _get_audit_log_store(request)
    index_sync_adapter = _get_index_sync_adapter(request)
    vector_store = _get_vector_store(request)

    if registry_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_registry_store provider not available"
            }
        )

    if runtime_state_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_runtime_state_store provider not available"
            }
        )

    return WorkerRuntimeStateService(
        registry_store=registry_store,
        runtime_state_store=runtime_state_store,
        audit_log_adapter=audit_log_store,
        index_sync_adapter=index_sync_adapter,
        vector_store=vector_store,
    )


def _get_embedding_provider(request: Request):
    """Get embedding provider from provider registry."""
    return _get_provider(request, 'embedding_provider')


def _get_vector_store(request: Request):
    """Get vector store from provider registry."""
    return _get_provider(request, 'vector_store')


def _check_provider_availability(request: Request) -> dict:
    """Check which providers are available in the registry."""
    registry = _get_provider_registry(request)
    return {
        'worker_registry_store': registry.get('worker_registry_store') is not None,
        'worker_profile_content_store': registry.get('worker_profile_content_store') is not None,
        'embedding_provider': registry.get('embedding_provider') is not None,
        'vector_store': registry.get('vector_store') is not None,
    }


def _trust_gateway_enabled() -> bool:
    """True when bcsfuse sits behind the Avernet gateway and trusts its auth.

    The gateway authenticates the caller (route_security user:required) and
    forwards the request; bcsfuse then skips its own shared-Bearer check.
    D2 lightest: bcsfuse does NOT verify X-Avernet-Principal.

    Scope: this bypass is GLOBAL — when the flag is on, EVERY protected bcsfuse
    route guarded by require_oss_auth skips the shared-Bearer check, not only the
    endpoints exposed via the gateway. Operational precondition: when this flag is
    enabled, bcsfuse MUST be reachable ONLY through the gateway (not dual-homed on
    a debug/singlebox/direct port); otherwise all those routes become
    unauthenticated. Match is case-insensitive against the literal "true".
    """
    return os.environ.get("BCSFUSE_TRUST_GATEWAY", "").lower() == "true"


def require_oss_auth(request: Request) -> None:
    """
    Require authentication for protected OSS routes.

    This function checks if a valid Bearer token is provided in the Authorization header.
    It reads the expected token from the auth provider registered in the application context.

    Args:
        request: FastAPI request object.

    Raises:
        HTTPException: 401 if authentication fails.
    """
    if _trust_gateway_enabled():
        return

    # Get auth provider from registry (NOT global)
    registry = _get_provider_registry(request)
    auth_provider = registry.get("auth")

    if auth_provider is None:
        # No auth provider configured - reject by default
        logger.error("[OSS Auth] No auth provider in registry")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": "Authentication required. Auth provider not configured."
            }
        )

    # Validate request
    if not auth_provider.validate_request(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": "Authentication required. Provide valid Bearer token in Authorization header."
            }
        )


def include_oss_business_routes(app) -> None:
    """
    Mount OSS business routes to the FastAPI application.

    This function registers OSS-safe business routes that:
    - Only depend on request.app.state.context
    - Only use provider registry for dependencies
    - Do NOT import from src.interfaces.api.*
    - Do NOT import internal code
    - Do NOT connect to external services during app construction
    - Do NOT read configs/application.yaml
    - Do NOT print token/password/secrets

    Args:
        app: FastAPI application instance with app.state.context set.
    """
    # Create routers for business and admin endpoints
    business_router = APIRouter(prefix="/api/v1")   # External product APIs (for 3rd-party callers)
    mgmt_router = APIRouter(prefix="/v1")            # Management platform APIs (for admin portal)
    admin_router = APIRouter(prefix="/v1/admin")     # Privileged admin APIs (conditional)

    # ========================================
    # R3 Worker/Profile Business Logic Routes (S28B-2B-13)
    # ========================================
    # Mount R3 real business logic routes BEFORE skeletons to avoid shadowing.
    # These replace the 501 skeleton routes for worker/profile management.
    try:
        from src.interfaces.api.worker_profile_parity_routes import include_r3_routes
        include_r3_routes(app)
        logger.info("[OSS Business Routes] R3 worker/profile routes mounted successfully (S28B-2B-13)")
    except Exception as e:
        logger.warning(f"Failed to mount R3 routes: {e}", exc_info=True)

    # ========================================
    # R4 Recommend Business Logic Route (S28B-2B-14)
    # ========================================
    # MOUNT REAL VECTOR-AWARE RECOMMEND ROUTE (NOT R4 keyword-only skeleton)
    # The real recommend_routes.py uses WorkerCandidateRecommendationImpl with vector search
    try:
        from src.interfaces.api.recommend_routes import router as recommend_router
        app.include_router(recommend_router, prefix="/api/v1", tags=["Recommend"])
        logger.info("[OSS Business Routes] ✅ REAL vector-aware recommend route mounted (recommend_routes.py)")
    except Exception as e:
        logger.error(f"❌ Failed to mount real recommend route: {e}", exc_info=True)
        # Fallback to R4 keyword-only route if real route fails
        try:
            from src.interfaces.api.recommend_parity_routes import include_r4_routes
            include_r4_routes(app)
            logger.warning("[OSS Business Routes] ⚠️ Fallback to R4 keyword-only recommend route")
        except Exception as e2:
            logger.error(f"❌ Failed to mount R4 fallback route: {e2}", exc_info=True)

    # ========================================
    # R5 Fusion Business Logic Route (S28B-2B-15)
    # ========================================
    # Mount R5 fusion route BEFORE P0 skeletons to avoid shadowing.
    # This replaces the 501 skeleton route for group fusion.
    try:
        from src.interfaces.api.fusion_parity_routes import include_r5_routes
        include_r5_routes(app)
        logger.info("[OSS Business Routes] R5 fusion route mounted successfully (S28B-2B-15)")
    except Exception as e:
        logger.warning(f"Failed to mount R5 routes: {e}", exc_info=True)

    # ========================================
    # R6 Verify Business Logic Routes (S28B-2B-16)
    # ========================================
    # Mount verify routes at /api/v1 prefix, aligned with OpenAPI contract.
    # Phase 3.2.5: Use verify_routes.py (correct schema) instead of verify_parity_routes.py.
    #
    # DISABLED: verify_parity_routes.py has schema mismatch with OpenAPI.
    # The R6 routes in verify_parity_routes.py use different request/response schema:
    # - verify_parity_routes: { capabilities, filters, verify_options }
    # - OpenAPI contract: { worker_ids, reset_trust_level, dry_run }
    #
    # NEW: Mount verify_routes.py which matches OpenAPI schema exactly.
    # - verify_routes.py: BatchVerifyRequest { worker_ids, reset_trust_level }
    # - verify_routes.py: BatchVerifyAllRequest { reset_trust_level, dry_run }
    # - Returns honest 503 when CapabilityVerifyService is unavailable.
    #
    #try:
    #    from src.interfaces.api.verify_parity_routes import include_r6_routes
    #    include_r6_routes(app)
    #    logger.info("[OSS Business Routes] R6 verify routes mounted successfully (S28B-2B-16)")
    #except Exception as e:
    #    logger.warning(f"Failed to mount R6 routes: {e}", exc_info=True)

    # Mount verify_routes.py with correct OpenAPI schema
    try:
        from src.interfaces.api.verify_routes import router as verify_router
        app.include_router(verify_router, prefix="/v1", tags=["Verify"])
        logger.info("[OPENAPI-P1-VERIFY-TRACE] Verify routes mounted at /v1 (Phase 3.2.5)")
    except Exception as e:
        logger.warning(f"[OPENAPI-P1-VERIFY-TRACE] Failed to mount verify routes: {e}", exc_info=True)

    # ========================================
    # Route Skeletons for Route Parity (S28B-2B-11B)
    # ========================================
    # Mount route skeletons AFTER R3 routes.
    # P0 routes (recommend/fusion/verify) mount at /api/v1
    # R3 worker/profile skeletons removed (replaced by real implementations above)
    try:
        from src.interfaces.api.route_skeletons_oss import include_route_skeletons
        include_route_skeletons(app)
        logger.info("[OSS Business Routes] P0 route skeletons mounted successfully (S28B-2B-11B)")
    except Exception as e:
        logger.warning(f"Failed to mount route skeletons: {e}", exc_info=True)

    # ========================================
    # Provider Status Endpoint
    # ========================================
    # Admin Diagnostic Endpoints (Phase 3.2.4)
    # ========================================
    # Workers Endpoints
    # ========================================
    @mgmt_router.get("/workers", tags=["Workers"])
    async def list_workers_oss(request: Request):
        """List workers (OSS wrapper)."""
        # Require authentication for protected endpoint
        require_oss_auth(request)

        store = _get_worker_registry_store(request)
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": "worker_registry_store provider not available in OSS mode"
                }
            )

        try:
            workers = store.list()
            # Handle both dict and object returns
            items = []
            for w in (workers or [])[:100]:
                if isinstance(w, dict):
                    items.append({
                        "id": w.get("worker_id", ""),
                        "name": w.get("identity", {}).get("name", "unknown"),
                        "type": w.get("worker_type", "bot"),
                    })
                else:
                    items.append({
                        "id": w.id if hasattr(w, 'id') else "",
                        "name": w.identity.name if hasattr(w, 'identity') and w.identity else "unknown",
                        "type": str(w.type) if hasattr(w, 'type') else "bot",
                    })

            return {
                "success": True,
                "items": items,
                "total": len(workers) if workers else 0,
            }
        except Exception as e:
            logger.error(f"[Workers OSS] Failed to list workers: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "LIST_WORKERS_ERROR", "message": str(e)}
            )

    @admin_router.post("/workers", tags=["Workers"])
    async def create_worker_oss(request: Request, create_req: WorkerCreateRequestOSS):
        """Create worker (OSS wrapper for test mode)."""
        # Require authentication for protected endpoint
        require_oss_auth(request)

        store = _get_worker_registry_store(request)
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": "worker_registry_store provider not available in OSS mode"
                }
            )

        try:
            # Import domain models
            from src.domain.models.worker import Worker, WorkerType, WorkerIdentity, WorkerState
            from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
            from src.domain.models.worker_source_info import WorkerSourceType
            from src.domain.models.worker_runtime_state import WorkerRuntimeState
            from src.domain.models.worker import Availability, TrustLevel

            # Create minimal worker object for test mode
            worker = Worker(
                id=create_req.worker_id,
                type=WorkerType.BOT,
                identity=WorkerIdentity(
                    name=create_req.name,
                    handle=create_req.worker_id,  # Use worker_id as handle
                    description=create_req.description or f"OSS test worker: {create_req.name}",
                ),
                responsibilities=["test"],
                domains=[],
                capabilities=[],
                constraints=[],
                skills=[],
                resources=[],
                memory_refs=[],
                state=WorkerState(
                    availability=Availability.PRIVATE,
                    trust_level=TrustLevel.UNVERIFIED,
                ),
                lifecycle_state=WorkerLifecycleState.INACTIVE,
                source_type=WorkerSourceType.API,
                version=1,  # Version must be >= 1
            )

            # Try to create in store
            created = store.create(worker)

            # Handle both dict and object returns (compatibility for different stores)
            if isinstance(created, dict):
                worker_id = created.get("worker_id", create_req.worker_id)
                name = created.get("identity", {}).get("name", create_req.name)
            else:
                worker_id = created.id if hasattr(created, 'id') else create_req.worker_id
                name = created.identity.name if hasattr(created, 'identity') else create_req.name

            return {
                "success": True,
                "worker_id": worker_id,
                "name": name,
            }
        except Exception as e:
            logger.error(f"[Workers OSS] Failed to create worker: {e}", exc_info=True)
            # Check for duplicate worker
            if "DuplicateWorker" in str(type(e).__name__) or "already exists" in str(e).lower():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "WORKER_ALREADY_EXISTS", "message": f"Worker {create_req.worker_id} already exists"}
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "CREATE_WORKER_ERROR", "message": str(e)}
            )

    @mgmt_router.get("/workers/{worker_id}", tags=["Workers"])
    async def get_worker_oss(worker_id: str, request: Request):
        """Get worker by ID (OSS wrapper)."""
        # Require authentication for protected endpoint
        require_oss_auth(request)

        store = _get_worker_registry_store(request)
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": "worker_registry_store provider not available in OSS mode"
                }
            )

        try:
            worker = store.get_by_id(worker_id)
            if worker is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "WORKER_NOT_FOUND", "message": f"Worker {worker_id} not found"}
                )

            # Handle both dict and object returns
            if isinstance(worker, dict):
                worker_dict = worker
            elif hasattr(worker, 'model_dump'):
                worker_dict = worker.model_dump()
            elif hasattr(worker, 'dict'):
                worker_dict = worker.dict()
            else:
                worker_dict = dict(worker)

            # Fetch runtime state from runtime_state_store
            runtime_state_store = _get_runtime_state_store(request)
            if runtime_state_store:
                try:
                    runtime_state_data = runtime_state_store.get_runtime_state(worker_id)
                    if runtime_state_data:
                        # Add runtime_state to worker dict
                        worker_dict["runtime_state"] = runtime_state_data.get("state", "offline")
                        worker_dict["is_online"] = runtime_state_data.get("state", "offline") == "online"
                    else:
                        # No runtime state record = offline
                        worker_dict["runtime_state"] = "offline"
                        worker_dict["is_online"] = False
                except Exception as e:
                    logger.warning(f"[Workers OSS] Failed to get runtime state for {worker_id}: {e}")
                    worker_dict["runtime_state"] = "offline"
                    worker_dict["is_online"] = False
            else:
                worker_dict["runtime_state"] = "unknown"
                worker_dict["is_online"] = False

            # Return flat WorkerResponse (OpenAPI contract)
            logger.info(f"[OSS_WORKER_GET_CONTRACT_RESPONSE] worker_id={worker_id} response_shape=flat worker_class={type(worker).__name__} store_class={type(store).__name__}")
            return _worker_to_contract_response(worker)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[Workers OSS] Failed to get worker {worker_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "GET_WORKER_ERROR", "message": str(e)}
            )

    @business_router.put("/workers/{worker_id}/online", tags=["Workers"])
    async def set_worker_online_oss(worker_id: str, request: Request):
        """Set worker online (OSS wrapper for test mode)."""
        # Require authentication for protected endpoint
        require_oss_auth(request)

        # Get runtime state service
        try:
            runtime_state_service = _get_runtime_state_service(request)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[Workers OSS] Failed to get runtime state service: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": f"Failed to initialize runtime state service: {str(e)}"
                }
            )

        try:
            # Use WorkerRuntimeStateService.set_online() - proper implementation
            updated = runtime_state_service.set_online(worker_id, updated_by="oss_api")

            # Extract runtime state from worker
            if hasattr(updated, 'state') and hasattr(updated.state, 'runtime_state'):
                if hasattr(updated.state.runtime_state, 'value'):
                    runtime_state = str(updated.state.runtime_state.value)
                else:
                    runtime_state = str(updated.state.runtime_state)
            else:
                runtime_state = "online"

            if hasattr(updated, 'lifecycle_state'):
                if hasattr(updated.lifecycle_state, 'value'):
                    lifecycle_state = str(updated.lifecycle_state.value)
                else:
                    lifecycle_state = str(updated.lifecycle_state)
            else:
                lifecycle_state = "active"

            # Sync runtime_state to vector store payloads (Faiss + Qdrant)
            from src.interfaces.api.worker_profile_parity_routes import _sync_runtime_state_to_vector_store
            _sync_runtime_state_to_vector_store(worker_id, "online")

            return {
                "success": True,
                "worker_id": worker_id,
                "status": "online",
                "runtime_state": runtime_state,
                "lifecycle_state": lifecycle_state,
            }
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[Workers OSS] Failed to set worker {worker_id} online: {e}", exc_info=True)
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[Workers OSS] Failed to set worker {worker_id} online: {e}", exc_info=True)

            # Check for specific errors
            if "not found" in error_msg.lower() or "WorkerNotFoundException" in type(e).__name__:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "WORKER_NOT_FOUND", "message": f"Worker {worker_id} not found"}
                )
            elif "DISABLED" in error_msg or "INACTIVE" in error_msg:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": "INVALID_STATE_TRANSITION", "message": error_msg}
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={"code": "SET_WORKER_ONLINE_ERROR", "message": error_msg}
                )

    @business_router.put("/workers/{worker_id}/offline", tags=["Workers"])
    async def set_worker_offline_oss(worker_id: str, request: Request):
        """Set worker offline (OSS wrapper for test mode)."""
        # Require authentication for protected endpoint
        require_oss_auth(request)

        # Get runtime state service
        try:
            runtime_state_service = _get_runtime_state_service(request)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[Workers OSS] Failed to get runtime state service: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": f"Failed to initialize runtime state service: {str(e)}"
                }
            )

        try:
            # Use WorkerRuntimeStateService.set_offline() - proper implementation
            updated = runtime_state_service.set_offline(worker_id, updated_by="oss_api")

            # Extract version
            version = 1
            if hasattr(updated, 'version'):
                version = updated.version
            elif isinstance(updated, dict):
                version = updated.get('version', 1)

            # Return flat RuntimeStateResponse (OpenAPI contract)
            result = {
                "success": True,
                "worker_id": worker_id,
                "runtime_state": _enum_value(updated.state.runtime_state if hasattr(updated, 'state') else 'offline'),
                "lifecycle_state": _enum_value(updated.lifecycle_state if hasattr(updated, 'lifecycle_state') else 'inactive'),
                "version": version,
            }

            state_type = type(updated).__name__ if updated else 'None'
            logger.info(f"[OSS_WORKER_OFFLINE_CONTRACT_RESPONSE] worker_id={worker_id} state_type={state_type} runtime_state={result['runtime_state']} lifecycle_state={result['lifecycle_state']} version={version} audit_log_adapter_class={type(runtime_state_service._audit_log_adapter).__name__ if runtime_state_service._audit_log_adapter else None} result=success")

            # Sync runtime_state to vector store payloads (Faiss + Qdrant)
            from src.interfaces.api.worker_profile_parity_routes import _sync_runtime_state_to_vector_store
            _sync_runtime_state_to_vector_store(worker_id, "offline")

            return result
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[Workers OSS] Failed to set worker {worker_id} offline: {e}", exc_info=True)

            # Check for specific errors
            if "not found" in error_msg.lower() or "WorkerNotFoundException" in type(e).__name__:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "WORKER_NOT_FOUND", "message": f"Worker {worker_id} not found"}
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={"code": "SET_WORKER_OFFLINE_ERROR", "message": error_msg}
                )

    # ===== End OpenAPI P1 Worker Core Routes =====

    # ========================================
    # OpenAPI P1 Worker Config Routes (Phase 3.2.3)
    @admin_router.delete("/workers/{worker_id}", tags=["Workers"])
    async def delete_worker_oss(worker_id: str, request: Request):
        """Delete worker (OSS wrapper for test mode)."""
        # Require authentication for protected endpoint
        require_oss_auth(request)

        store = _get_worker_registry_store(request)
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": "worker_registry_store provider not available in OSS mode"
                }
            )

        try:
            # Check if worker exists first
            worker = store.get_by_id(worker_id)
            if worker is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "WORKER_NOT_FOUND", "message": f"Worker {worker_id} not found"}
                )

            # Delete worker
            store.delete(worker_id)

            return {
                "success": True,
                "worker_id": worker_id,
                "deleted": True,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[Workers OSS] Failed to delete worker {worker_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "DELETE_WORKER_ERROR", "message": str(e)}
            )

    # ========================================
    # Profiles Endpoints
    # ========================================
    @mgmt_router.get("/workers/{worker_id}/profiles", tags=["Profiles"])
    async def list_profiles_oss(worker_id: str, request: Request):
        """List profiles for a worker (OSS wrapper)."""
        # Require authentication for protected endpoint
        require_oss_auth(request)

        store = _get_profile_content_store(request)
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": "worker_profile_content_store provider not available in OSS mode"
                }
            )

        try:
            result = store.list_profiles(worker_id)

            # Handle both list return and object return
            # InMemoryWorkerProfileContentStore returns a list directly
            # Other stores might return an object with .items and .total
            if isinstance(result, list):
                items = result[:100]
                total = len(result)
            else:
                # Object with .items and .total attributes
                items = result.items[:100] if hasattr(result, 'items') else []
                total = result.total if hasattr(result, 'total') else len(items)

            return {
                "success": True,
                "items": [
                    {
                        "worker_id": p.get('worker_id') if isinstance(p, dict) else (p.worker_id if hasattr(p, 'worker_id') else worker_id),
                        "profile_id": p.get('profile_id') if isinstance(p, dict) else (p.profile_id if hasattr(p, 'profile_id') else 'unknown'),
                    }
                    for p in items
                ],
                "total": total,
            }
        except Exception as e:
            logger.error(f"[Profiles OSS] Failed to list profiles for worker {worker_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "LIST_PROFILES_ERROR", "message": str(e)}
            )

    @mgmt_router.put("/workers/{worker_id}/profiles/{profile_id}", tags=["Profiles"])
    async def upsert_profile_oss(worker_id: str, profile_id: str, request: Request, profile_req: ProfileRequest):
        """
        Upsert profile (PUT /api/v1/workers/{worker_id}/profiles/{profile_id}).

        Contract-aligned implementation: all fields optional, full replace/upsert.
        If activate=True, updates active profile binding.
        """
        # Require authentication for protected endpoint
        require_oss_auth(request)

        # Route marker for Phase 3.4.1H diagnosis
        logger.info(
            f"[PROFILE_PUT_ROUTE_OWNER] handler=oss_business_routes.upsert_profile_oss "
            f"worker_id={worker_id} profile_id={profile_id}"
        )

        profile_store = _get_profile_content_store(request)
        if profile_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": "worker_profile_content_store provider not available in OSS mode"
                }
            )

        try:
            # Check if profile already exists
            existing_profile = profile_store.get_profile(worker_id, profile_id)
            is_create = existing_profile is None

            # Get current timestamp
            now = datetime.utcnow().isoformat()

            # Build profile data with all fields from ProfileRequest
            profile_data = {
                "worker_id": worker_id,
                "profile_id": profile_id,
                "display_name": profile_req.display_name,
                "soul_md": profile_req.soul_md,
                "agents_md": profile_req.agents_md,
                "tools_md": profile_req.tools_md,
                "boot_md": profile_req.boot_md,
                "heartbeat_md": profile_req.heartbeat_md,
                "contents": profile_req.contents or {},
                "skill_sets": [s.model_dump() if hasattr(s, 'model_dump') else dict(s) for s in profile_req.skill_sets],
                "metadata": profile_req.metadata or {},
                "content_type": "api",
                "is_active": False,  # Will be updated if activate=True
                "version": 1,
                "created_at": now,
                "updated_at": now,
            }

            # If updating existing profile, preserve created_at and increment version
            if existing_profile:
                if isinstance(existing_profile, dict):
                    profile_data["created_at"] = existing_profile.get("created_at", now)
                    profile_data["version"] = existing_profile.get("version", 1) + 1
                elif hasattr(existing_profile, "created_at"):
                    profile_data["created_at"] = existing_profile.created_at.isoformat() if hasattr(existing_profile.created_at, 'isoformat') else str(existing_profile.created_at)
                    profile_data["version"] = getattr(existing_profile, "version", 1) + 1

            # Store profile
            profile_store.upsert_profile(worker_id, profile_id, profile_data)

            logger.info(
                f"[PROFILE_UPSERT_REQUEST] worker_id={worker_id}, profile_id={profile_id}, "
                f"activate={profile_req.activate}, "
                f"contents_keys={list(profile_req.contents.keys()) if profile_req.contents else []}, "
                f"metadata_keys={list(profile_req.metadata.keys()) if profile_req.metadata else []}, "
                f"skill_sets_count={len(profile_req.skill_sets)}"
            )

            # Handle activation
            if profile_req.activate:
                profile_store.activate_profile(worker_id, profile_id)
                profile_data["is_active"] = True

                # Sync profile binding
                binding_store = _get_profile_binding_store(request)
                if binding_store:
                    profile_key = f"{worker_id}:{profile_id}"
                    from src.domain.models.worker_source_info import WorkerSourceType
                    binding_store.bind_profile(
                        worker_id=worker_id,
                        profile_key=profile_key,
                        source_type=WorkerSourceType.API,
                    )
                    logger.info(
                        f"[PROFILE_ACTIVATE_SIDE_EFFECT] worker_id={worker_id}, "
                        f"profile_id={profile_id}, profile_key={profile_key}, "
                        f"binding_updated=True, result=success"
                    )

                    # Sync worker active_profile_key (aligned with internal profile_routes.py)
                    try:
                        from src.interfaces.api.dependencies.worker_dependencies import _get_registry_store as _get_reg_store
                        reg_store = _get_reg_store()
                        worker_obj = reg_store.get_by_id(worker_id) if reg_store else None
                        if worker_obj:
                            if isinstance(worker_obj, dict):
                                worker_obj['active_profile_key'] = profile_key
                                from src.domain.models.worker import Worker as WorkerModel
                                reg_store.update(WorkerModel.model_validate(worker_obj))
                            else:
                                if hasattr(worker_obj, 'model_dump'):
                                    w_dict = worker_obj.model_dump()
                                elif hasattr(worker_obj, 'dict'):
                                    w_dict = worker_obj.dict()
                                else:
                                    w_dict = dict(worker_obj)
                                w_dict['active_profile_key'] = profile_key
                                from src.domain.models.worker import Worker as WorkerModel
                                reg_store.update(WorkerModel.model_validate(w_dict))
                            logger.info(
                                f"[PROFILE_ACTIVATE_SIDE_EFFECT] worker_id={worker_id}, "
                                f"active_profile_key={profile_key}, updated=True"
                            )
                    except Exception as e:
                        logger.warning(f"[PROFILE_ACTIVATE_SIDE_EFFECT] Failed to update active_profile_key: {e}")

            # Eager indexing if enabled
            # Phase 3.4.1H Fix: Use _build_vector_index_for_worker instead of missing worker_index_sync_adapter
            enable_profile_embedding = os.getenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false").lower() == "true"
            enable_eager = os.getenv("ENABLE_EAGER_INDEXING", "false").lower() == "true"

            if enable_profile_embedding or enable_eager:
                import time
                start = time.time()
                try:
                    # Import _build_vector_index_for_worker from fusion_dependencies
                    from src.interfaces.api.dependencies.fusion_dependencies import _build_vector_index_for_worker

                    profile_key = f"{worker_id}:{profile_id}"
                    logger.info(
                        f"[PROFILE_EAGER_INDEX_TRIGGER] worker_id={worker_id}, "
                        f"profile_id={profile_id}, profile_key={profile_key}, "
                        f"reason=profile_upsert activate={profile_req.activate} "
                        f"enable_profile_embedding_index={enable_profile_embedding} "
                        f"enable_eager_indexing={enable_eager}"
                    )

                    # Build vector index for this worker
                    success = _build_vector_index_for_worker(worker_id)
                    elapsed_ms = int((time.time() - start) * 1000)

                    if success:
                        logger.info(
                            f"[PROFILE_EAGER_INDEX_RESULT] worker_id={worker_id}, "
                            f"profile_id={profile_id}, profile_key={profile_key}, "
                            f"result=success, elapsed_ms={elapsed_ms}"
                        )
                    else:
                        logger.warning(
                            f"[PROFILE_EAGER_INDEX_RESULT] worker_id={worker_id}, "
                            f"profile_id={profile_id}, profile_key={profile_key}, "
                            f"result=failed, elapsed_ms={elapsed_ms}, reason=index_build_returned_false"
                        )
                except Exception as e:
                    elapsed_ms = int((time.time() - start) * 1000)
                    logger.warning(
                        f"[PROFILE_EAGER_INDEX_RESULT] worker_id={worker_id} "
                        f"profile_id={profile_id} result=error elapsed_ms={elapsed_ms} error={e}"
                    )
            else:
                logger.debug(
                    f"[PROFILE_EAGER_INDEX_SKIP] worker_id={worker_id}, "
                    f"profile_id={profile_id}, "
                    f"enable_profile_embedding_index={enable_profile_embedding}, "
                    f"enable_eager_indexing={enable_eager}"
                )

            # Return flat ProfileResponse
            return ProfileResponse(
                worker_id=worker_id,
                profile_id=profile_id,
                display_name=profile_data.get("display_name"),
                soul_md=profile_data.get("soul_md"),
                agents_md=profile_data.get("agents_md"),
                tools_md=profile_data.get("tools_md"),
                boot_md=profile_data.get("boot_md"),
                heartbeat_md=profile_data.get("heartbeat_md"),
                contents=profile_data.get("contents", {}),
                skill_sets=profile_data.get("skill_sets", []),
                metadata=profile_data.get("metadata", {}),
                content_type=profile_data.get("content_type", "api"),
                is_active=profile_data.get("is_active", False),
                version=profile_data.get("version", 1),
                created_at=profile_data.get("created_at"),
                updated_at=profile_data.get("updated_at"),
            )
        except Exception as e:
            logger.error(f"[Profiles OSS] Failed to upsert profile {profile_id} for worker {worker_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "UPSERT_PROFILE_ERROR", "message": str(e)}
            )

    @mgmt_router.get("/workers/{worker_id}/profiles/active", tags=["Profiles"])
    async def get_active_profile_oss(worker_id: str, request: Request):
        """Get active profile (OSS wrapper). Must NOT 404."""
        # Require authentication for protected endpoint
        require_oss_auth(request)

        profile_store = _get_profile_content_store(request)
        if profile_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": "worker_profile_content_store provider not available"
                }
            )

        try:
            content = None
            resolved_profile_id = None
            binding_found = False
            active_profile_key = None

            # 1. Check worker.active_profile_key
            worker_store = _get_worker_registry_store(request)
            if worker_store:
                try:
                    worker = worker_store.get_by_id(worker_id)
                    if worker:
                        if isinstance(worker, dict):
                            active_profile_key = worker.get('active_profile_key')
                        else:
                            active_profile_key = getattr(worker, 'active_profile_key', None)

                        if active_profile_key and ':' in active_profile_key:
                            # Format: "worker_id:profile_id"
                            parts = active_profile_key.split(':', 1)
                            if len(parts) == 2:
                                resolved_profile_id = parts[1]
                except Exception as e:
                    logger.warning(f"[Profiles OSS] Failed to check worker active_profile_key: {e}")

            # 2. Check profile binding store
            if not resolved_profile_id:
                binding_store = _get_profile_binding_store(request)
                if binding_store:
                    try:
                        active_binding = binding_store.get_active_profile_binding(worker_id)
                        if active_binding:
                            binding_found = True
                            if isinstance(active_binding, dict):
                                resolved_profile_id = active_binding.get('profile_id')
                            else:
                                resolved_profile_id = getattr(active_binding, 'profile_id', None)
                    except Exception as e:
                        logger.warning(f"[Profiles OSS] Failed to check active binding: {e}")

            # 3. Try to get the profile content
            if resolved_profile_id:
                try:
                    content = profile_store.get_profile(worker_id, resolved_profile_id)
                except Exception as e:
                    logger.warning(f"[Profiles OSS] Failed to get profile {resolved_profile_id}: {e}")

            # 4. Fallback to default profile if still no content
            if content is None:
                try:
                    content = profile_store.get_profile(worker_id, 'default')
                    if content:
                        resolved_profile_id = 'default'
                except Exception as e:
                    logger.warning(f"[Profiles OSS] Failed to get default profile: {e}")

            logger.info(f"[OSS_ACTIVE_PROFILE_RESOLUTION] worker_id={worker_id} active_profile_key={active_profile_key} resolved_profile_id={resolved_profile_id} binding_found={binding_found} content_found={content is not None} result={'success' if content else 'not_found'}")

            # Return valid ProfileResponse even if no active profile (root-compatible default)
            if content is None:
                # Return a minimal valid profile response (root-compatible behavior)
                from datetime import datetime
                now = datetime.utcnow().isoformat()
                return {
                    "worker_id": worker_id,
                    "profile_id": "default",
                    "display_name": None,
                    "soul_md": None,
                    "agents_md": None,
                    "tools_md": None,
                    "boot_md": None,
                    "heartbeat_md": None,
                    "contents": {},
                    "skill_sets": [],
                    "metadata": {"message": "No active profile found"},
                    "content_type": "api",
                    "is_active": False,
                    "version": 1,
                    "quality_score": None,
                    "quality_issues": ["No active profile found"],
                    "created_at": now,
                    "updated_at": now,
                }

            # Return flat ProfileResponse
            return _profile_to_contract_response(content, is_active=True)
        except Exception as e:
            logger.error(f"[Profiles OSS] Failed to get active profile for {worker_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "GET_ACTIVE_PROFILE_ERROR", "message": str(e)}
            )

    @mgmt_router.get("/workers/{worker_id}/profiles/{profile_id}", tags=["Profiles"])
    async def get_profile_oss(worker_id: str, profile_id: str, request: Request):
        """Get specific profile (OSS wrapper)."""
        # Require authentication for protected endpoint
        require_oss_auth(request)

        profile_store = _get_profile_content_store(request)
        if profile_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": "worker_profile_content_store provider not available in OSS mode"
                }
            )

        try:
            profile = profile_store.get_profile(worker_id, profile_id)
            if profile is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "PROFILE_NOT_FOUND", "message": f"Profile {profile_id} not found for worker {worker_id}"}
                )

            # Determine is_active from multiple sources
            is_active = False
            active_source = "default"

            # 1. Check profile binding store for active binding
            binding_store = _get_profile_binding_store(request)
            if binding_store:
                try:
                    active_binding = binding_store.get_active_profile_binding(worker_id)
                    if active_binding:
                        # binding format: {"profile_id": "...", ...}
                        if isinstance(active_binding, dict):
                            active_profile_id = active_binding.get('profile_id')
                        else:
                            active_profile_id = getattr(active_binding, 'profile_id', None)

                        if active_profile_id == profile_id:
                            is_active = True
                            active_source = "binding_store"
                except Exception as e:
                    logger.warning(f"[Profiles OSS] Failed to check active binding: {e}")

            # 2. Check worker.active_profile_key if binding store not conclusive
            if not is_active and active_source == "default":
                worker_store = _get_worker_registry_store(request)
                if worker_store:
                    try:
                        worker = worker_store.get_by_id(worker_id)
                        if worker:
                            active_profile_key = None
                            if isinstance(worker, dict):
                                active_profile_key = worker.get('active_profile_key')
                            else:
                                active_profile_key = getattr(worker, 'active_profile_key', None)

                            if active_profile_key and active_profile_key == f"{worker_id}:{profile_id}":
                                is_active = True
                                active_source = "worker_active_profile_key"
                    except Exception as e:
                        logger.warning(f"[Profiles OSS] Failed to check worker active_profile_key: {e}")

            # 3. Fallback to content.is_active if available
            if not is_active and active_source == "default":
                if isinstance(profile, dict):
                    content_is_active = profile.get('is_active')
                else:
                    content_is_active = getattr(profile, 'is_active', None)

                if isinstance(content_is_active, bool):
                    is_active = content_is_active
                    active_source = "content_is_active"

            # Return flat ProfileResponse (OpenAPI contract)
            logger.info(f"[OSS_PROFILE_GET_CONTRACT_RESPONSE] worker_id={worker_id} profile_id={profile_id} response_shape=flat is_active={is_active} active_source={active_source} profile_store_class={type(profile_store).__name__}")
            return _profile_to_contract_response(profile, is_active=is_active)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[Profiles OSS] Failed to get profile {profile_id} for worker {worker_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "GET_PROFILE_ERROR", "message": str(e)}
            )

    @admin_router.delete("/workers/{worker_id}/profiles/{profile_id}", tags=["Profiles"])
    async def delete_profile_oss(worker_id: str, profile_id: str, request: Request):
        """Delete profile (OSS wrapper for test mode)."""
        # Require authentication for protected endpoint
        require_oss_auth(request)

        profile_store = _get_profile_content_store(request)
        vector_store = _get_vector_store(request)

        if profile_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": "worker_profile_content_store provider not available in OSS mode"
                }
            )

        try:
            # Check if profile exists
            profile = profile_store.get_profile(worker_id, profile_id)
            if profile is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "PROFILE_NOT_FOUND", "message": f"Profile {profile_id} not found for worker {worker_id}"}
                )

            # Delete from profile store
            profile_store.delete_profile(worker_id, profile_id)

            # Delete from vector store if available
            vectors_deleted = 0
            if vector_store is not None:
                try:
                    vectors_deleted = vector_store.delete_by_profile(worker_id, profile_id)
                except Exception as vec_err:
                    logger.warning(f"[Profiles OSS] Failed to delete vectors for profile {profile_id}: {vec_err}")

            return {
                "success": True,
                "worker_id": worker_id,
                "profile_id": profile_id,
                "deleted": True,
                "vectors_deleted": vectors_deleted,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[Profiles OSS] Failed to delete profile {profile_id} for worker {worker_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "DELETE_PROFILE_ERROR", "message": str(e)}
            )

    # ========================================
    # Search Endpoints
    # ========================================
    @business_router.post("/search", tags=["Search"])
    async def search_oss(request: Request, search_req: SearchRequestOSS):
        """Search profiles — aligned with internal profile_routes.search_profiles.

        Uses match_service.match() for fragment-level search, aggregation and
        optional reranking, then returns the same response shape as the
        internal ``SearchResponse`` model.
        """
        import time

        require_oss_auth(request)
        start_time = time.time()

        try:
            logger.info(
                "[Search OSS] Query: %s, top_k: %s",
                search_req.query, search_req.top_k,
            )

            # --- Obtain services via fusion_dependencies ---
            from src.interfaces.api.dependencies.fusion_dependencies import (
                _get_vector_match_service,
                _get_embedding_generator,
            )

            match_service = _get_vector_match_service()
            embedding_gen = _get_embedding_generator()

            if not match_service or not embedding_gen:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "PROVIDER_NOT_AVAILABLE",
                        "message": "match_service or embedding_generator not available",
                    },
                )

            # --- Generate query embedding ---
            query_embedding = embedding_gen.embed(search_req.query)
            logger.info(
                "[Search OSS] Embedding generated, dimension=%d",
                len(query_embedding) if query_embedding else 0,
            )

            # --- Visibility filters (aligned with internal defaults) ---
            default_visibility = {
                "availability": ["protected", "public"],
            }
            user_filters = dict(getattr(search_req, "filters", None) or {})
            merged_filters = dict(user_filters)
            if "availability" not in merged_filters:
                merged_filters["availability"] = default_visibility["availability"]
            if "runtime_state" not in merged_filters:
                # OSS keeps runtime_state default for backwards compat
                merged_filters["runtime_state"] = ["online"]

            logger.info(
                "[Search OSS] filters: user=%s, merged=%s",
                user_filters, merged_filters,
            )

            # --- Build runtime_config (aligned with internal) ---
            runtime_config = {}
            reranker_model = os.getenv("RERANKER_MODEL", "bge-reranker-v2-m3")
            if reranker_model:
                runtime_config["reranker_model"] = reranker_model

            # --- Execute match via service (fragment-aware) ---
            results = match_service.match(
                query_embedding=query_embedding,
                top_k=search_req.top_k,
                query=search_req.query,
                mode=getattr(search_req, "mode", "auto"),
                min_score=getattr(search_req, "min_score", 0.0),
                filters=merged_filters,
                runtime_config=runtime_config if runtime_config else None,
            )
            logger.info(
                "[Search OSS] match returned %d results", len(results),
            )

            # --- Format results (aligned with internal SearchResultItem) ---
            formatted_results = []
            for result in results:
                parts = result.profile_key.split(":")

                # Extract fragment_matches (aligned with internal)
                fragment_matches = []
                if hasattr(result, "fragment_matches") and result.fragment_matches:
                    for fm in result.fragment_matches[:5]:
                        fragment_matches.append({
                            "fragment_type": getattr(fm, "fragment_type", "unknown"),
                            "score": getattr(fm, "score", 0.0),
                            "weighted_score": getattr(fm, "weighted_score", 0.0),
                            "content_preview": getattr(fm, "content_preview", "")[:100],
                            "content": getattr(fm, "content", "") or getattr(fm, "content_preview", ""),
                        })

                # Extract short_profile from metadata
                short_profile = ""
                if hasattr(result, "metadata") and result.metadata:
                    short_profile = getattr(result.metadata, "short_profile", "")
                    if not short_profile and hasattr(result.metadata, "payload"):
                        short_profile = result.metadata.payload.get("short_profile", "")

                # worker_id may contain colons, so profile_id is the last part
                formatted_results.append({
                    "profile_key": result.profile_key,
                    "worker_id": ":".join(parts[:-1]) if len(parts) > 1 else result.profile_key,
                    "profile_id": parts[-1] if len(parts) > 1 else "default",
                    "score": result.score,
                    "aggregated_score": getattr(result, "aggregated_score", None),
                    "fragment_matches": fragment_matches,
                    "short_profile": short_profile,
                    "source": "vector",
                })

            # --- Build config_applied (aligned with internal) ---
            config_applied = dict(runtime_config)

            elapsed_ms = (time.time() - start_time) * 1000

            return {
                "query": search_req.query,
                "top_k": search_req.top_k,
                "mode": getattr(search_req, "mode", "auto"),
                "results_count": len(formatted_results),
                "results": formatted_results,
                "timing_ms": round(elapsed_ms, 2),
                "config_applied": config_applied,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error("[Search OSS] Search failed: %s", e, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "SEARCH_ERROR", "message": str(e)},
            )

# ========================================
    # Include routers in app
    # ========================================
    app.include_router(business_router)
    app.include_router(mgmt_router)

    # Conditionally mount admin routes
    if os.getenv("BCSFUSE_EXPOSE_ADMIN", "false").lower() == "true":
        app.include_router(admin_router)
        logger.info("[OSS Business Routes] Admin routes mounted at /v1/admin")
    else:
        logger.info("[OSS Business Routes] Admin routes NOT mounted (set BCSFUSE_EXPOSE_ADMIN=true to enable)")

    logger.info("[OSS Business Routes] OSS business routes mounted successfully")

