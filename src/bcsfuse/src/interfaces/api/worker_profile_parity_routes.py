"""
R3 Worker/Profile Business Logic Routes

Implements R3 worker/profile canonical routes with public-safe implementations.
These routes replace the 501 skeleton routes for worker/profile management.

S28B-2B-13: Worker/Profile Business Logic Implementation

Routes implemented:
- P1 Important: 11 worker management routes
- P2 Useful: 5 profile management routes

All routes use existing public-safe stores (SQLite, InMemory) and do NOT depend on internal providers.
"""

import os
import logging
from datetime import datetime
from typing import Optional, Any

from fastapi import APIRouter, Request, HTTPException, status
from fastapi.responses import JSONResponse

from src.interfaces.api.schemas.worker_management_schemas import (
    WorkerBatchQueryRequest,
    WorkerBatchQueryResponse,
    WorkerSyncRequest,
    WorkerSyncResponse,
    WorkerAvailabilityUpdate,
    WorkerAvailabilityResponse,
    WorkerTrustLevelUpdate,
    WorkerTrustLevelResponse,
    WorkerPatchRequest,
    WorkerPatchResponse,
    WorkerConfigResponse,
    WorkerConfigUpdate,
    WorkerConfigBatchUpdate,
    WorkerConfigBatchResponse,
    WorkersBySourceResponse,
    WorkerProfileQualityResponse,
    Availability,
    TrustLevel,
    ProfilePatchRequest as ContractProfilePatchRequest,
    ProfileResponse,
)
from src.interfaces.api.schemas.profile_management_schemas import (
    ProfileSearchRequest,
    ProfileSearchResponse,
    ProfileSearchResult,
    ActiveProfilesResponse,
    ActiveProfileItem,
    ProfileQualityResponse,
    ProfileQualityScore,
    ProfileAnalyzeRequest,
    ProfileAnalyzeResponse,
    ProfileCapabilityAnalysis,
    ProfilePatchResponse,
    ProfileActivateResponse,
    ActivateResponse,
)

logger = logging.getLogger(__name__)

# Routers for R3 worker/profile routes
# api_router: External product APIs — /api/v1 (always exposed, for 3rd-party callers like BCS)
# mgmt_router: Management platform APIs — /v1 (always exposed, for admin portal)
# admin_router: Privileged admin APIs — /v1/admin (conditionally exposed)
# compat_router: Backward-compatible routes — /v1 (for existing callers like BCS, deprecated)
api_router = APIRouter()
mgmt_router = APIRouter()
admin_router = APIRouter()
compat_router = APIRouter()


# =============================================================================
# Dependency Injection Helpers
# =============================================================================

def _get_worker_registry_store(request: Request):
    """Get worker registry store from provider registry."""
    return request.app.state.context.registry.get('worker_registry_store')


def _get_profile_content_store(request: Request):
    """Get profile content store from provider registry."""
    return request.app.state.context.registry.get('worker_profile_content_store')


def _get_profile_binding_store(request: Request):
    """Get profile binding store from provider registry."""
    return request.app.state.context.registry.get('worker_profile_binding_store')


def _get_runtime_state_store(request: Request):
    """Get runtime state store from provider registry."""
    return request.app.state.context.registry.get('worker_runtime_state_store')


def _require_auth(request: Request) -> None:
    """Require authentication for protected endpoints."""
    from src.bootstrap.oss_business_routes import require_oss_auth
    require_oss_auth(request)


# =============================================================================
# P1 Important Routes - Worker Management (11 routes)
# =============================================================================

@mgmt_router.post(
    "/workers/batch",
    summary="Batch query workers",
    description="Query multiple workers by their IDs.",
    response_model=WorkerBatchQueryResponse,
    tags=["Workers"],
)
async def batch_query_workers(request: Request, req: WorkerBatchQueryRequest):
    """P1: Batch query workers by IDs."""
    _require_auth(request)

    store = _get_worker_registry_store(request)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_registry_store provider not available"
            }
        )

    try:
        # Use per-worker get_by_id to avoid missing get_by_ids on some store implementations
        workers_dict = {}
        for wid in req.worker_ids:
            try:
                w = store.get_by_id(wid)
                if w:
                    workers_dict[wid] = w
            except Exception as e:
                logger.warning(f"[Workers R3] Failed to get worker {wid}: {e}")

        workers = []
        not_found_ids = []

        for worker_id in req.worker_ids:
            if worker_id in workers_dict:
                worker = workers_dict[worker_id]
                # Convert Worker object to dict
                if hasattr(worker, 'model_dump'):
                    worker_dict = worker.model_dump()
                elif hasattr(worker, 'dict'):
                    worker_dict = worker.dict()
                else:
                    worker_dict = dict(worker)
                workers.append(worker_dict)
            else:
                not_found_ids.append(worker_id)

        return WorkerBatchQueryResponse(
            workers=workers,
            not_found_ids=not_found_ids,
            total=len(workers),
        )
    except Exception as e:
        logger.error(f"[Workers R3] Failed to batch query workers: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "BATCH_QUERY_WORKERS_ERROR", "message": str(e)}
        )


@api_router.post(
    "/workers/{worker_id}/sync",
    summary="Sync worker",
    description="Atomic sync: create + online + profile activation.",
    response_model=WorkerSyncResponse,
    tags=["Workers"],
)
async def sync_worker(worker_id: str, request: Request, req: WorkerSyncRequest):
    """
    P1: Sync worker (create + online + activate profile).

    Atomic sync operation aligned with root_original contract:
    - Create or update worker
    - Set runtime_state to online
    - Upsert and activate profile
    - Return canonical response schema
    """
    _require_auth(request)

    worker_store = _get_worker_registry_store(request)
    profile_store = _get_profile_content_store(request)
    runtime_state_store = _get_runtime_state_store(request)

    # Debug logging for provider resolution
    logger.info(
        f"[SYNC_PROVIDER_STATUS] worker_id={worker_id}, "
        f"worker_store={'SET' if worker_store else 'NONE'}, "
        f"profile_store={'SET' if profile_store else 'NONE'}, "
        f"runtime_state_store={'SET' if runtime_state_store else 'NONE'}"
    )

    if worker_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_registry_store provider not available"
            }
        )

    # Track created flag for response
    created = False
    worker_created = False  # For compensation logic
    profile_activated = False
    profile_id = req.profile.profile_id  # Use profile_id from request (root_original contract)
    effective_runtime_state = req.runtime_state or "online"  # 传入则用传入值，否则默认 online

    try:
        from src.domain.models.worker import Worker, WorkerType, WorkerIdentity, WorkerState
        from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
        from src.domain.models.worker_source_info import WorkerSourceType

        # Step 1: Create or update worker
        existing = worker_store.get_by_id(worker_id)

        # Phase 2.6.5: Map availability string to enum
        availability_map = {
            "private": Availability.PRIVATE,
            "protected": Availability.PROTECTED,
            "public": Availability.PUBLIC,
        }
        availability = availability_map.get(req.availability, Availability.PROTECTED)
        logger.info(f"[SYNC-AVAILABILITY-TRACE] worker_id={worker_id}, request_availability={req.availability}, mapped_availability={availability.value}")

        if existing is None:
            # Create new worker
            # Determine active_profile_key (default: worker_id:default)
            active_profile_key = req.profile_key or f"{worker_id}:{profile_id}"

            worker = Worker(
                id=worker_id,
                type=WorkerType.BOT,
                identity=WorkerIdentity(
                    name=req.name,
                    handle=f"@{worker_id}",  # Generate handle from worker_id (no identity_handle in SyncWorkerRequest)
                    description=req.description,
                    title=None,  # No identity_title in SyncWorkerRequest
                ),
                responsibilities=req.responsibilities if hasattr(req, 'responsibilities') else [],
                domains=req.domains,
                capabilities=[],
                skills=[],
                resources=[],
                state=WorkerState(
                    availability=availability,  # Phase 2.6.5: Use availability from request
                    trust_level=TrustLevel.UNVERIFIED,  # Default to standard for sync
                ),
                lifecycle_state=WorkerLifecycleState.INACTIVE,
                source_type=WorkerSourceType.API,
                external_id=None,  # No external_id in SyncWorkerRequest
                version=1,
                active_profile_key=active_profile_key,  # Set active profile key
            )

            # No metadata field in SyncWorkerRequest

            worker_store.create(worker)
            created = True
            worker_created = True  # Mark for compensation
            logger.info(f"[Workers R3 Sync] Worker created: {worker_id} with active_profile_key={active_profile_key}")
        else:
            # Update existing worker
            worker_dict = existing.model_dump() if hasattr(existing, 'model_dump') else existing.dict()
            worker_dict["identity"]["name"] = req.name
            if req.description is not None:
                worker_dict["identity"]["description"] = req.description
            if req.domains:
                worker_dict["domains"] = req.domains
            # Phase 2.6.5: Update availability from request
            worker_dict["state"]["availability"] = availability
            # Update active_profile_key
            worker_dict['active_profile_key'] = f"{worker_id}:{profile_id}"

            from src.domain.models.worker import Worker as WorkerModel
            updated_worker = WorkerModel.model_validate(worker_dict)
            worker_store.update(updated_worker)
            logger.info(f"[Workers R3 Sync] Worker updated: {worker_id}, availability={availability.value}")

        # Step 2: Set runtime state (use request value, default to online if not provided)
        try:
            if runtime_state_store is not None:
                from src.application.services.worker_runtime_state_service import WorkerRuntimeStateService
                from src.domain.models.worker_runtime_state import WorkerRuntimeState
                from datetime import datetime

                # Upsert runtime state (set_runtime_state handles both create and update)
                # Use effective_runtime_state from request (or default "online")
                runtime_state_store.set_runtime_state(
                    worker_id=worker_id,
                    runtime_state={
                        "state": effective_runtime_state,
                        "heartbeat_at": datetime.utcnow().isoformat(),
                        "metadata": None,  # Avoid JSON serialization issues
                    },
                    updated_by="sync-worker",
                )
                logger.info(f"[Workers R3 Sync] Runtime state set: {worker_id} -> {effective_runtime_state}")

                # (Vector payload sync moved below after current_worker is available)

                # Update worker.state.runtime_state for consistency
                current_worker = worker_store.get_by_id(worker_id)
                if current_worker:
                    from src.domain.models.worker_runtime_state import WorkerRuntimeState as RuntimeStateEnum
                    if hasattr(current_worker.state, 'runtime_state'):
                        try:
                            worker_dict = current_worker.model_dump() if hasattr(current_worker, 'model_dump') else current_worker.dict()
                            worker_dict['state']['runtime_state'] = effective_runtime_state
                            from src.domain.models.worker import Worker as WorkerModel
                            updated_state_worker = WorkerModel.model_validate(worker_dict)
                            worker_store.update(updated_state_worker)
                            logger.info(f"[Workers R3 Sync] Worker.state.runtime_state updated to {effective_runtime_state}")
                        except Exception as e_state:
                            logger.warning(f"[Workers R3 Sync] Failed to update worker.state.runtime_state: {e_state}")

                    # Sync runtime_state + availability to vector store payloads (Faiss + Qdrant)
                    try:
                        _sync_runtime_state_to_vector_store(worker_id, effective_runtime_state)
                        if isinstance(current_worker, dict):
                            avail = (current_worker.get("state") or {}).get("availability", "protected")
                        else:
                            avail = getattr(getattr(current_worker, 'state', None), 'availability', 'protected')
                            if hasattr(avail, 'value'):
                                avail = avail.value
                        _sync_availability_to_vector_store(worker_id, avail)
                    except Exception as sync_err:
                        logger.warning(f"[Workers R3 Sync] Vector payload sync failed: {sync_err}")

            else:
                logger.warning(f"[Workers R3 Sync] Runtime state store not available, skipping runtime state update")
        except Exception as e:
            logger.error(f"[Workers R3 Sync] Failed to set runtime state for {worker_id}: {e}")

            # Compensation: Delete worker if just created
            if worker_created:
                try:
                    worker_store.delete(worker_id)
                    logger.warning(f"[Workers R3 Sync] Compensation: deleted worker {worker_id} due to runtime state failure")
                except Exception as cleanup_error:
                    logger.error(f"[Workers R3 Sync] Compensation failed for {worker_id}: {cleanup_error}")

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "SET_RUNTIME_STATE_FAILED", "message": f"Failed to set worker runtime state: {str(e)}"}
            )

        # Step 3: Set lifecycle state to ACTIVE
        from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
        current = worker_store.get_by_id(worker_id)
        if current:
            version = current.version if hasattr(current, 'version') else 1
            try:
                worker_store.update_lifecycle_state(worker_id, WorkerLifecycleState.ACTIVE, version)
                logger.info(f"[Workers R3 Sync] Lifecycle state updated: {worker_id} -> ACTIVE")
            except Exception as e:
                logger.warning(f"[Workers R3 Sync] Failed to update lifecycle state: {e}")

        # Step 4: Upsert and activate profile
        logger.info(
            f"[SYNC_STEP_START] worker_id={worker_id}, profile_id={profile_id}, "
            f"profile_key={worker_id}:{profile_id}, request_activate=True, runtime_state_requested=online"
        )
        logger.info(
            f"[SYNC_PROVIDER_RESOLUTION] worker_registry_store_class={type(worker_store).__name__}, "
            f"runtime_state_store_class={type(runtime_state_store).__name__ if runtime_state_store else 'None'}, "
            f"profile_content_store_class={type(profile_store).__name__ if profile_store else 'None'}"
        )
        logger.info(
            f"[SYNC_WORKER_EXISTS_CHECK] worker_id={worker_id}, exists={not created}, "
            f"created={created}"
        )
        logger.info(
            f"[SYNC_WORKER_CREATE_OR_UPDATE] worker_id={worker_id}, action={'create' if created else 'update'}, "
            f"created={created}, active_profile_key={worker_id}:{profile_id}, success=True"
        )
        logger.info(
            f"[SYNC_RUNTIME_STATE_SET] worker_id={worker_id}, target_state=online, "
            f"runtime_state_store_class={type(runtime_state_store).__name__ if runtime_state_store else 'None'}, "
            f"success=True"
        )

        # Step 4: Upsert and activate profile (root_original contract)
        # Use profile data from request (req.profile) instead of req.profile_content
        logger.info(
            f"[SYNC_STEP_START] worker_id={worker_id}, profile_id={req.profile.profile_id}, "
            f"profile_key={worker_id}:{req.profile.profile_id}, request_activate={req.profile.activate}, runtime_state_requested=online"
        )
        logger.info(
            f"[SYNC_PROVIDER_RESOLUTION] worker_registry_store_class={type(worker_store).__name__}, "
            f"runtime_state_store_class={type(runtime_state_store).__name__ if runtime_state_store else 'None'}, "
            f"profile_content_store_class={type(profile_store).__name__ if profile_store else 'None'}"
        )

        if profile_store is not None:
            try:
                profile_id = req.profile.profile_id
                logger.info(
                    f"[SYNC_PROFILE_UPSERT_START] worker_id={worker_id}, profile_id={profile_id}, "
                    f"profile_store_class={type(profile_store).__name__}"
                )

                # Construct profile data from request (root_original contract)
                # Merge summary into contents if provided
                merged_contents = dict(req.profile.contents)
                if req.profile.summary is not None:
                    merged_contents["ecb_summary"] = req.profile.summary.model_dump(exclude_none=True)

                # Fallback profile: ensure contents["profile"] exists for high-weight
                # vector fragment (0.65).  If the caller didn't provide it, generate
                # a fallback from soul_md / name / description so the vector index
                # is immediately useful.  LLM analysis will replace it later.
                if not merged_contents.get("profile"):
                    fallback = _generate_fallback_profile(req, merged_contents)
                    if fallback:
                        merged_contents["profile"] = fallback
                        logger.info(
                            f"[SYNC_FALLBACK_PROFILE] worker_id={worker_id}, "
                            f"generated fallback profile, len={len(fallback)}"
                        )

                profile_data = {
                    "worker_id": worker_id,  # CRITICAL: Include worker_id for ProfileResponse
                    "profile_id": profile_id,  # CRITICAL: Include profile_id for ProfileResponse
                    "content": req.profile.soul_md or "",  # soul_md is the main content
                    "display_name": req.profile.display_name,
                    "contents": merged_contents,
                    "skill_sets": req.profile.skill_sets,
                    "metadata": req.profile.metadata,
                    "created_at": datetime.utcnow().isoformat(),
                    "updated_at": datetime.utcnow().isoformat(),
                }

                # Upsert profile
                profile_store.upsert_profile(worker_id, profile_id, profile_data)
                logger.info(
                    f"[SYNC_PROFILE_UPSERT] worker_id={worker_id}, profile_id={profile_id}, "
                    f"profile_key={worker_id}:{profile_id}, profile_store_class={type(profile_store).__name__}, "
                    f"method=upsert_profile, success=True, contents_keys={list(merged_contents.keys())}"
                )

                # Activate profile if requested (root_original contract)
                if req.profile.activate:
                    profile_store.activate_profile(worker_id, profile_id)
                    profile_activated = True
                    logger.info(
                        f"[SYNC_PROFILE_ACTIVATE] worker_id={worker_id}, profile_id={profile_id}, "
                        f"activate_requested=True, method=activate_profile, success=True"
                    )
                    logger.info(f"[Workers R3 Sync] Profile activated: {worker_id}/{profile_id}")
                else:
                    logger.info(
                        f"[SYNC_PROFILE_SKIP_ACTIVATE] worker_id={worker_id}, profile_id={profile_id}, "
                        f"activate_requested=False"
                    )

                # Sync profile binding (root_original: _sync_profile_binding)
                logger.info(
                    f"[SYNC_PROFILE_BINDING_START] worker_id={worker_id}, profile_key={worker_id}:{profile_id}"
                )
                binding_store = _get_profile_binding_store(request)
                if binding_store is not None:
                    try:
                        profile_key = f"{worker_id}:{profile_id}"
                        binding_store.bind_profile(
                            worker_id=worker_id,
                            profile_key=profile_key,
                            source_type=WorkerSourceType.API,
                        )
                        logger.info(
                            f"[SYNC_PROFILE_BINDING] worker_id={worker_id}, profile_key={profile_key}, "
                            f"binding_store_class={type(binding_store).__name__}, method=bind_profile, success=True"
                        )
                        logger.info(f"[Workers R3 Sync] Profile binding created: {profile_key}")
                    except Exception as e_bind:
                        logger.error(
                            f"[SYNC_PROFILE_BINDING] worker_id={worker_id}, profile_key={profile_key}, "
                            f"binding_store_class={type(binding_store).__name__}, method=bind_profile, "
                            f"success=False, exception_type={type(e_bind).__name__}, exception_preview={str(e_bind)[:200]}"
                        )
                        logger.warning(f"[Workers R3 Sync] Failed to sync profile binding: {e_bind}")
                else:
                    logger.warning(
                        f"[SYNC_PROFILE_BINDING] worker_id={worker_id}, profile_key={worker_id}:{profile_id}, "
                        f"binding_store_class=None, method=bind_profile, success=False, "
                        f"exception_type=PROVIDER_NOT_AVAILABLE, exception_preview='binding_store provider not available'"
                    )

                # CRITICAL FIX: Sync worker active_profile_key (root_original: _sync_worker_active_profile)
                # Note: active_profile_key is already set during worker creation/update (lines 234 and 252).
                # This block ensures it's updated again if profile.activate=True.
                if req.profile.activate:
                    logger.info(
                        f"[SYNC_ACTIVE_PROFILE_KEY_START] worker_id={worker_id}, profile_key={worker_id}:{profile_id}"
                    )
                    try:
                        # Re-fetch worker to get latest version
                        worker = worker_store.get_by_id(worker_id)
                        if worker:
                            # Check if active_profile_key already matches
                            current_profile_key = getattr(worker, 'active_profile_key', None)
                            expected_profile_key = f"{worker_id}:{profile_id}"

                            if current_profile_key != expected_profile_key:
                                # Update worker's active_profile_key
                                if hasattr(worker, 'model_dump'):
                                    worker_dict = worker.model_dump()
                                elif hasattr(worker, 'dict'):
                                    worker_dict = worker.dict()
                                else:
                                    worker_dict = dict(worker)

                                worker_dict['active_profile_key'] = expected_profile_key

                                from src.domain.models.worker import Worker as WorkerModel
                                updated_worker = WorkerModel.model_validate(worker_dict)
                                worker_store.update(updated_worker)

                                logger.info(
                                    f"[SYNC_ACTIVE_PROFILE_KEY] worker_id={worker_id}, "
                                    f"profile_key={expected_profile_key}, success=True, "
                                    f"method=worker_store.update, previous_key={current_profile_key}"
                                )
                                logger.info(f"[Workers R3 Sync] Worker active_profile_key updated: {expected_profile_key}")
                            else:
                                logger.info(
                                    f"[SYNC_ACTIVE_PROFILE_KEY] worker_id={worker_id}, "
                                    f"profile_key={expected_profile_key}, already_set=True, "
                                    f"skip_update=True"
                                )
                        else:
                            logger.warning(
                                f"[SYNC_ACTIVE_PROFILE_KEY] worker_id={worker_id}, "
                                f"profile_key={worker_id}:{profile_id}, success=False, "
                                f"exception_type=WORKER_NOT_FOUND"
                            )
                    except Exception as e_active:
                        logger.error(
                            f"[SYNC_ACTIVE_PROFILE_KEY] worker_id={worker_id}, "
                            f"profile_key={worker_id}:{profile_id}, success=False, "
                            f"exception_type={type(e_active).__name__}, exception_preview={str(e_active)[:200]}",
                            exc_info=True
                        )
                        logger.warning(f"[Workers R3 Sync] Failed to update worker active_profile_key: {e_active}")

                # CRITICAL FIX: Trigger index sync (root_original: _trigger_index_sync)
                # This ensures search/recommend can find the new profile
                logger.info(f"[SYNC_INDEX_SYNC_START] worker_id={worker_id}")
                try:
                    from src.interfaces.api.profile_routes import _trigger_index_sync
                    _trigger_index_sync(worker_id)
                    logger.info(
                        f"[SYNC_INDEX_SYNC] worker_id={worker_id}, success=True, method=_trigger_index_sync"
                    )
                    logger.info(f"[Workers R3 Sync] Index sync triggered for: {worker_id}")
                except Exception as e_index:
                    logger.warning(
                        f"[SYNC_INDEX_SYNC] worker_id={worker_id}, success=False, "
                        f"exception_type={type(e_index).__name__}, exception_preview={str(e_index)[:200]}"
                    )
                    logger.warning(f"[Workers R3 Sync] Failed to trigger index sync: {e_index}")

                # P0-SEARCH-RECOMMEND-EAGER-INDEXING-FIX:
                # Immediately trigger incremental indexing for the current worker/profile.
                # This ensures the newly synced worker is searchable within 5 seconds,
                # avoiding the lazy full-scan on first search/recommend request.
                logger.info(
                    f"[SYNC_EAGER_INDEX_TRIGGER] worker_id={worker_id}, "
                    f"profile_id={profile_id}, profile_key={worker_id}:{profile_id}, "
                    f"mode=current_profile_incremental"
                )
                try:
                    from src.interfaces.api.dependencies.fusion_dependencies import _build_vector_index_for_worker
                    import time
                    start_time = time.time()

                    index_success = _build_vector_index_for_worker(worker_id)

                    elapsed_ms = int((time.time() - start_time) * 1000)

                    if index_success:
                        logger.info(
                            f"[SYNC_EAGER_INDEX_RESULT] worker_id={worker_id}, "
                            f"profile_id={profile_id}, profile_key={worker_id}:{profile_id}, "
                            f"result=PASS, elapsed_ms={elapsed_ms}, "
                            f"method=_build_vector_index_for_worker"
                        )
                    else:
                        logger.warning(
                            f"[SYNC_EAGER_INDEX_RESULT] worker_id={worker_id}, "
                            f"profile_id={profile_id}, profile_key={worker_id}:{profile_id}, "
                            f"result=FAIL, elapsed_ms={elapsed_ms}, "
                            f"method=_build_vector_index_for_worker"
                        )
                except Exception as e_eager:
                    logger.error(
                        f"[SYNC_EAGER_INDEX_ERROR] worker_id={worker_id}, "
                        f"profile_id={profile_id}, profile_key={worker_id}:{profile_id}, "
                        f"exception_type={type(e_eager).__name__}, "
                        f"message_preview={str(e_eager)[:200]}",
                        exc_info=True
                    )

                logger.info(
                    f"[SYNC_ACTIVE_PROFILE_VERIFY] worker_id={worker_id}, "
                    f"expected_profile_key={worker_id}:{profile_id}, "
                    f"profile_activated_result={profile_activated}"
                )

                # LLM Profile Analysis (aligned with internal worker_routes.py)
                # Condition: availability != private OR runtime_state == online
                should_run_llm_analysis = (
                    req.availability != "private"
                    or effective_runtime_state == "online"
                )

                if should_run_llm_analysis:
                    # Serialize profile_data for background task
                    # (background task runs after response, needs a snapshot)
                    profile_data_snapshot = dict(profile_data)

                    if req.sync_llm:
                        # Synchronous: await before returning HTTP response
                        logger.info(
                            f"[SYNC_LLM] worker_id={worker_id}, mode=synchronous"
                        )
                        try:
                            await _analyze_and_persist_async(
                                worker_id=worker_id,
                                profile_id=profile_id,
                                profile_data=profile_data_snapshot,
                            )
                            logger.info(f"[SYNC_LLM] worker_id={worker_id}, mode=synchronous, result=success")
                        except Exception as e_llm:
                            logger.warning(f"[SYNC_LLM] Synchronous LLM analysis failed: {e_llm}")
                    else:
                        # Asynchronous: fire-and-forget background task
                        import asyncio
                        asyncio.create_task(
                            _analyze_and_persist_async(
                                worker_id=worker_id,
                                profile_id=profile_id,
                                profile_data=profile_data_snapshot,
                            )
                        )
                        logger.info(
                            f"[SYNC_LLM] worker_id={worker_id}, mode=async, LLM analysis scheduled in background"
                        )
                else:
                    logger.info(
                        f"[SYNC_LLM] worker_id={worker_id}, skipped "
                        f"(availability={req.availability}, runtime_state={effective_runtime_state})"
                    )

            except Exception as e:
                # Profile upsert failure is non-fatal
                logger.error(
                    f"[SYNC_PROFILE_UPSERT] worker_id={worker_id}, profile_id={profile_id}, "
                    f"profile_store_class={type(profile_store).__name__}, method=upsert_profile, "
                    f"success=False, exception_type={type(e).__name__}, exception_preview={str(e)[:200]}",
                    exc_info=True
                )
                logger.error(f"[Workers R3 Sync] Profile upsert failed for {worker_id}: {e}", exc_info=True)
                # Worker remains online, but profile_activated = False
        else:
            # No profile store available
            logger.warning(
                f"[SYNC_PROFILE_SKIP] worker_id={worker_id}, profile_store_class=None, "
                f"reason='profile_store provider not available'"
            )

        # Return canonical response (root_original contract)
        logger.info(
            f"[SYNC_RESPONSE] success=True, worker_id={worker_id}, created={created}, "
            f"runtime_state={effective_runtime_state}, profile_id={profile_id}, "
            f"profile_activated={profile_activated}"
        )
        return WorkerSyncResponse(
            success=True,
            worker_id=worker_id,
            created=created,
            runtime_state=effective_runtime_state,
            profile_id=profile_id,
            profile_activated=profile_activated,
        )

    except HTTPException:
        # Already handled, re-raise
        raise
    except Exception as e:
        logger.error(f"[Workers R3 Sync] Failed to sync worker {worker_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "SYNC_WORKER_ERROR", "message": str(e)}
        )


# ---------------------------------------------------------------------------
# Backward-compatible sync route (mounted at /v1 for existing callers like BCS)
# Reuses the same handler so logic stays in one place.
# ---------------------------------------------------------------------------
@compat_router.post(
    "/workers/{worker_id}/sync",
    summary="Sync worker (backward-compatible)",
    description="Atomic sync: create + online + profile activation. "
                "Legacy path at /v1 for backward compatibility; prefer /api/v1.",
    response_model=WorkerSyncResponse,
    tags=["Workers"],
    deprecated=True,
)
async def sync_worker_compat(worker_id: str, request: Request, req: WorkerSyncRequest):
    """Backward-compatible alias for sync_worker at /v1 prefix."""
    return await sync_worker(worker_id, request, req)


@api_router.put(
    "/workers/{worker_id}/availability",
    summary="Set worker availability",
    description="Update worker availability status.",
    response_model=WorkerAvailabilityResponse,
    tags=["Workers"],
)
async def set_worker_availability(worker_id: str, request: Request, req: WorkerAvailabilityUpdate):
    """P1: Set worker availability."""
    _require_auth(request)

    store = _get_worker_registry_store(request)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_registry_store provider not available"
            }
        )

    try:
        worker = store.get_by_id(worker_id)
        if worker is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WORKER_NOT_FOUND", "message": f"Worker {worker_id} not found"}
            )

        # Convert dict to Worker object if needed
        from src.domain.models.worker import Worker
        from src.domain.models.worker import Availability
        if isinstance(worker, dict):
            # For MySQL store, use update(worker_id, updates) directly
            import inspect
            update_sig = inspect.signature(store.update)
            params = list(update_sig.parameters.keys())

            avail_value = req.availability.value if hasattr(req.availability, 'value') else str(req.availability)

            if len(params) >= 2 and params[0] == 'worker_id':
                # MySQL-style store: update(worker_id, updates)
                success = store.update(worker_id, {"availability": avail_value})
                if not success:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail={"code": "UPDATE_FAILED", "message": "Failed to update availability"}
                    )
                updated_at = None  # We'll get it from re-fetch
            else:
                # SQLite/InMemory-style: need to use Worker object
                # Convert dict to Worker first
                worker_obj = Worker.model_validate(worker)
                worker_obj.state.availability = Availability(avail_value)
                worker_obj = store.update(worker_obj)
                updated_at = worker_obj.updated_at

            # Sync availability to Qdrant vector payload so search filters work
            _sync_availability_to_vector_store(worker_id, avail_value)

            import datetime
            return WorkerAvailabilityResponse(
                worker_id=worker_id,
                availability=req.availability,
                updated_at=updated_at or datetime.datetime.now().isoformat(),
            )
        else:
            # Worker object - update directly
            from src.domain.models.worker import Availability
            avail_value = req.availability.value if hasattr(req.availability, 'value') else str(req.availability)
            worker.state.availability = Availability(avail_value)
            updated_worker = store.update(worker)

            # Sync availability to Qdrant vector payload so search filters work
            _sync_availability_to_vector_store(worker_id, avail_value)

            return WorkerAvailabilityResponse(
                worker_id=worker_id,
                availability=req.availability,
                updated_at=updated_worker.updated_at,
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Workers R3] Failed to set availability for {worker_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "SET_AVAILABILITY_ERROR", "message": str(e)}
        )


@mgmt_router.put(
    "/workers/{worker_id}/trust-level",
    summary="Set worker trust level",
    description="Update worker trust level.",
    response_model=WorkerTrustLevelResponse,
    tags=["Workers"],
)
async def set_worker_trust_level(worker_id: str, request: Request, req: WorkerTrustLevelUpdate):
    """P1: Set worker trust level."""
    _require_auth(request)

    store = _get_worker_registry_store(request)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_registry_store provider not available"
            }
        )

    try:
        worker = store.get_by_id(worker_id)
        if worker is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WORKER_NOT_FOUND", "message": f"Worker {worker_id} not found"}
            )

        # Convert dict to Worker object if needed
        from src.domain.models.worker import Worker
        from src.domain.models.worker import TrustLevel
        if isinstance(worker, dict):
            # For MySQL store, use update(worker_id, updates) directly
            import inspect
            update_sig = inspect.signature(store.update)
            params = list(update_sig.parameters.keys())

            trust_value = req.trust_level.value if hasattr(req.trust_level, 'value') else str(req.trust_level)

            if len(params) >= 2 and params[0] == 'worker_id':
                # MySQL-style store: update(worker_id, updates)
                success = store.update(worker_id, {"trust_level": trust_value})
                if not success:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail={"code": "UPDATE_FAILED", "message": "Failed to update trust level"}
                    )
                import datetime
                return WorkerTrustLevelResponse(
                    worker_id=worker_id,
                    trust_level=req.trust_level,
                    updated_at=datetime.datetime.now().isoformat(),
                )
            else:
                # SQLite/InMemory-style: need to use Worker object
                worker_obj = Worker.model_validate(worker)
                worker_obj.state.trust_level = TrustLevel(trust_value)
                worker_obj = store.update(worker_obj)
                return WorkerTrustLevelResponse(
                    worker_id=worker_id,
                    trust_level=req.trust_level,
                    updated_at=worker_obj.updated_at,
                )
        else:
            # Worker object - update directly
            worker.state.trust_level = TrustLevel(req.trust_level.value if hasattr(req.trust_level, 'value') else req.trust_level)
            updated_worker = store.update(worker)

            return WorkerTrustLevelResponse(
                worker_id=worker_id,
                trust_level=req.trust_level,
                updated_at=updated_worker.updated_at,
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Workers R3] Failed to set trust level for {worker_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "SET_TRUST_LEVEL_ERROR", "message": str(e)}
        )


@admin_router.patch(
    "/workers/{worker_id}",
    summary="Patch worker",
    description="Partial update of worker fields.",
    response_model=WorkerPatchResponse,
    tags=["Workers"],
)
async def patch_worker(worker_id: str, request: Request, req: WorkerPatchRequest):
    """P1: Patch worker."""
    _require_auth(request)

    store = _get_worker_registry_store(request)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_registry_store provider not available"
            }
        )

    try:
        worker = store.get_by_id(worker_id)
        if worker is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WORKER_NOT_FOUND", "message": f"Worker {worker_id} not found"}
            )

        # Convert dict to Worker object if needed
        from src.domain.models.worker import Worker, WorkerState, WorkerIdentity
        from src.domain.models.worker import Availability, TrustLevel, WorkerType
        from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
        from src.domain.models.worker_source_info import WorkerSourceType

        if isinstance(worker, dict):
            # Map API response fields to Worker model fields
            worker_dict = worker.copy()

            # Map worker_id -> id
            if "worker_id" in worker_dict:
                worker_dict["id"] = worker_dict.pop("worker_id")

            # Map worker_type -> type
            if "worker_type" in worker_dict:
                worker_dict["type"] = worker_dict.pop("worker_type")

            # Parse state if it's a string
            if "state" in worker_dict and isinstance(worker_dict["state"], str):
                # Convert enum string to WorkerState dict
                state_str = worker_dict.pop("state")
                # Remove separate availability/trust_level fields
                availability_str = worker_dict.pop("availability", state_str)
                trust_level_str = worker_dict.pop("trust_level", "TrustLevel.UNVERIFIED")

                # Parse availability
                if "Availability." in availability_str:
                    availability_value = availability_str.split(".")[-1].lower()
                else:
                    availability_value = availability_str.lower()

                # Parse trust level
                if "TrustLevel." in trust_level_str:
                    trust_level_value = trust_level_str.split(".")[-1].lower()
                else:
                    trust_level_value = trust_level_str.lower()

                # Create WorkerState dict
                worker_dict["state"] = {
                    "availability": availability_value,
                    "trust_level": trust_level_value,
                    "runtime_state": "offline",
                    "is_public": availability_value == "PUBLIC"
                }

            # Ensure required fields exist
            if "lifecycle_state" not in worker_dict:
                worker_dict["lifecycle_state"] = "active"
            if "source_type" not in worker_dict:
                worker_dict["source_type"] = "api"
            if "responsibilities" not in worker_dict:
                worker_dict["responsibilities"] = []
            if "domains" not in worker_dict:
                worker_dict["domains"] = []
            if "constraints" not in worker_dict:
                worker_dict["constraints"] = []
            if "memory_refs" not in worker_dict:
                worker_dict["memory_refs"] = []

            # Remove metadata from top level (it belongs in config)
            worker_dict.pop("metadata", None)

            worker = Worker.model_validate(worker_dict)

        # Build updates dict for model_copy
        update_dict = {}
        updated_fields = []

        if req.name is not None:
            update_dict["identity"] = worker.identity.model_copy(update={"name": req.name})
            updated_fields.append("name")

        if req.description is not None:
            identity_update = update_dict.get("identity", worker.identity)
            update_dict["identity"] = identity_update.model_copy(update={"description": req.description})
            updated_fields.append("description")

        if req.domains is not None:
            update_dict["domains"] = req.domains
            updated_fields.append("domains")

        if req.capabilities is not None:
            update_dict["capabilities"] = req.capabilities
            updated_fields.append("capabilities")

        if req.responsibilities is not None:
            update_dict["responsibilities"] = req.responsibilities
            updated_fields.append("responsibilities")

        if req.metadata is not None:
            update_dict["config"] = worker.config.model_copy(update={"metadata": req.metadata})
            updated_fields.append("metadata")

        # Update worker using model_copy
        if update_dict:
            updated_worker_obj = worker.model_copy(update=update_dict)

            # Handle different store.update signatures
            # MySQL store: update(worker_id: str, updates: dict) -> bool
            # SQLite/InMemory store: update(worker: Worker) -> Worker
            import inspect
            update_sig = inspect.signature(store.update)
            params = list(update_sig.parameters.keys())

            if len(params) >= 2 and params[0] == 'worker_id':
                # MySQL-style store: update(worker_id, updates)
                # Build a flat updates dict with only changed fields
                mysql_updates = {}

                # Extract identity changes
                if "identity" in update_dict:
                    identity = update_dict["identity"]
                    mysql_updates["identity_name"] = identity.name
                    if identity.description:
                        mysql_updates["identity_description"] = identity.description

                # Extract state changes
                if "state" in update_dict:
                    state = update_dict["state"]
                    if hasattr(state, 'availability'):
                        mysql_updates["availability"] = state.availability.value if hasattr(state.availability, 'value') else str(state.availability)
                    if hasattr(state, 'trust_level'):
                        mysql_updates["trust_level"] = state.trust_level.value if hasattr(state.trust_level, 'value') else str(state.trust_level)

                # Extract simple field changes
                for field in ["domains", "responsibilities", "capabilities", "skills", "resources", "constraints", "memory_refs"]:
                    if field in update_dict:
                        mysql_updates[field] = update_dict[field]

                # Extract config changes
                if "config" in update_dict:
                    config = update_dict["config"]
                    if hasattr(config, 'metadata'):
                        mysql_updates["metadata"] = config.metadata

                success = store.update(worker_id, mysql_updates)
                if not success:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail={"code": "UPDATE_FAILED", "message": "Failed to update worker"}
                    )
                # Re-fetch the updated worker from MySQL
                updated_worker = store.get_by_id(worker_id)
                if isinstance(updated_worker, dict):
                    # Convert MySQL dict to Worker object
                    # This ensures we get the actual MySQL state (updated_at, version, etc.)
                    worker_dict = updated_worker.copy()

                    # Map MySQL column names to Worker model field names
                    if "worker_id" in worker_dict:
                        worker_dict["id"] = worker_dict.pop("worker_id")
                    if "worker_type" in worker_dict:
                        worker_dict["type"] = worker_dict.pop("worker_type")

                    # Parse state if it's a string
                    if "state" in worker_dict and isinstance(worker_dict["state"], str):
                        state_str = worker_dict.pop("state")
                        availability_str = worker_dict.pop("availability", state_str)
                        trust_level_str = worker_dict.pop("trust_level", "unverified")

                        # Parse availability
                        if "Availability." in availability_str:
                            availability_value = availability_str.split(".")[-1].lower()
                        else:
                            availability_value = availability_str.lower()

                        # Parse trust level
                        if "TrustLevel." in trust_level_str:
                            trust_level_value = trust_level_str.split(".")[-1].lower()
                        else:
                            trust_level_value = trust_level_str.lower()

                        # Create WorkerState dict
                        worker_dict["state"] = {
                            "availability": availability_value,
                            "trust_level": trust_level_value,
                            "runtime_state": "offline",
                            "is_public": availability_value == "public"
                        }

                    # Remove top-level metadata (it belongs in config)
                    worker_dict.pop("metadata", None)

                    # Ensure required fields
                    if "lifecycle_state" not in worker_dict:
                        worker_dict["lifecycle_state"] = "active"
                    if "source_type" not in worker_dict:
                        worker_dict["source_type"] = "api"
                    if "responsibilities" not in worker_dict:
                        worker_dict["responsibilities"] = []
                    if "domains" not in worker_dict:
                        worker_dict["domains"] = []
                    if "constraints" not in worker_dict:
                        worker_dict["constraints"] = []
                    if "memory_refs" not in worker_dict:
                        worker_dict["memory_refs"] = []

                    updated_worker = Worker.model_validate(worker_dict)
            else:
                # SQLite/InMemory-style store: update(worker) -> Worker
                updated_worker = store.update(updated_worker_obj)
        else:
            updated_worker = worker

        return WorkerPatchResponse(
            worker_id=worker_id,
            updated_fields=updated_fields,
            updated_at=updated_worker.updated_at if hasattr(updated_worker, 'updated_at') else worker.updated_at,
            version=updated_worker.version if hasattr(updated_worker, 'version') else worker.version,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Workers R3] Failed to patch worker {worker_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "PATCH_WORKER_ERROR", "message": str(e)}
        )


@mgmt_router.get(
    "/workers/{worker_id}/config",
    summary="Get worker config",
    description="Get worker configuration.",
    response_model=WorkerConfigResponse,
    tags=["Workers"],
)
async def get_worker_config(worker_id: str, request: Request):
    """P1: Get worker config."""
    _require_auth(request)

    store = _get_worker_registry_store(request)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_registry_store provider not available"
            }
        )

    try:
        worker = store.get_by_id(worker_id)
        if worker is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WORKER_NOT_FOUND", "message": f"Worker {worker_id} not found"}
            )

        # Get config
        if hasattr(worker, 'config'):
            config = worker.config
            if hasattr(config, 'model_dump'):
                config_dict = config.model_dump()
            elif hasattr(config, 'dict'):
                config_dict = config.dict()
            else:
                config_dict = dict(config) if config else {}
        else:
            config_dict = {}

        fusion_enable = config_dict.get("fusion_enable", False)

        return WorkerConfigResponse(
            worker_id=worker_id,
            fusion_enable=fusion_enable,
            config=config_dict,
            version=getattr(worker, 'version', 1),
            updated_at=getattr(worker, 'updated_at', None),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Workers R3] Failed to get config for {worker_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "GET_CONFIG_ERROR", "message": str(e)}
        )


@mgmt_router.put(
    "/workers/{worker_id}/config",
    summary="Update worker config",
    description="Update worker configuration.",
    response_model=WorkerConfigResponse,
    tags=["Workers"],
)
async def update_worker_config(worker_id: str, request: Request, req: WorkerConfigUpdate):
    """P1: Update worker config."""
    _require_auth(request)

    store = _get_worker_registry_store(request)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_registry_store provider not available"
            }
        )

    try:
        worker = store.get_by_id(worker_id)
        if worker is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WORKER_NOT_FOUND", "message": f"Worker {worker_id} not found"}
            )

        # Build config update
        config_update = req.config or {}
        if req.fusion_enable is not None:
            config_update["fusion_enable"] = req.fusion_enable

        # Update worker config
        for key, value in config_update.items():
            setattr(worker.config, key, value)

        # Update worker with correct method signature
        updated_worker = store.update(worker)

        # Get updated config
        updated_config = updated_worker.config
        if hasattr(updated_config, 'model_dump'):
            updated_config_dict = updated_config.model_dump()
        elif hasattr(updated_config, 'dict'):
            updated_config_dict = updated_config.dict()
        else:
            updated_config_dict = dict(updated_config) if updated_config else {}

        return WorkerConfigResponse(
            worker_id=worker_id,
            fusion_enable=updated_config_dict.get("fusion_enable", False),
            config=updated_config_dict,
            version=updated_worker.version,
            updated_at=updated_worker.updated_at,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Workers R3] Failed to update config for {worker_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "UPDATE_CONFIG_ERROR", "message": str(e)}
        )


@admin_router.post(
    "/workers/config/batch",
    summary="Batch update worker configs",
    description="Update configurations for multiple workers.",
    response_model=WorkerConfigBatchResponse,
    tags=["Workers"],
)
async def batch_update_worker_configs(request: Request, req: WorkerConfigBatchUpdate):
    """P1: Batch update worker configs."""
    _require_auth(request)

    store = _get_worker_registry_store(request)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_registry_store provider not available"
            }
        )

    try:
        updated = []
        failed = []

        for update_item in req.updates:
            worker_id = update_item.get("worker_id")
            config = update_item.get("config", {})

            if not worker_id:
                failed.append({
                    "worker_id": "unknown",
                    "reason": "worker_id is required"
                })
                continue

            try:
                # Check if worker exists
                worker = store.get_by_id(worker_id)
                if worker is None:
                    failed.append({
                        "worker_id": worker_id,
                        "reason": "Worker not found"
                    })
                    continue

                # Update config
                store.update(worker_id, {"config": config})
                updated.append(worker_id)

            except Exception as e:
                failed.append({
                    "worker_id": worker_id,
                    "reason": str(e)
                })

        return WorkerConfigBatchResponse(
            updated=updated,
            failed=failed,
            total=len(req.updates),
        )
    except Exception as e:
        logger.error(f"[Workers R3] Failed to batch update configs: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "BATCH_UPDATE_CONFIG_ERROR", "message": str(e)}
        )


@mgmt_router.get(
    "/workers/config/by-source",
    summary="Query workers by source",
    description="Get workers filtered by source type.",
    response_model=WorkersBySourceResponse,
    tags=["Workers"],
)
async def query_workers_by_source(request: Request, source: Optional[str] = None):
    """P1: Query workers by source type."""
    _require_auth(request)

    store = _get_worker_registry_store(request)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_registry_store provider not available"
            }
        )

    try:
        from src.domain.models.worker_source_info import WorkerSourceType

        # Convert source string to enum
        source_types = None
        if source:
            try:
                source_types = [WorkerSourceType(source)]
            except ValueError:
                # Invalid source type, return empty
                return WorkersBySourceResponse(
                    source=source or "all",
                    workers=[],
                    total=0,
                )

        # List workers with source filter
        workers = store.list(source_types=source_types, limit=1000)

        # Convert to dict
        workers_list = []
        for w in workers:
            if hasattr(w, 'model_dump'):
                workers_list.append(w.model_dump())
            elif hasattr(w, 'dict'):
                workers_list.append(w.dict())
            else:
                workers_list.append(dict(w))

        return WorkersBySourceResponse(
            source=source or "all",
            workers=workers_list,
            total=len(workers_list),
        )
    except Exception as e:
        logger.error(f"[Workers R3] Failed to query workers by source: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "QUERY_BY_SOURCE_ERROR", "message": str(e)}
        )


@mgmt_router.get(
    "/workers/{worker_id}/profiles/quality",
    summary="Get worker profile quality",
    description="Get quality metrics for worker's profiles.",
    response_model=WorkerProfileQualityResponse,
    tags=["Workers"],
)
async def get_worker_profile_quality(worker_id: str, request: Request):
    """P1: Get worker profile quality."""
    _require_auth(request)

    worker_store = _get_worker_registry_store(request)
    profile_store = _get_profile_content_store(request)

    if worker_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_registry_store provider not available"
            }
        )

    try:
        # Check if worker exists
        worker = worker_store.get_by_id(worker_id)
        if worker is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WORKER_NOT_FOUND", "message": f"Worker {worker_id} not found"}
            )

        # Get profile metrics
        profile_count = 0
        active_profile_key = None
        quality_score = 0.0

        if profile_store is not None:
            profiles = profile_store.list_profiles(worker_id)
            profile_count = len(profiles) if profiles else 0

            # Get active profile
            active_profile = profile_store.get_active_profile_for_worker(worker_id)
            if active_profile:
                active_profile_key = f"{worker_id}:{active_profile.get('profile_id', 'default')}"

        # Minimal quality score: based on profile count
        # Score = min(profile_count / 10.0, 1.0)
        quality_score = min(profile_count / 10.0, 1.0)

        return WorkerProfileQualityResponse(
            worker_id=worker_id,
            quality_score=quality_score,
            profile_count=profile_count,
            active_profile_key=active_profile_key,
            quality_details={
                "profile_count": profile_count,
                "has_active_profile": active_profile_key is not None,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Workers R3] Failed to get profile quality for {worker_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "GET_PROFILE_QUALITY_ERROR", "message": str(e)}
        )


@mgmt_router.put(
    "/workers/{worker_id}/profiles/{profile_id}/activate",
    summary="Activate Profile",
    description="Activate profile (canonical PUT method for OpenAPI P1 contract parity).",
    response_model=ActivateResponse,
    tags=["Profiles"],
)
async def activate_profile_put(worker_id: str, profile_id: str, request: Request):
    """
    P1: Activate profile (PUT method for canonical contract).

    If ENABLE_PROFILE_EMBEDDING_INDEX=true, triggers embedding generation and vector indexing.
    """
    _require_auth(request)

    profile_store = _get_profile_content_store(request)

    if profile_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_profile_content_store provider not available"
            }
        )

    # Initialize metadata for response
    embedding_index_requested = False
    embedding_indexed = False
    vector_upserted = False
    index_error_code = None
    index_error_preview = None

    try:
        # Check if profile exists
        profile = profile_store.get_profile(worker_id, profile_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PROFILE_NOT_FOUND", "message": f"Profile {profile_id} not found for worker {worker_id}"}
            )

        # Get previous active profile
        previous_active = profile_store.get_active_profile_for_worker(worker_id)
        previous_active_key = None
        if previous_active:
            previous_active_key = f"{worker_id}:{previous_active.get('profile_id', 'default')}"

        # Activate profile
        activated = profile_store.activate_profile(worker_id, profile_id)
        if not activated:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "ACTIVATE_PROFILE_ERROR", "message": "Failed to activate profile"}
            )

        profile_key = f"{worker_id}:{profile_id}"

        # Phase C-Fast-3: Sync profile binding to ensure visibility in G5 retrieval
        # CRITICAL: This MUST sync denormalized columns workers.active_profile_key
        binding_updated = False
        binding_store_class = None
        binding_store_id = None
        registry_store_injected = False

        try:
            binding_store = _get_profile_binding_store(request)
            if binding_store:
                binding_store_class = type(binding_store).__name__
                binding_store_id = id(binding_store)

                # Check if registry_store is injected
                registry_store_injected = hasattr(binding_store, '_registry_store') and binding_store._registry_store is not None

                logger.info(
                    f"[Profiles R3] bind_profile() diagnostic: "
                    f"profile_key={profile_key}, "
                    f"binding_store_class={binding_store_class}, "
                    f"binding_store_id={binding_store_id}, "
                    f"registry_store_injected={registry_store_injected}"
                )

                from src.domain.models.worker_source_info import WorkerSourceType
                binding_result = binding_store.bind_profile(
                    worker_id=worker_id,
                    profile_key=profile_key,
                    source_type=WorkerSourceType.API,
                )
                binding_updated = True
                logger.info(
                    f"[Profiles R3] bind_profile() completed: "
                    f"profile_key={profile_key}, "
                    f"binding_id={binding_result.id if hasattr(binding_result, 'id') else 'N/A'}, "
                    f"is_active={binding_result.is_active if hasattr(binding_result, 'is_active') else 'N/A'}"
                )
            else:
                logger.warning(f"[Profiles R3] Profile binding store not available for {profile_key}")
        except Exception as e:
            logger.error(
                f"[Profiles R3] bind_profile() failed for {profile_key}: {e}",
                exc_info=True
            )

        # Check if profile embedding indexing is enabled
        from src.infra.config.feature_flags import FeatureFlags
        if FeatureFlags.is_profile_embedding_index_enabled():
            embedding_index_requested = True
            logger.info(f"[Profiles R3] Profile embedding indexing enabled, starting indexing for {profile_key}")

            try:
                # Get embedding provider and vector store
                from src.interfaces.api.dependencies.fusion_dependencies import (
                    _get_embedding_generator,
                    _get_vector_match_service,
                )
                from src.domain.models.worker_profile import WorkerProfile

                # Get embedding generator
                embedding_provider = _get_embedding_generator()
                if embedding_provider is None:
                    logger.warning(f"[Profiles R3] Embedding provider not available for {profile_key}, skipping indexing")
                    index_error_code = "EMBEDDING_PROVIDER_NOT_AVAILABLE"
                    index_error_preview = "Embedding provider not configured or initialization failed"
                else:
                    # Get vector match service
                    vector_match_service = _get_vector_match_service()
                    if vector_match_service is None:
                        logger.warning(f"[Profiles R3] Vector match service not available for {profile_key}, skipping indexing")
                        index_error_code = "VECTOR_STORE_NOT_AVAILABLE"
                        index_error_preview = "Vector store not configured or initialization failed"
                    else:
                        # Extract profile content
                        if isinstance(profile, dict):
                            profile_content = profile.get('content', '')
                            profile_metadata = profile.get('metadata', {})
                        else:
                            profile_content = getattr(profile, 'content', '')
                            profile_metadata = getattr(profile, 'metadata', {})

                        # Build embedding text
                        # Combine worker_id, profile_id, role, domains, expertise
                        role = profile_metadata.get('role', '')
                        domains = profile_metadata.get('domains', [])
                        expertise = profile_metadata.get('expertise', [])

                        embedding_text_parts = [profile_content]
                        if role:
                            embedding_text_parts.append(f"Role: {role}")
                        if domains:
                            embedding_text_parts.append(f"Domains: {', '.join(domains)}")
                        if expertise:
                            embedding_text_parts.append(f"Expertise: {', '.join(expertise)}")

                        embedding_text = '\n'.join(embedding_text_parts)

                        logger.info(f"[Profiles R3] Generating embedding for {profile_key} (text_length={len(embedding_text)})")

                        # Generate embedding
                        import time
                        start_time = time.time()
                        embedding_vector = embedding_provider.embed(embedding_text)
                        embedding_latency_ms = int((time.time() - start_time) * 1000)

                        if embedding_vector and len(embedding_vector) > 0:
                            embedding_indexed = True
                            logger.info(
                                f"[Profiles R3] Embedding generated for {profile_key}: "
                                f"dimension={len(embedding_vector)}, latency={embedding_latency_ms}ms"
                            )

                            # Get worker state for payload
                            worker_registry_store = _get_worker_registry_store(request)
                            availability = "public"
                            runtime_state = "offline"

                            if worker_registry_store:
                                worker = worker_registry_store.get_by_id(worker_id)
                                if worker:
                                    if hasattr(worker, 'state') and hasattr(worker.state, 'availability'):
                                        availability = worker.state.availability.value if hasattr(worker.state.availability, 'value') else str(worker.state.availability)
                                    # Try to get runtime state if available
                                    try:
                                        from src.interfaces.api.dependencies.worker_dependencies import _get_runtime_state_store
                                        runtime_state_store = _get_runtime_state_store()
                                        if runtime_state_store:
                                            rs = runtime_state_store.get_runtime_state(worker_id)
                                            if rs:
                                                runtime_state = rs.value if hasattr(rs, 'value') else str(rs)
                                    except Exception as e:
                                        logger.debug(f"[Profiles R3] Could not get runtime state for {worker_id}: {e}")

                            # Prepare payload for vector store
                            payload = {
                                "worker_id": worker_id,
                                "profile_id": profile_id,
                                "role": role,
                                "domains": domains,
                                "expertise": expertise,
                                "content_type": "profile",
                                "is_active": True,
                                "availability": availability,
                                "runtime_state": runtime_state,
                            }

                            # Upsert to vector store
                            try:
                                # Access the underlying vector store from vector_match_service
                                vector_store = vector_match_service._vector_store

                                # DIAGNOSTIC: Log vector store instance info for singleton verification
                                vector_store_instance_id = id(vector_store)
                                vector_store_type = type(vector_store).__name__
                                logger.info(
                                    f"[Profiles R3] Using vector_store instance: "
                                    f"id={vector_store_instance_id}, type={vector_store_type}"
                                )

                                # Upsert the vector
                                vector_store.upsert(
                                    id=profile_key,
                                    vector=embedding_vector,
                                    metadata=payload,
                                )
                                vector_upserted = True
                                logger.info(
                                    f"[Profiles R3] Vector upserted for {profile_key}: "
                                    f"dimension={len(embedding_vector)}, payload_keys={list(payload.keys())}"
                                )
                            except Exception as e:
                                logger.error(f"[Profiles R3] Failed to upsert vector for {profile_key}: {e}", exc_info=True)
                                index_error_code = "VECTOR_UPSERT_ERROR"
                                index_error_preview = str(e)[:500]
                        else:
                            logger.error(f"[Profiles R3] Embedding generation returned empty vector for {profile_key}")
                            index_error_code = "EMBEDDING_EMPTY_VECTOR"
                            index_error_preview = "Embedding provider returned empty vector"

            except Exception as e:
                logger.error(f"[Profiles R3] Failed to index profile embedding for {profile_key}: {e}", exc_info=True)
                index_error_code = "INDEXING_ERROR"
                index_error_preview = str(e)[:500]

        # Build response with OpenAPI P1 contract-aligned fields
        # Note: embedding indexing (if enabled) is performed as side effect above
        # Response follows OpenAPI ActivateResponse schema
        response = ActivateResponse(
            worker_id=worker_id,
            profile_id=profile_id,
            is_active=True,
            binding_updated=binding_updated,
            worker_updated=False,  # Worker record not updated in activate flow
            message=f"Profile {profile_id} activated successfully for worker {worker_id}",
        )

        logger.info(
            f"[OPENAPI-P1-PROFILE-TRACE] stage=activate_profile "
            f"worker_id={worker_id} profile_id={profile_id} "
            f"is_active=True binding_updated={binding_updated} worker_updated=False "
            f"embedding_index_requested={embedding_index_requested} "
            f"embedding_indexed={embedding_indexed} vector_upserted={vector_upserted}"
        )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Profiles R3] Failed to activate profile {profile_id} for {worker_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "ACTIVATE_PROFILE_ERROR", "message": str(e)}
        )


# =============================================================================
# P2 Useful Routes - Profile Management (5 routes)
# =============================================================================

@mgmt_router.get(
    "/workers/profiles/active-profiles",
    summary="Get all active profiles",
    description="Get all currently active profiles.",
    response_model=ActiveProfilesResponse,
    tags=["Profiles"],
)
async def get_active_profiles(request: Request):
    """P2: Get all active profiles."""
    _require_auth(request)

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
        active_profiles = profile_store.get_active_profiles()

        items = []
        for profile in (active_profiles or []):
            if isinstance(profile, dict):
                worker_id = profile.get('worker_id', '')
                profile_id = profile.get('profile_id', '')
                display_name = profile.get('metadata', {}).get('display_name')
            else:
                worker_id = getattr(profile, 'worker_id', '')
                profile_id = getattr(profile, 'profile_id', '')
                display_name = None

            items.append(ActiveProfileItem(
                profile_key=f"{worker_id}:{profile_id}",
                worker_id=worker_id,
                profile_id=profile_id,
                display_name=display_name,
                is_active=True,
            ))

        return ActiveProfilesResponse(
            profiles=items,
            total=len(items),
        )
    except Exception as e:
        logger.error(f"[Profiles R3] Failed to get active profiles: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "GET_ACTIVE_PROFILES_ERROR", "message": str(e)}
        )


@mgmt_router.post(
    "/workers/profiles/search",
    summary="Search profiles",
    description="Search profiles by content (minimal keyword matching).",
    response_model=ProfileSearchResponse,
    tags=["Profiles"],
)
async def search_profiles(request: Request, req: ProfileSearchRequest):
    """P2: Search profiles (minimal implementation)."""
    _require_auth(request)

    worker_store = _get_worker_registry_store(request)
    profile_store = _get_profile_content_store(request)

    if worker_store is None or profile_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_registry_store or worker_profile_content_store not available"
            }
        )

    try:
        # Minimal search: keyword matching
        # Get all workers, then search their profiles
        workers = worker_store.list(limit=1000)
        results = []
        query_lower = req.query.lower()

        for worker in workers:
            worker_id = worker.id if hasattr(worker, 'id') else worker.get('id')
            profiles = profile_store.list_profiles(worker_id)

            for profile in (profiles or []):
                if isinstance(profile, dict):
                    content = profile.get('content', '')
                    profile_id = profile.get('profile_id', 'default')
                else:
                    content = getattr(profile, 'content', '')
                    profile_id = getattr(profile, 'profile_id', 'default')

                # Simple keyword match
                if query_lower in content.lower():
                    results.append(ProfileSearchResult(
                        profile_key=f"{worker_id}:{profile_id}",
                        worker_id=worker_id,
                        score=0.8,  # Fixed score for minimal implementation
                        matched_content=content[:200] if content else None,
                        highlights=[],
                    ))

                    if len(results) >= req.top_k:
                        break

            if len(results) >= req.top_k:
                break

        return ProfileSearchResponse(
            results=results[:req.top_k],
            total=len(results),
            query=req.query,
            search_type="keyword",
            trace_id="",
        )
    except Exception as e:
        logger.error(f"[Profiles R3] Failed to search profiles: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "SEARCH_PROFILES_ERROR", "message": str(e)}
        )


@mgmt_router.get(
    "/workers/{worker_id}/profiles/{profile_id}/quality",
    summary="Get profile quality",
    description="Get quality metrics for a specific profile.",
    response_model=ProfileQualityResponse,
    tags=["Profiles"],
)
async def get_profile_quality(worker_id: str, profile_id: str, request: Request):
    """P2: Get profile quality."""
    _require_auth(request)

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
        profile = profile_store.get_profile(worker_id, profile_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PROFILE_NOT_FOUND", "message": f"Profile {profile_id} not found for worker {worker_id}"}
            )

        # Minimal quality metrics
        if isinstance(profile, dict):
            content = profile.get('content', '')
            metadata = profile.get('metadata', {})
        else:
            content = getattr(profile, 'content', '')
            metadata = getattr(profile, 'metadata', {})

        # Calculate minimal quality score based on content length
        content_score = min(len(content) / 1000.0, 1.0) if content else 0.0
        metadata_score = 0.5 if metadata else 0.0
        overall_score = (content_score * 0.7) + (metadata_score * 0.3)

        scores = [
            ProfileQualityScore(
                dimension="content_length",
                score=content_score,
                weight=0.7,
                details={"length": len(content)},
            ),
            ProfileQualityScore(
                dimension="metadata_completeness",
                score=metadata_score,
                weight=0.3,
                details={"has_metadata": bool(metadata)},
            ),
        ]

        return ProfileQualityResponse(
            profile_key=f"{worker_id}:{profile_id}",
            worker_id=worker_id,
            overall_score=overall_score,
            scores=scores,
            recommendations=[],
            last_analyzed=None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Profiles R3] Failed to get quality for {profile_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "GET_PROFILE_QUALITY_ERROR", "message": str(e)}
        )


@admin_router.post(
    "/workers/{worker_id}/profiles/{profile_id}/analyze",
    summary="Analyze profile",
    description="Analyze profile capabilities and quality.",
    response_model=ProfileAnalyzeResponse,
    tags=["Profiles"],
)
async def analyze_profile(worker_id: str, profile_id: str, request: Request, req: ProfileAnalyzeRequest):
    """P2: Analyze profile (minimal implementation)."""
    _require_auth(request)

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
        profile = profile_store.get_profile(worker_id, profile_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PROFILE_NOT_FOUND", "message": f"Profile {profile_id} not found for worker {worker_id}"}
            )

        # Minimal analysis: basic stats
        if isinstance(profile, dict):
            content = profile.get('content', '')
            metadata = profile.get('metadata', {})
        else:
            content = getattr(profile, 'content', '')
            metadata = getattr(profile, 'metadata', {})

        # Calculate basic metrics
        word_count = len(content.split()) if content else 0
        quality_score = min(word_count / 500.0, 1.0)  # Normalize to 0-1
        completeness = 0.5 if metadata else 0.3

        return ProfileAnalyzeResponse(
            profile_key=f"{worker_id}:{profile_id}",
            worker_id=worker_id,
            analyze_type=req.analyze_type,
            capabilities=[
                ProfileCapabilityAnalysis(
                    capability="general",
                    confidence=0.5,
                    evidence=["Basic profile analysis"],
                ),
            ],
            quality_score=quality_score,
            completeness=completeness,
            suggestions=["Add more detailed content to improve profile quality"],
            analysis_metadata={
                "word_count": word_count,
                "has_metadata": bool(metadata),
                "analyze_type": req.analyze_type,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Profiles R3] Failed to analyze profile {profile_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "ANALYZE_PROFILE_ERROR", "message": str(e)}
        )


@mgmt_router.patch(
    "/workers/{worker_id}/profiles/{profile_id}",
    summary="Patch profile",
    description="Partial update of profile fields with merge/delete semantics.",
    response_model=ProfileResponse,
    tags=["Profiles"],
)
async def patch_profile(worker_id: str, profile_id: str, request: Request, req: ContractProfilePatchRequest):
    """
    P2: Patch profile with contract-aligned semantics.

    Merge semantics:
    - display_name, soul_md, agents_md, tools_md, boot_md, heartbeat_md: Replace if provided
    - contents: Merge (add/update keys, don't delete unprovided keys)
    - contents_delete: Delete specified keys from contents
    - skill_sets: Replace all if provided
    - metadata: Merge (add/update keys, don't delete unprovided keys)
    - metadata_delete: Delete specified keys from metadata
    - activate: Activate profile if True
    """
    _require_auth(request)

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
        profile = profile_store.get_profile(worker_id, profile_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PROFILE_NOT_FOUND", "message": f"Profile {profile_id} not found for worker {worker_id}"}
            )

        # Get current profile data
        if isinstance(profile, dict):
            current_data = profile
        elif hasattr(profile, 'model_dump'):
            current_data = profile.model_dump()
        elif hasattr(profile, 'dict'):
            current_data = profile.dict()
        else:
            current_data = dict(profile)

        # Track updated fields
        updated_fields = []

        # Apply scalar field updates (replace semantics)
        if req.display_name is not None:
            current_data['display_name'] = req.display_name
            updated_fields.append('display_name')

        if req.soul_md is not None:
            current_data['soul_md'] = req.soul_md
            updated_fields.append('soul_md')

        if req.agents_md is not None:
            current_data['agents_md'] = req.agents_md
            updated_fields.append('agents_md')

        if req.tools_md is not None:
            current_data['tools_md'] = req.tools_md
            updated_fields.append('tools_md')

        if req.boot_md is not None:
            current_data['boot_md'] = req.boot_md
            updated_fields.append('boot_md')

        if req.heartbeat_md is not None:
            current_data['heartbeat_md'] = req.heartbeat_md
            updated_fields.append('heartbeat_md')

        # Apply contents merge
        contents_added_or_updated = []
        contents_deleted = []

        if req.contents is not None:
            if 'contents' not in current_data:
                current_data['contents'] = {}
            current_data['contents'].update(req.contents)
            contents_added_or_updated = list(req.contents.keys())
            updated_fields.append('contents')

        # Apply contents_delete
        if req.contents_delete is not None and len(req.contents_delete) > 0:
            if 'contents' not in current_data:
                current_data['contents'] = {}
            for key in req.contents_delete:
                if key in current_data['contents']:
                    del current_data['contents'][key]
                    contents_deleted.append(key)
            if contents_deleted:
                updated_fields.append('contents_delete')

        # Apply skill_sets (replace semantics)
        if req.skill_sets is not None:
            current_data['skill_sets'] = [s.model_dump() if hasattr(s, 'model_dump') else dict(s) for s in req.skill_sets]
            updated_fields.append('skill_sets')

        # Apply metadata merge
        metadata_added_or_updated = []
        metadata_deleted = []

        if req.metadata is not None:
            if 'metadata' not in current_data:
                current_data['metadata'] = {}
            current_data['metadata'].update(req.metadata)
            metadata_added_or_updated = list(req.metadata.keys())
            updated_fields.append('metadata')

        # Apply metadata_delete
        if req.metadata_delete is not None and len(req.metadata_delete) > 0:
            if 'metadata' not in current_data:
                current_data['metadata'] = {}
            for key in req.metadata_delete:
                if key in current_data['metadata']:
                    del current_data['metadata'][key]
                    metadata_deleted.append(key)
            if metadata_deleted:
                updated_fields.append('metadata_delete')

        # Update timestamp
        current_data['updated_at'] = datetime.utcnow().isoformat()

        # Update version
        version = current_data.get('version', 1) + 1
        current_data['version'] = version

        # Handle activation
        is_active = current_data.get('is_active', False)
        if req.activate:
            profile_store.activate_profile(worker_id, profile_id)
            is_active = True
            current_data['is_active'] = True
            updated_fields.append('activate')

            # Sync profile binding
            binding_store = _get_profile_binding_store(request)
            if binding_store:
                profile_key = f"{worker_id}:{profile_id}"
                try:
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
                except Exception as e:
                    logger.warning(f"[PROFILE_ACTIVATE_SIDE_EFFECT] Binding failed: {e}")

            # Sync worker active_profile_key (aligned with internal profile_routes.py)
            profile_key = f"{worker_id}:{profile_id}"
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

        logger.info(
            f"[PROFILE_PATCH_MERGE] worker_id={worker_id}, profile_id={profile_id}, "
            f"contents_added_or_updated={contents_added_or_updated}, "
            f"contents_deleted={contents_deleted}, "
            f"metadata_added_or_updated={metadata_added_or_updated}, "
            f"metadata_deleted={metadata_deleted}, "
            f"skill_sets_replaced={len(req.skill_sets) if req.skill_sets is not None else 'N/A'}"
        )

        # Save updated profile
        profile_store.upsert_profile(worker_id, profile_id, current_data)

        # Eager indexing if enabled
        if os.getenv("ENABLE_EAGER_INDEXING", "false").lower() == "true":
            import time
            start = time.time()
            try:
                index_adapter = _get_index_sync_adapter(request)
                if index_adapter:
                    profile_key = f"{worker_id}:{profile_id}"
                    index_adapter.sync_profile(worker_id, profile_key)
                    elapsed_ms = int((time.time() - start) * 1000)
                    logger.info(
                        f"[PROFILE_EAGER_INDEX_RESULT] worker_id={worker_id}, "
                        f"profile_id={profile_id}, profile_key={profile_key}, "
                        f"result=success, elapsed_ms={elapsed_ms}"
                    )
            except Exception as e:
                logger.warning(f"[PROFILE_EAGER_INDEX_RESULT] Failed: {e}")

        # Return flat ProfileResponse
        return ProfileResponse(
            worker_id=worker_id,
            profile_id=profile_id,
            display_name=current_data.get('display_name'),
            soul_md=current_data.get('soul_md'),
            agents_md=current_data.get('agents_md'),
            tools_md=current_data.get('tools_md'),
            boot_md=current_data.get('boot_md'),
            heartbeat_md=current_data.get('heartbeat_md'),
            contents=current_data.get('contents', {}),
            skill_sets=current_data.get('skill_sets', []),
            metadata=current_data.get('metadata', {}),
            content_type=current_data.get('content_type', 'api'),
            is_active=is_active,
            version=version,
            created_at=current_data.get('created_at'),
            updated_at=current_data.get('updated_at'),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Profiles R3] Failed to patch profile {profile_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "PATCH_PROFILE_ERROR", "message": str(e)}
        )


# =============================================================================
# Diagnostics Endpoint — REMOVED
# =============================================================================
# The /__diagnostics/open-core/runtime-fingerprint endpoint has been removed.
# It leaked runtime internals (module SHA256, provider keys, PYTHONPATH, CWD)
# and was not needed for external API exposure.

# =============================================================================
# LLM Profile Analysis (aligned with internal worker_routes.py)
# =============================================================================

_profile_analyzer = None


def _get_profile_analyzer():
    """获取 Profile Analyzer Service 单例（复用 profile_routes.py 实现）"""
    global _profile_analyzer
    if _profile_analyzer is None:
        try:
            from src.interfaces.api.dependencies.fusion_dependencies import _get_llm_gateway_service
            from src.application.services.profile_analyzer_service import ProfileAnalyzerService

            gateway = _get_llm_gateway_service()
            if gateway:
                try:
                    from src.interfaces.api.dependencies.worker_dependencies import _get_bot_cognition_provider
                    cognition_provider = _get_bot_cognition_provider()
                except Exception:
                    cognition_provider = None
                _profile_analyzer = ProfileAnalyzerService(
                    llm_gateway=gateway,
                    cognition_provider=cognition_provider,
                )
                logger.info("[R3-ProfileAnalyzer] Initialized with LLM Gateway")
            else:
                logger.warning("[R3-ProfileAnalyzer] LLM Gateway not available, analysis disabled")
        except Exception as e:
            logger.warning(f"[R3-ProfileAnalyzer] Init failed: {e}")
    return _profile_analyzer


def _generate_fallback_profile(req, merged_contents: dict) -> str | None:
    """
    从 sync 请求内容生成兜底 profile，确保向量索引有高权重 fragment。

    当调用方未传 contents["profile"] 时，从 soul_md / name / description 提取。
    LLM 分析完成后会替换此兜底值。

    Args:
        req: WorkerSyncRequest
        merged_contents: 已合并的 contents dict

    Returns:
        兜底 profile 文本，或 None
    """
    parts = []
    if req.name:
        parts.append(f"名称: {req.name}")
    if req.description:
        parts.append(f"描述: {req.description}")
    if req.profile.display_name:
        parts.append(f"显示名: {req.profile.display_name}")
    if req.profile.soul_md:
        parts.append(req.profile.soul_md[:2000])

    # 如果都没传，用 merged_contents 的文本值
    if not parts:
        for v in merged_contents.values():
            if isinstance(v, str) and v.strip():
                parts.append(v.strip()[:500])

    return "\n\n".join(parts) if parts else None


async def _analyze_and_persist_async(
    worker_id: str,
    profile_id: str,
    profile_data: dict,
    max_retries: int = 2,
) -> None:
    """
    异步后台任务：LLM 分析 + 写回 profile + 触发向量重建

    对齐内部版 worker_routes.py 的 _analyze_and_persist_async / _analyze_and_persist_sync。
    使用线程池限制并发（最多 4 个线程）。

    Args:
        worker_id: Worker ID
        profile_id: Profile ID
        profile_data: 传入的 profile 数据 dict（含 contents）
        max_retries: 最大重试次数
    """
    import asyncio
    import time

    for attempt in range(max_retries + 1):
        try:
            # Step 1: LLM 分析
            analyzer = _get_profile_analyzer()
            if not analyzer:
                logger.warning(f"[BG-LLM][{worker_id}] Analyzer not available, skip")
                return

            # 构建 WorkerProfileContent 供 analyzer 使用
            from src.domain.models.worker_profile_content import WorkerProfileContent

            contents = profile_data.get("contents", {})
            content = WorkerProfileContent(
                worker_id=worker_id,
                profile_id=profile_id,
                soul_md=profile_data.get("soul_md") or profile_data.get("content", ""),
                agents_md="",
                tools_md="",
                boot_md="",
                metadata=profile_data.get("metadata", {}),
                contents=contents,
            )

            logger.info(
                f"[BG-LLM][{worker_id}] Starting analysis (attempt {attempt + 1}/{max_retries + 1})"
            )

            # 在线程池中执行同步 LLM 调用
            loop = asyncio.get_event_loop()
            analysis = await loop.run_in_executor(None, analyzer.analyze, content)

            if not analysis.llm_success:
                logger.warning(
                    f"[BG-LLM][{worker_id}] Analysis failed: {analysis.error_message}"
                )
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                    continue
                return

            # Step 2: 写回 contents
            logger.info(
                f"[BG-LLM][{worker_id}] Writing analysis results, "
                f"tags={analysis.capability_tags}"
            )

            contents["profile"] = analysis.semantic_profile
            contents["capabilities"] = analysis.capability_tags
            contents["short_profile"] = analysis.short_profile

            profile_data["contents"] = contents
            profile_data["updated_at"] = datetime.utcnow().isoformat()

            # 重新获取 profile_store 并更新（后台任务无 request，从 app context 获取）
            from src.interfaces.api.dependencies.fusion_dependencies import (
                get_app_context,
            )
            _ctx = get_app_context()
            profile_store = None
            if _ctx and hasattr(_ctx, 'registry'):
                profile_store = _ctx.registry.get('worker_profile_content_store')
            if profile_store:
                profile_store.upsert_profile(worker_id, profile_id, profile_data)
                logger.info(
                    f"[BG-LLM][{worker_id}] Profile persisted successfully, "
                    f"profile_len={len(analysis.semantic_profile or '')}"
                )

                # Step 3: 触发向量重建（只重算变更的 profile/capabilities fragments）
                try:
                    from src.interfaces.api.dependencies.fusion_dependencies import (
                        _build_vector_index_for_worker,
                    )
                    _build_vector_index_for_worker(worker_id)
                    logger.info(f"[BG-LLM][{worker_id}] Vector rebuild triggered")
                except Exception as e_vec:
                    logger.warning(f"[BG-LLM][{worker_id}] Vector rebuild failed: {e_vec}")

                # 重置服务缓存
                try:
                    from src.interfaces.api.profile_routes import _trigger_index_sync
                    _trigger_index_sync(worker_id)
                except Exception:
                    pass

            return  # 成功完成

        except Exception as e:
            logger.error(
                f"[BG-LLM][{worker_id}] Background task failed "
                f"(attempt {attempt + 1}): {e}",
                exc_info=True,
            )
            if attempt < max_retries:
                import time
                time.sleep(2 ** attempt)
            else:
                logger.error(f"[BG-LLM][{worker_id}] All retries exhausted")


# =============================================================================
# =============================================================================
# Vector Store Payload Sync
# =============================================================================

def _sync_availability_to_vector_store(worker_id: str, availability: str) -> None:
    """Update availability field in vector payload so search filters work.

    When PUT /availability updates the DB, the vector payload still holds
    the old value. Search uses payload filters, so without this sync the
    filter would be stale.

    Supports both Qdrant (native set_payload) and Faiss (direct metadata
    update via update_payload_by_worker).

    Non-critical: errors are logged but do not fail the HTTP request.
    """
    try:
        from src.interfaces.api.dependencies.fusion_dependencies import _get_vector_match_service
        service = _get_vector_match_service()
        if service is None or service._vector_store is None:
            logger.warning(
                "[AVAILABILITY-VECTORSYNC] vector_store not available, "
                "skipping payload update for worker=%s", worker_id
            )
            return

        vector_store = service._vector_store

        # ---- Faiss path: direct payload update ----
        if hasattr(vector_store, 'update_payload_by_worker'):
            count = vector_store.update_payload_by_worker(
                worker_id, {"availability": availability},
            )
            logger.info(
                "[AVAILABILITY-VECTORSYNC] Faiss: updated availability=%s for "
                "worker=%s, fragments_updated=%d", availability, worker_id, count,
            )
            return

        # ---- Qdrant path: native set_payload ----
        # Ensure client is initialized
        vector_store._ensure_client()
        client = vector_store._client
        collection = vector_store.collection_name

        # Scroll all point IDs for this worker, then batch set_payload
        from qdrant_client.models import Filter, FieldCondition, MatchText

        worker_filter = Filter(
            must=[
                FieldCondition(key="worker_id", match=MatchText(text=worker_id))
            ]
        )

        # Collect all point IDs for this worker
        point_ids = []
        offset = None
        while True:
            records, offset = client.scroll(
                collection,
                scroll_filter=worker_filter,
                limit=100,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            point_ids.extend([r.id for r in records])
            if offset is None or not records:
                break

        if not point_ids:
            logger.info(
                "[AVAILABILITY-VECTORSYNC] No vectors found for worker=%s, skip", worker_id
            )
            return

        # Batch update availability in payload
        client.set_payload(
            collection_name=collection,
            payload={"availability": availability},
            points=point_ids,
            wait=True,
        )

        logger.info(
            "[AVAILABILITY-VECTORSYNC] Qdrant: updated availability=%s for worker=%s, "
            "fragments_updated=%d", availability, worker_id, len(point_ids)
        )
    except Exception as e:
        logger.warning(
            "[AVAILABILITY-VECTORSYNC] Failed to sync availability to vector store "
            "for worker=%s: %s", worker_id, e
        )


def _sync_runtime_state_to_vector_store(worker_id: str, runtime_state: str) -> None:
    """Update runtime_state field in vector payload so search filters work.

    When PUT /online or /offline updates the DB, the vector payload still holds
    the old value. Search uses payload filters (e.g. {"runtime_state": ["online"]}),
    so without this sync the filter would be stale.

    Supports both Qdrant (native set_payload) and Faiss (direct metadata
    update via update_payload_by_worker).

    Non-critical: errors are logged but do not fail the HTTP request.
    """
    try:
        from src.interfaces.api.dependencies.fusion_dependencies import _get_vector_match_service
        service = _get_vector_match_service()
        if service is None or service._vector_store is None:
            logger.warning(
                "[RUNTIMESTATE-VECTORSYNC] vector_store not available, "
                "skipping payload update for worker=%s", worker_id
            )
            return

        vector_store = service._vector_store

        # ---- Faiss path: direct payload update ----
        if hasattr(vector_store, 'update_payload_by_worker'):
            count = vector_store.update_payload_by_worker(
                worker_id, {"runtime_state": runtime_state},
            )
            logger.info(
                "[RUNTIMESTATE-VECTORSYNC] Faiss: updated runtime_state=%s for "
                "worker=%s, fragments_updated=%d", runtime_state, worker_id, count,
            )
            return

        # ---- Qdrant path: native set_payload ----
        vector_store._ensure_client()
        client = vector_store._client
        collection = vector_store.collection_name

        from qdrant_client.models import Filter, FieldCondition, MatchText

        worker_filter = Filter(
            must=[
                FieldCondition(key="worker_id", match=MatchText(text=worker_id))
            ]
        )

        point_ids = []
        offset = None
        while True:
            records, offset = client.scroll(
                collection,
                scroll_filter=worker_filter,
                limit=100,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            point_ids.extend([r.id for r in records])
            if offset is None or not records:
                break

        if not point_ids:
            logger.info(
                "[RUNTIMESTATE-VECTORSYNC] No vectors found for worker=%s, skip",
                worker_id,
            )
            return

        client.set_payload(
            collection_name=collection,
            payload={"runtime_state": runtime_state},
            points=point_ids,
            wait=True,
        )

        logger.info(
            "[RUNTIMESTATE-VECTORSYNC] Qdrant: updated runtime_state=%s for "
            "worker=%s, fragments_updated=%d",
            runtime_state, worker_id, len(point_ids),
        )
    except Exception as e:
        logger.warning(
            "[RUNTIMESTATE-VECTORSYNC] Failed to sync runtime_state to vector "
            "store for worker=%s: %s", worker_id, e,
        )


# =============================================================================
# LLM Profile Analysis (aligned with internal worker_routes.py)
# =============================================================================

_profile_analyzer = None


def _get_profile_analyzer():
    """获取 Profile Analyzer Service 单例（复用 profile_routes.py 实现）"""
    global _profile_analyzer
    if _profile_analyzer is None:
        try:
            from src.interfaces.api.dependencies.fusion_dependencies import _get_llm_gateway_service
            from src.application.services.profile_analyzer_service import ProfileAnalyzerService

            gateway = _get_llm_gateway_service()
            if gateway:
                try:
                    from src.interfaces.api.dependencies.worker_dependencies import _get_bot_cognition_provider
                    cognition_provider = _get_bot_cognition_provider()
                except Exception:
                    cognition_provider = None
                _profile_analyzer = ProfileAnalyzerService(
                    llm_gateway=gateway,
                    cognition_provider=cognition_provider,
                )
                logger.info("[R3-ProfileAnalyzer] Initialized with LLM Gateway")
            else:
                logger.warning("[R3-ProfileAnalyzer] LLM Gateway not available, analysis disabled")
        except Exception as e:
            logger.warning(f"[R3-ProfileAnalyzer] Init failed: {e}")
    return _profile_analyzer


def _generate_fallback_profile(req, merged_contents: dict) -> str | None:
    """
    从 sync 请求内容生成兜底 profile，确保向量索引有高权重 fragment。

    当调用方未传 contents["profile"] 时，从 soul_md / name / description 提取。
    LLM 分析完成后会替换此兜底值。

    Args:
        req: WorkerSyncRequest
        merged_contents: 已合并的 contents dict

    Returns:
        兜底 profile 文本，或 None
    """
    parts = []
    if req.name:
        parts.append(f"名称: {req.name}")
    if req.description:
        parts.append(f"描述: {req.description}")
    if req.profile.display_name:
        parts.append(f"显示名: {req.profile.display_name}")
    if req.profile.soul_md:
        parts.append(req.profile.soul_md[:2000])

    # 如果都没传，用 merged_contents 的文本值
    if not parts:
        for v in merged_contents.values():
            if isinstance(v, str) and v.strip():
                parts.append(v.strip()[:500])

    return "\n\n".join(parts) if parts else None


async def _analyze_and_persist_async(
    worker_id: str,
    profile_id: str,
    profile_data: dict,
    max_retries: int = 2,
) -> None:
    """
    异步后台任务：LLM 分析 + 写回 profile + 触发向量重建

    对齐内部版 worker_routes.py 的 _analyze_and_persist_async / _analyze_and_persist_sync。
    使用线程池限制并发（最多 4 个线程）。

    Args:
        worker_id: Worker ID
        profile_id: Profile ID
        profile_data: 传入的 profile 数据 dict（含 contents）
        max_retries: 最大重试次数
    """
    import asyncio
    import time

    for attempt in range(max_retries + 1):
        try:
            # Step 1: LLM 分析
            analyzer = _get_profile_analyzer()
            if not analyzer:
                logger.warning(f"[BG-LLM][{worker_id}] Analyzer not available, skip")
                return

            # 构建 WorkerProfileContent 供 analyzer 使用
            from src.domain.models.worker_profile_content import WorkerProfileContent

            contents = profile_data.get("contents", {})
            content = WorkerProfileContent(
                worker_id=worker_id,
                profile_id=profile_id,
                soul_md=profile_data.get("soul_md") or profile_data.get("content", ""),
                agents_md="",
                tools_md="",
                boot_md="",
                metadata=profile_data.get("metadata", {}),
                contents=contents,
            )

            logger.info(
                f"[BG-LLM][{worker_id}] Starting analysis (attempt {attempt + 1}/{max_retries + 1})"
            )

            # 在线程池中执行同步 LLM 调用
            loop = asyncio.get_event_loop()
            analysis = await loop.run_in_executor(None, analyzer.analyze, content)

            if not analysis.llm_success:
                logger.warning(
                    f"[BG-LLM][{worker_id}] Analysis failed: {analysis.error_message}"
                )
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                    continue
                return

            # Step 2: 写回 contents
            logger.info(
                f"[BG-LLM][{worker_id}] Writing analysis results, "
                f"tags={analysis.capability_tags}"
            )

            contents["profile"] = analysis.semantic_profile
            contents["capabilities"] = analysis.capability_tags
            contents["short_profile"] = analysis.short_profile

            profile_data["contents"] = contents
            profile_data["updated_at"] = datetime.utcnow().isoformat()

            # 重新获取 profile_store 并更新（后台任务无 request，从 app context 获取）
            from src.interfaces.api.dependencies.fusion_dependencies import (
                get_app_context,
            )
            _ctx = get_app_context()
            profile_store = None
            if _ctx and hasattr(_ctx, 'registry'):
                profile_store = _ctx.registry.get('worker_profile_content_store')
            if profile_store:
                profile_store.upsert_profile(worker_id, profile_id, profile_data)
                logger.info(
                    f"[BG-LLM][{worker_id}] Profile persisted successfully, "
                    f"profile_len={len(analysis.semantic_profile or '')}"
                )

                # Step 3: 触发向量重建（只重算变更的 profile/capabilities fragments）
                try:
                    from src.interfaces.api.dependencies.fusion_dependencies import (
                        _build_vector_index_for_worker,
                    )
                    _build_vector_index_for_worker(worker_id)
                    logger.info(f"[BG-LLM][{worker_id}] Vector rebuild triggered")
                except Exception as e_vec:
                    logger.warning(f"[BG-LLM][{worker_id}] Vector rebuild failed: {e_vec}")

                # 重置服务缓存
                try:
                    from src.interfaces.api.profile_routes import _trigger_index_sync
                    _trigger_index_sync(worker_id)
                except Exception:
                    pass

            return  # 成功完成

        except Exception as e:
            logger.error(
                f"[BG-LLM][{worker_id}] Background task failed "
                f"(attempt {attempt + 1}): {e}",
                exc_info=True,
            )
            if attempt < max_retries:
                import time
                time.sleep(2 ** attempt)
            else:
                logger.error(f"[BG-LLM][{worker_id}] All retries exhausted")


# =============================================================================
# Route Mounting Helper
# =============================================================================

def include_r3_routes(app) -> None:
    """
    Mount R3 worker/profile routes into FastAPI application.

    Route categories:
    - api_router: External product APIs at /api/v1 (sync, availability — for 3rd-party callers)
    - mgmt_router: Management platform APIs at /v1 (trust-level, profiles, config reads — for admin portal)
    - compat_router: Backward-compatible alias at /v1 (sync — deprecated, for existing callers like BCS)
    - admin_router: Privileged admin APIs at /v1/admin (only when BCSFUSE_EXPOSE_ADMIN=true)

    These routes MUST be mounted BEFORE skeleton routes to avoid shadowing.

    Args:
        app: FastAPI application instance
    """
    # External product APIs — always exposed at /api/v1
    app.include_router(api_router, prefix="/api/v1", tags=["R3-Worker-Profile"])

    # Management platform APIs — always exposed at /v1
    app.include_router(mgmt_router, prefix="/v1", tags=["R3-Management"])

    # Backward-compatible routes — mounted at /v1 (deprecated)
    app.include_router(compat_router, prefix="/v1", tags=["R3-Compat"])

    # High-risk admin APIs — conditionally exposed at /v1/admin
    if os.getenv("BCSFUSE_EXPOSE_ADMIN", "false").lower() == "true":
        app.include_router(admin_router, prefix="/v1/admin", tags=["R3-Admin"])
        logger.info("[R3 Routes] R3 admin routes mounted at /v1/admin")
    else:
        logger.info("[R3 Routes] R3 admin routes NOT mounted (set BCSFUSE_EXPOSE_ADMIN=true to enable)")

    logger.info("[R3 Routes] R3 worker/profile routes mounted successfully")