"""
OSS Business Routes Module

Provides OSS-safe business routes that avoid importing from app.adapters.http.*
This module is placed in src/app/bootstrap/ to avoid the import chain triggered by
app.adapters.http.__init__.py.

IMPORTANT: DO NOT import from app.adapters.http.* as it triggers:
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
import logging
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException, status

from app.adapters.http.schemas.worker_management_schemas import (
    ProfileRequest,
    ProfileResponse,
)
from app.application.agent_search_service import (
    AgentSearchCommand,
    AgentSearchService,
)
from app.bootstrap.index_write_guard import ensure_writable_index
from app.bootstrap.oss_business_support import (
    SearchRequestOSS,
    _get_profile_binding_store,
    _get_profile_content_store,
    _get_vector_store,
    _get_worker_registry_store,
    _profile_to_contract_response,
    _trust_gateway_enabled,  # compatibility re-export
    require_oss_auth,
)
from app.bootstrap.oss_worker_routes import include_oss_worker_routes

logger = logging.getLogger(__name__)

__all__ = [
    "SearchRequestOSS",
    "_trust_gateway_enabled",
    "include_oss_business_routes",
    "require_oss_auth",
]

def include_oss_business_routes(app) -> None:
    """
    Mount OSS business routes to the FastAPI application.

    This function registers OSS-safe business routes that:
    - Only depend on request.app.state.context
    - Only use provider registry for dependencies
    - Do NOT import from app.adapters.http.*
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
        from app.adapters.http.worker_profile_parity_routes import include_r3_routes
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
        from app.adapters.http.recommend_routes import router as recommend_router
        app.include_router(recommend_router, prefix="/api/v1", tags=["Recommend"])
        logger.info("[OSS Business Routes] ✅ REAL vector-aware recommend route mounted (recommend_routes.py)")
    except Exception as e:
        logger.error(f"❌ Failed to mount real recommend route: {e}", exc_info=True)
        # Fallback to R4 keyword-only route if real route fails
        try:
            from app.adapters.http.recommend_parity_routes import include_r4_routes
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
        from app.adapters.http.fusion_parity_routes import include_r5_routes
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
    #    from app.adapters.http.verify_parity_routes import include_r6_routes
    #    include_r6_routes(app)
    #    logger.info("[OSS Business Routes] R6 verify routes mounted successfully (S28B-2B-16)")
    #except Exception as e:
    #    logger.warning(f"Failed to mount R6 routes: {e}", exc_info=True)

    # Mount verify_routes.py with correct OpenAPI schema
    try:
        from app.adapters.http.verify_routes import router as verify_router
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
        from app.adapters.http.route_skeletons_oss import include_route_skeletons
        include_route_skeletons(app)
        logger.info("[OSS Business Routes] P0 route skeletons mounted successfully (S28B-2B-11B)")
    except Exception as e:
        logger.warning(f"Failed to mount route skeletons: {e}", exc_info=True)

    include_oss_worker_routes(
        business_router=business_router,
        mgmt_router=mgmt_router,
        admin_router=admin_router,
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
        ensure_writable_index(request)

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
                    from app.domain.models.worker_source_info import WorkerSourceType
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
                        from app.adapters.http.dependencies.worker_dependencies import _get_registry_store as _get_reg_store
                        reg_store = _get_reg_store()
                        worker_obj = reg_store.get_by_id(worker_id) if reg_store else None
                        if worker_obj:
                            if isinstance(worker_obj, dict):
                                worker_obj['active_profile_key'] = profile_key
                                from app.domain.models.worker import Worker as WorkerModel
                                reg_store.update(WorkerModel.model_validate(worker_obj))
                            else:
                                if hasattr(worker_obj, 'model_dump'):
                                    w_dict = worker_obj.model_dump()
                                elif hasattr(worker_obj, 'dict'):
                                    w_dict = worker_obj.dict()
                                else:
                                    w_dict = dict(worker_obj)
                                w_dict['active_profile_key'] = profile_key
                                from app.domain.models.worker import Worker as WorkerModel
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
                    from app.adapters.http.dependencies.fusion_dependencies import _build_vector_index_for_worker

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

        response: dict | None = None
        try:
            logger.info(
                "[Search OSS] Request query_length=%s, top_k=%s",
                len(search_req.query), search_req.top_k,
            )

            from app.adapters.http.search_response_cache import (
                store_v1_search_response,
            )
            from app.adapters.http.search_threadpool import (
                run_search_in_threadpool,
            )
            from app.adapters.http.dependencies.fusion_dependencies import (
                _get_vector_match_service,
                _get_embedding_generator,
            )
            from app.application.services.search_index_state import (
                search_index_state_metadata,
            )
            app_state = getattr(getattr(request, "app", None), "state", None)
            context = getattr(app_state, "context", None)
            registry = getattr(context, "registry", None)
            match_service = _get_vector_match_service()
            embedding_gen = _get_embedding_generator()
            if not match_service:
                raise RuntimeError("match service not available")

            # --- Execute the unified application search service ---
            # Query normalization, default filters, bounded execution, and
            # embedding degradation are application policy, not route policy.
            result_cache = getattr(
                app_state, "agent_search_result_cache", None
            )
            agent_search_service = AgentSearchService(
                embedding_generator=embedding_gen,
                vector_match_service=match_service,
                result_cache=result_cache,
                resilience_cache=getattr(
                    app_state, "agent_search_resilience_cache", None
                ),
                local_snapshot=getattr(
                    app_state, "local_retrieval_snapshot", None
                ),
                execution_budget=getattr(
                    app_state, "agent_search_execution_budget", None
                ),
                offload=run_search_in_threadpool,
                index_state_provider=(
                    lambda: search_index_state_metadata(registry)
                    if registry is not None
                    else None
                ),
            )
            outcome = await agent_search_service.search(
                AgentSearchCommand(
                    query=search_req.query,
                    top_k=search_req.top_k,
                    mode=getattr(search_req, "mode", "auto"),
                    min_score=getattr(search_req, "min_score", 0.0),
                    filters=getattr(search_req, "filters", None),
                    reranker_model=os.getenv("RERANKER_MODEL"),
                )
            )
            logger.info(
                "[Search OSS] match returned %d results", len(outcome.results),
            )
            elapsed_ms = (time.time() - start_time) * 1000
            from app.adapters.http.search_response_projection import (
                build_v1_search_response,
            )

            response = build_v1_search_response(
                search_req,
                outcome,
                elapsed_ms=elapsed_ms,
            )

            store_v1_search_response(
                result_cache=result_cache,
                request=search_req,
                response=response,
                metadata=outcome.metadata,
            )
            return response
        except Exception as e:
            if response is not None:
                return response
            logger.error("[Search OSS] Search failed: %s", type(e).__name__)
            from app.adapters.http.search_response_projection import (
                build_final_v1_search_response,
            )

            return build_final_v1_search_response(
                search_req,
                elapsed_ms=(time.time() - start_time) * 1000,
                error_type=type(e).__name__,
                local_snapshot=getattr(
                    getattr(getattr(request, "app", None), "state", None),
                    "local_retrieval_snapshot",
                    None,
                ),
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

