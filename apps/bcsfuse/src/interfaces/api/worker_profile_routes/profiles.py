"""Worker/Profile compatibility routes: profiles."""

import logging
from fastapi import Request, HTTPException, status
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
    ActivateResponse,
)

from . import common

logger = logging.getLogger(__name__)


@common.mgmt_router.put(
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
    common._require_auth(request)

    profile_store = common._get_profile_content_store(request)

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
            binding_store = common._get_profile_binding_store(request)
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
                            worker_registry_store = common._get_worker_registry_store(request)
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


@common.mgmt_router.get(
    "/workers/profiles/active-profiles",
    summary="Get all active profiles",
    description="Get all currently active profiles.",
    response_model=ActiveProfilesResponse,
    tags=["Profiles"],
)
async def get_active_profiles(request: Request):
    """P2: Get all active profiles."""
    common._require_auth(request)

    profile_store = common._get_profile_content_store(request)

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


@common.mgmt_router.post(
    "/workers/profiles/search",
    summary="Search profiles",
    description="Search profiles by content (minimal keyword matching).",
    response_model=ProfileSearchResponse,
    tags=["Profiles"],
)
async def search_profiles(request: Request, req: ProfileSearchRequest):
    """P2: Search profiles (minimal implementation)."""
    common._require_auth(request)

    worker_store = common._get_worker_registry_store(request)
    profile_store = common._get_profile_content_store(request)

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


@common.mgmt_router.get(
    "/workers/{worker_id}/profiles/{profile_id}/quality",
    summary="Get profile quality",
    description="Get quality metrics for a specific profile.",
    response_model=ProfileQualityResponse,
    tags=["Profiles"],
)
async def get_profile_quality(worker_id: str, profile_id: str, request: Request):
    """P2: Get profile quality."""
    common._require_auth(request)

    profile_store = common._get_profile_content_store(request)

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


@common.admin_router.post(
    "/workers/{worker_id}/profiles/{profile_id}/analyze",
    summary="Analyze profile",
    description="Analyze profile capabilities and quality.",
    response_model=ProfileAnalyzeResponse,
    tags=["Profiles"],
)
async def analyze_profile(worker_id: str, profile_id: str, request: Request, req: ProfileAnalyzeRequest):
    """P2: Analyze profile (minimal implementation)."""
    common._require_auth(request)

    profile_store = common._get_profile_content_store(request)

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
