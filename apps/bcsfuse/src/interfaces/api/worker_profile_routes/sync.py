"""Worker/Profile compatibility routes: sync."""

import logging
from datetime import datetime
from fastapi import Request, HTTPException, status
from src.interfaces.api.schemas.worker_management_schemas import (
    WorkerSyncRequest,
    WorkerSyncResponse,
    Availability,
    TrustLevel,
)

from . import analysis
from . import common
from . import vector_sync

logger = logging.getLogger(__name__)


@common.api_router.post(
    "/workers/{worker_id}/sync",
    summary="Sync worker",
    description="Sync worker and profile; preserve existing runtime state when omitted.",
    response_model=WorkerSyncResponse,
    tags=["Workers"],
)
async def sync_worker(worker_id: str, request: Request, req: WorkerSyncRequest):
    """
    P1: Sync worker and profile without changing omitted runtime state.

    Atomic sync operation aligned with root_original contract:
    - Create or update worker
    - Apply explicit runtime_state; otherwise preserve it (new workers default online)
    - Upsert and activate profile
    - Return canonical response schema
    """
    common._require_auth(request)

    worker_store = common._get_worker_registry_store(request)
    profile_store = common._get_profile_content_store(request)
    runtime_state_store = common._get_runtime_state_store(request)

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
    effective_runtime_state = req.runtime_state

    try:
        from src.domain.models.worker import Worker, WorkerType, WorkerIdentity, WorkerState
        from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
        from src.domain.models.worker_source_info import WorkerSourceType
        from src.domain.models.worker_runtime_state import WorkerRuntimeState

        # Step 1: Create or update worker
        existing = worker_store.get_by_id(worker_id)
        update_runtime_state = existing is None or req.runtime_state is not None
        if effective_runtime_state is None:
            if existing is None:
                effective_runtime_state = "online"
            else:
                # Read for response/analysis only. Do not write a snapshot back:
                # user publication owns runtime state independently of visibility.
                stored_state = (
                    runtime_state_store.get_runtime_state(worker_id)
                    if runtime_state_store is not None else None
                )
                current_state = stored_state if stored_state is not None else existing.state.runtime_state
                # The in-memory store retains the mapping written by this route;
                # persistent stores return the runtime-state enum.
                if isinstance(current_state, dict):
                    current_state = current_state["state"]
                effective_runtime_state = WorkerRuntimeState(current_state).value

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
            active_profile_key = (
                req.profile_key or f"{worker_id}:{profile_id}"
            ) if req.profile.activate else None

            worker = Worker(
                id=worker_id,
                type=WorkerType(req.type),
                identity=WorkerIdentity(
                    name=req.name,
                    handle=f"@{worker_id}",  # Generate handle from worker_id (no identity_handle in SyncWorkerRequest)
                    description=req.description,
                    title=None,  # No identity_title in SyncWorkerRequest
                ),
                responsibilities=req.responsibilities if hasattr(req, 'responsibilities') else [],
                domains=req.domains,
                capabilities=req.capabilities or [
                    {"name": "general", "level": "intermediate"},
                ],
                skills=req.skills,
                resources=[],
                state=WorkerState(
                    availability=availability,  # Phase 2.6.5: Use availability from request
                    trust_level=TrustLevel(req.trust_level),
                ),
                # Match the internal import contract: a successfully synced
                # Worker is visible to registry-aware recommendation as soon
                # as the create commits. The later lifecycle write remains for
                # existing Workers and must not be the only activation write.
                lifecycle_state=WorkerLifecycleState.ACTIVE,
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
            worker_dict["responsibilities"] = req.responsibilities
            worker_dict["domains"] = req.domains
            # Phase 2.6.5: Update availability from request
            worker_dict["state"]["availability"] = availability
            # Update active_profile_key
            if req.profile.activate:
                worker_dict['active_profile_key'] = (
                    req.profile_key or f"{worker_id}:{profile_id}"
                )

            from src.domain.models.worker import Worker as WorkerModel
            updated_worker = WorkerModel.model_validate(worker_dict)
            worker_store.update(updated_worker)
            logger.info(f"[Workers R3 Sync] Worker updated: {worker_id}, availability={availability.value}")

        # Step 2: Only explicit changes or new workers write runtime state.
        try:
            if runtime_state_store is not None and update_runtime_state:
                from src.domain.models.worker_runtime_state import WorkerRuntimeState

                previous_runtime_state = runtime_state_store.get_runtime_state(
                    worker_id
                )
                if isinstance(previous_runtime_state, dict):
                    previous_runtime_state = previous_runtime_state.get("state")
                if previous_runtime_state is None:
                    previous_runtime_state = (
                        existing.state.runtime_state
                        if existing is not None
                        else WorkerRuntimeState.OFFLINE
                    )
                previous_runtime_state = WorkerRuntimeState(
                    previous_runtime_state
                )

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
                    if hasattr(current_worker.state, 'runtime_state'):
                        try:
                            worker_dict = current_worker.model_dump() if hasattr(current_worker, 'model_dump') else current_worker.dict()
                            worker_dict['state']['runtime_state'] = effective_runtime_state
                            from src.domain.models.worker import Worker as WorkerModel
                            updated_state_worker = WorkerModel.model_validate(worker_dict)
                            worker_store.update(updated_state_worker)
                            logger.info(f"[Workers R3 Sync] Worker.state.runtime_state updated to {effective_runtime_state}")
                        except Exception as e_state:
                            try:
                                runtime_state_store.set_runtime_state(
                                    worker_id=worker_id,
                                    runtime_state=previous_runtime_state,
                                    updated_by="sync-worker-rollback",
                                )
                            except Exception as rollback_error:
                                logger.exception(
                                    "[Workers R3 Sync] Failed to roll back runtime "
                                    "state for %s: %s",
                                    worker_id,
                                    rollback_error,
                                )
                            raise RuntimeError(
                                "failed to persist worker runtime-state mirror"
                            ) from e_state

                    # Sync runtime_state + availability to vector store payloads (Faiss + Qdrant)
                    try:
                        vector_sync._sync_runtime_state_to_vector_store(worker_id, effective_runtime_state)
                        if isinstance(current_worker, dict):
                            avail = (current_worker.get("state") or {}).get("availability", "protected")
                        else:
                            avail = getattr(getattr(current_worker, 'state', None), 'availability', 'protected')
                            if hasattr(avail, 'value'):
                                avail = avail.value
                        vector_sync._sync_availability_to_vector_store(worker_id, avail)
                    except Exception as sync_err:
                        logger.warning(f"[Workers R3 Sync] Vector payload sync failed: {sync_err}")

            elif not update_runtime_state:
                logger.info(f"[Workers R3 Sync] Runtime state preserved: {worker_id} -> {effective_runtime_state}")
                # Availability still changed even though runtime state did not.
                try:
                    vector_sync._sync_availability_to_vector_store(worker_id, availability.value)
                except Exception as sync_err:
                    logger.warning(f"[Workers R3 Sync] Vector availability sync failed: {sync_err}")
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
            f"profile_key={worker_id}:{profile_id}, request_activate=True, runtime_state_requested={req.runtime_state}"
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
            f"[SYNC_RUNTIME_STATE_SET] worker_id={worker_id}, target_state={effective_runtime_state}, update_requested={update_runtime_state}, "
            f"runtime_state_store_class={type(runtime_state_store).__name__ if runtime_state_store else 'None'}, "
            f"success=True"
        )

        # Step 4: Upsert and activate profile (root_original contract)
        # Use profile data from request (req.profile) instead of req.profile_content
        logger.info(
            f"[SYNC_STEP_START] worker_id={worker_id}, profile_id={req.profile.profile_id}, "
            f"profile_key={worker_id}:{req.profile.profile_id}, request_activate={req.profile.activate}, runtime_state_requested={req.runtime_state}"
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
                    fallback = analysis._generate_fallback_profile(req, merged_contents)
                    if fallback:
                        merged_contents["profile"] = fallback
                        logger.info(
                            f"[SYNC_FALLBACK_PROFILE] worker_id={worker_id}, "
                            f"generated fallback profile, len={len(fallback)}"
                        )

                profile_data = {
                    "worker_id": worker_id,  # CRITICAL: Include worker_id for ProfileResponse
                    "profile_id": profile_id,  # CRITICAL: Include profile_id for ProfileResponse
                    "soul_md": req.profile.soul_md or "",  # SOUL.md content
                    "display_name": req.profile.display_name or req.name,
                    "description": req.description,
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
                binding_store = common._get_profile_binding_store(request)
                if req.profile.activate and binding_store is not None:
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
                elif req.profile.activate:
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
                            await analysis._analyze_and_persist_async(
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
                            analysis._analyze_and_persist_async(
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

        # Preserve the internal sync contract: when capability verification is
        # enabled, newly synced profile data must be re-verified before the
        # worker can retain a higher trust level. Verification remains
        # fail-open for registration, matching the previous OCB route.
        from src.application.utils.drm_config_helper import (
            is_capability_verify_enabled,
        )

        capability_verify_enabled = is_capability_verify_enabled()
        if capability_verify_enabled is None:
            from src.infra.config.feature_flags import FeatureFlags

            capability_verify_enabled = FeatureFlags.is_capability_verify_enabled()
        if capability_verify_enabled:
            try:
                from src.domain.events import (
                    WorkerProfileCreatedEvent,
                    get_event_bus,
                )
                from src.interfaces.api.dependencies.fusion_dependencies import (
                    get_capability_verify_service,
                )

                verify_service = get_capability_verify_service()
                if verify_service is not None:
                    if not verify_service._running:
                        await verify_service.start()
                        logger.info("[SYNC] CapabilityVerifyService lazy-started")

                    worker_store.update_trust_level(
                        worker_id,
                        TrustLevel.UNVERIFIED,
                    )
                    get_event_bus().publish(
                        WorkerProfileCreatedEvent(worker_id=worker_id)
                    )
                    logger.info(
                        "[SYNC] Capability verification scheduled: worker_id=%s",
                        worker_id,
                    )
            except Exception as verification_error:
                logger.warning(
                    "[SYNC] Capability verification setup failed for %s: %s",
                    worker_id,
                    verification_error,
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


@common.compat_router.post(
    "/workers/{worker_id}/sync",
    summary="Sync worker (backward-compatible)",
    description="Sync worker and profile; preserve existing runtime state when omitted. "
                "Legacy path at /v1 for backward compatibility; prefer /api/v1.",
    response_model=WorkerSyncResponse,
    tags=["Workers"],
    deprecated=True,
)
async def sync_worker_compat(worker_id: str, request: Request, req: WorkerSyncRequest):
    """Backward-compatible alias for sync_worker at /v1 prefix."""
    return await sync_worker(worker_id, request, req)
