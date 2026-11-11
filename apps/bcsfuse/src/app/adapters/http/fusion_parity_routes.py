"""
R5 Fusion Business Logic Routes (Runtime-Aware Wrapper)

Implements R5 fusion canonical route with runtime-aware routing:
- If ENABLE_REAL_LLM=true or LLM_ENABLED=true: delegates to real LLM fusion
- Otherwise: uses deterministic profile merge fallback

S28B-2B-15: Fusion Business Logic Implementation (Enhanced with Runtime-Aware Routing)

Runtime-Aware Policy:
- Checks ENABLE_REAL_LLM / LLM_ENABLED environment variables
- If real LLM enabled: delegates to fusion_routes.GroupFusionService
- If real LLM unavailable: falls back to deterministic profile merge
- Returns honest metadata about which implementation was used
- Fallback reason always included when not using real LLM

Routes implemented:
- P0 Critical: POST /groups/{group_id}/fuse (1 route)

Metadata Tags:
- route_impl: "real_llm_fusion" | "deterministic_parity_fallback"
- llm_enabled: true | false
- llm_flag_source: "ENABLE_REAL_LLM" | "LLM_ENABLED" | "none"
- llm_used: true | false
- fallback_reason: (only when using fallback)
"""

import logging
import os
import uuid
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException, status

from app.adapters.http.fusion_fallback import _create_minimal_perspective
from app.adapters.http.schemas.fusion_schemas import (
    FusionRequest,
    FusionResult,
    ConflictPoint,
    AlignmentPoint,
    RiskAssessment,
    TimingResponse,
)

logger = logging.getLogger(__name__)


async def _run_fuse(service, request, group_id: str):
    """Run the synchronous GroupFusionService.fuse() off the event loop.

    The R5 fusion handler is async; calling service.fuse() inline blocks the
    loop for the full fusion duration (up to 600s). run_in_threadpool keeps the
    gateway's concurrent requests from serializing on it.
    """
    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(service.fuse, request, group_id=group_id)


router = APIRouter()



def _get_worker_registry_store(request: Request):
    """Get worker registry store from provider registry."""
    return request.app.state.context.registry.get('worker_registry_store')


def _get_profile_content_store(request: Request):
    """Get profile content store from provider registry."""
    return request.app.state.context.registry.get('worker_profile_content_store')


def _require_auth(request: Request) -> None:
    """Require authentication for protected endpoints."""
    from app.tc.auth import require_oss_auth
    require_oss_auth(request)



@router.post(
    "/groups/{group_id}/fuse",
    response_model=FusionResult,
    status_code=status.HTTP_200_OK,
    summary="Fuse group participants (runtime-aware)",
    description="""
R5: Group fusion route (runtime-aware implementation).

**Runtime-Aware Routing**:
- If ENABLE_REAL_LLM=true or LLM_ENABLED=true: uses real LLM fusion
- Otherwise: falls back to deterministic profile merge

**Real LLM Mode** (when enabled):
- Full G1/G2/G5 pipeline
- LLM-based perspective generation
- Vector search and semantic analysis
- Conflict detection and alignment analysis
- Expert diagnosis

**Fallback Mode** (when LLM unavailable):
- Deterministic profile summary extraction
- No LLM-based perspective generation
- No vector search or embedding
- No conflict detection (G2 unavailable)
- No expert diagnosis (G5 unavailable)

**Metadata Tags**:
- route_impl: "real_llm_fusion" | "deterministic_parity_fallback"
- llm_enabled: true | false
- llm_flag_source: "ENABLE_REAL_LLM" | "LLM_ENABLED" | "none"
- llm_used: true | false
- fallback_reason: (only when using fallback)
""",
    tags=["Fusion"],
)
async def fuse_group(request: Request, group_id: str, req: FusionRequest):
    """
    P0: Fuse group participants with runtime-aware routing.

    Checks ENABLE_REAL_LLM/LLM_ENABLED and routes to:
    - Real LLM fusion service if enabled and available
    - Deterministic fallback otherwise

    Returns fusion result with metadata about implementation used.
    """
    _require_auth(request)

    canonical_enabled = os.environ.get("ENABLE_REAL_LLM", "").lower() == "true"
    legacy_enabled = os.environ.get("LLM_ENABLED", "").lower() == "true"
    llm_enabled = canonical_enabled or legacy_enabled
    llm_flag_source = "ENABLE_REAL_LLM" if canonical_enabled else "LLM_ENABLED" if legacy_enabled else "none"

    logger.info(
        f"[Fusion][R5] Runtime-aware routing: "
        f"ENABLE_REAL_LLM={canonical_enabled}, LLM_ENABLED={legacy_enabled}, "
        f"llm_enabled={llm_enabled}, source={llm_flag_source}"
    )

    if llm_enabled:
        try:
            logger.info("[Fusion][R5] Attempting to delegate to real LLM fusion service...")

            from app.adapters.http.fusion_routes import get_service as get_real_fusion_service
            from app.adapters.http.fusion_routes import FusionRequest as RealFusionRequest
            from app.infra.context import set_current_cookie

            cookie = request.headers.get("cookie", "")
            if cookie:
                set_current_cookie(cookie)
                logger.info(f"[Fusion][R5] Cookie set for BCN API calls (len={len(cookie)})")

            try:
                service = get_real_fusion_service()
                logger.info(f"[Fusion][R5] Real fusion service obtained: {type(service).__name__}")

                from app.adapters.http.dependencies.fusion_dependencies import get_availability_checker_from_request
                try:
                    oss_safe_checker = get_availability_checker_from_request(request)
                    service.set_availability_checker(oss_safe_checker)
                    logger.info(
                        f"[Fusion][R5] Injected OSS-safe availability_checker: "
                        f"registry_store_id={id(oss_safe_checker._registry_store) if oss_safe_checker._registry_store else 0}"
                    )
                except Exception as ac_err:
                    logger.error(
                        f"[Fusion][R5] Failed to inject OSS-safe availability_checker: {ac_err}",
                        exc_info=True
                    )
                    raise RuntimeError(f"Failed to create OSS-safe availability_checker: {ac_err}") from ac_err

                from app.adapters.http.dependencies.fusion_dependencies import (
                    get_profile_source_from_request,
                    _get_llm_gateway_service
                )
                try:
                    oss_safe_profile_source = get_profile_source_from_request(request)

                    oss_safe_gateway = _get_llm_gateway_service()

                    if oss_safe_gateway is None:
                        raise RuntimeError(
                            "LLM Gateway not available. "
                            "ENABLE_REAL_LLM=true requires LLM_BASE_URL and LLM_AUTH_TOKEN. "
                            "Cannot create LLMPerspectiveProvider."
                        )

                    from app.infra.providers.llm_perspective_provider import LLMPerspectiveProvider
                    oss_safe_perspective_provider = LLMPerspectiveProvider(
                        gateway=oss_safe_gateway,
                        profile_source=oss_safe_profile_source,
                    )

                    service.set_perspective_provider(oss_safe_perspective_provider)

                    logger.info(
                        f"[Fusion][R5] Injected OSS-safe perspective_provider: "
                        f"profile_source_type={type(oss_safe_profile_source).__name__}, "
                        f"gateway_type={type(oss_safe_gateway).__name__}"
                    )
                except Exception as pp_err:
                    logger.error(
                        f"[Fusion][R5] Failed to inject OSS-safe perspective_provider: {pp_err}",
                        exc_info=True
                    )
                    raise RuntimeError(
                        f"Failed to create OSS-safe perspective_provider: {pp_err}. "
                        f"Phase B4 Fix: Fusion requires request-context profile_source for correct store instance."
                    ) from pp_err
            except Exception as e:
                logger.error(f"[Fusion][R5] Failed to get real fusion service: {e}", exc_info=True)
                raise RuntimeError(f"Failed to initialize real fusion service: {e}")

            try:
                logger.info(f"[Fusion][R5] API request fusion_mode: {req.fusion_mode}")
                logger.info(f"[Fusion][R5] API request participants: {req.participants[:3] if req.participants else []}")

                req_dict = req.model_dump()
                logger.info(f"[Fusion][R5] req_dict fusion_mode: {req_dict.get('fusion_mode')}")
                logger.info(f"[Fusion][R5] req_dict options: {req_dict.get('options', {})}")

                req_dict.pop("session_id", None)

                real_request = RealFusionRequest(**req_dict)
                logger.info(f"[Fusion][R5] Domain request fusion_mode: {real_request.fusion_mode}")
                logger.info(f"[Fusion][R5] Converted FusionRequest to domain model successfully")
            except Exception as e:
                logger.error(f"[Fusion][R5] Failed to convert FusionRequest: {e}", exc_info=True)
                raise RuntimeError(f"Failed to convert request to domain model: {e}")

            logger.info(f"[Fusion][R5] Calling real LLM fusion: group_id={group_id}, participants={len(req.participants)}")
            start_time = datetime.now()

            result = await _run_fuse(service, real_request, group_id)

            latency_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            logger.info(f"[Fusion][R5] Real LLM fusion completed in {latency_ms}ms")

            from app.domain.models.fusion_result import FusionResult as DomainFusionResult

            if isinstance(result, DomainFusionResult):
                result_dict = result.model_dump()


                if result_dict.get("conflicts"):
                    converted_conflicts = []
                    for i, conflict in enumerate(result_dict["conflicts"]):
                        try:
                            if not isinstance(conflict, dict):
                                conflict = conflict.model_dump() if hasattr(conflict, "model_dump") else dict(conflict)

                            positions = conflict.get("positions") or []
                            if isinstance(positions, list):
                                positions_text = "\n".join(str(p) for p in positions if p)
                            else:
                                positions_text = str(positions) if positions else ""

                            description = (
                                conflict.get("description")
                                or positions_text
                                or conflict.get("analysis")
                                or conflict.get("issue")
                                or "No description available"
                            )

                            converted_conflict = {
                                "topic": conflict.get("issue") or conflict.get("topic") or conflict.get("conflict_type") or "Conflict",
                                "participants": conflict.get("parties") or conflict.get("participants") or [],
                                "description": description,
                                "severity": conflict.get("severity") or "medium",
                            }

                            converted_conflicts.append(converted_conflict)

                        except Exception as c_err:
                            logger.error(
                                f"[Fusion][R5][G2-SCHEMA] Conflict {i} conversion failed: {c_err}, "
                                f"conflict={conflict}",
                                exc_info=True
                            )
                            continue

                    result_dict["conflicts"] = converted_conflicts
                    logger.info(f"[G2-SCHEMA-MAPPING] Converted {len(converted_conflicts)} conflicts")

                if result_dict.get("alignment_points"):
                    converted_alignments = []
                    for i, alignment in enumerate(result_dict["alignment_points"]):
                        try:
                            if not isinstance(alignment, dict):
                                alignment = alignment.model_dump() if hasattr(alignment, "model_dump") else dict(alignment)

                            converted_alignment = {
                                "topic": alignment.get("summary") or alignment.get("topic") or "Alignment",
                                "participants": alignment.get("participants") or [],
                                "description": (
                                    alignment.get("description") or
                                    alignment.get("rationale") or
                                    alignment.get("summary") or
                                    "No description available"
                                ),
                                "strength": alignment.get("strength") or alignment.get("significance") or "moderate",
                            }

                            converted_alignments.append(converted_alignment)

                        except Exception as a_err:
                            logger.error(
                                f"[Fusion][R5][G2-SCHEMA] Alignment {i} conversion failed: {a_err}, "
                                f"alignment={alignment}",
                                exc_info=True
                            )
                            continue

                    result_dict["alignments"] = converted_alignments
                    result_dict.pop("alignment_points", None)
                    logger.info(f"[G2-SCHEMA-MAPPING] Converted {len(converted_alignments)} alignments (alignment_points -> alignments)")

                if result_dict.get("conclusion"):
                    if "metadata" not in result_dict:
                        result_dict["metadata"] = {}
                    result_dict["metadata"]["g2_conclusion"] = result_dict.pop("conclusion")
                    logger.info(f"[G2-SCHEMA-MAPPING] Mapped conclusion to metadata.g2_conclusion")

                if result_dict.get("risk_assessment"):
                    try:
                        risk_assessment = result_dict["risk_assessment"]
                        if not isinstance(risk_assessment, dict):
                            risk_assessment = risk_assessment.model_dump() if hasattr(risk_assessment, "model_dump") else dict(risk_assessment)

                        risks = []
                        if isinstance(risk_assessment, dict):
                            if risk_assessment.get("overall"):
                                risks.append({
                                    "risk_id": "overall-risk",
                                    "category": "overall",
                                    "description": f"Overall risk level: {risk_assessment['overall']}",
                                    "probability": "medium",
                                    "impact": risk_assessment["overall"],
                                    "mitigation": "Monitor and mitigate as needed",
                                })

                            if risk_assessment.get("categories"):
                                for domain, level in risk_assessment["categories"].items():
                                    risks.append({
                                        "risk_id": f"risk-{domain}",
                                        "category": domain,
                                        "description": f"{domain} risk level: {level}",
                                        "probability": "medium",
                                        "impact": level,
                                        "mitigation": "Monitor and mitigate as needed",
                                    })

                        result_dict["risks"] = risks
                        logger.info(f"[G5-SCHEMA-MAPPING] Converted risk_assessment to {len(risks)} risks")

                    except Exception as r_err:
                        logger.error(
                            f"[Fusion][R5][G5-SCHEMA] Risk assessment conversion failed: {r_err}",
                            exc_info=True
                        )

                g5_metadata = {}
                if result_dict.get("critical_issues"):
                    g5_metadata["critical_issues"] = result_dict["critical_issues"]
                    logger.info(f"[G5-STRUCTURED-MAPPING] captured {len(result_dict['critical_issues'])} critical_issues")
                if result_dict.get("recommendations"):
                    g5_metadata["recommendations"] = result_dict["recommendations"]
                    logger.info(f"[G5-STRUCTURED-MAPPING] captured {len(result_dict['recommendations'])} recommendations")
                if result_dict.get("go_live_conditions"):
                    g5_metadata["go_live_conditions"] = result_dict["go_live_conditions"]
                    logger.info(f"[G5-STRUCTURED-MAPPING] captured {len(result_dict['go_live_conditions'])} go_live_conditions")
                if result_dict.get("summary"):
                    g5_metadata["summary"] = result_dict["summary"]
                    logger.info(f"[G5-STRUCTURED-MAPPING] captured summary")

                if g5_metadata:
                    if "metadata" not in result_dict:
                        result_dict["metadata"] = {}
                    result_dict["metadata"]["g5_structured_output"] = g5_metadata

                if result_dict.get("perspectives"):
                    converted_perspectives = []
                    conversion_errors = []

                    for i, perspective in enumerate(result_dict["perspectives"]):
                        try:
                            if not isinstance(perspective, dict):
                                logger.error(
                                    f"[Fusion][R5] Perspective {i} is not a dict: type={type(perspective).__name__}"
                                )
                                raise ValueError(
                                    f"Perspective {i} is not a dict (type={type(perspective).__name__}). "
                                    f"Domain result.model_dump() should produce dict perspectives."
                                )

                            logger.info(
                                f"[Fusion][R5] Converting perspective {i}: "
                                f"keys={list(perspective.keys())}, "
                                f"participant_id={perspective.get('participant_id', 'MISSING')}, "
                                f"summary_len={len(perspective.get('summary', ''))}, "
                                f"status={perspective.get('status', 'MISSING')}"
                            )


                            converted_p = {}

                            participant_id = perspective.get("participant_id")
                            if not participant_id:
                                raise ValueError(
                                    f"Perspective {i}: missing required field 'participant_id'. "
                                    f"Available keys: {list(perspective.keys())}"
                                )
                            converted_p["participant_id"] = participant_id

                            profile_key = perspective.get("profile_key") or participant_id
                            if not profile_key:
                                raise ValueError(
                                    f"Perspective {i}: profile_key is empty. "
                                    f"participant_id={participant_id}, profile_key={profile_key}"
                                )
                            converted_p["profile_key"] = profile_key

                            perspective_text = (
                                perspective.get("perspective") or  # Direct field (unlikely)
                                perspective.get("summary") or      # Domain field (most common)
                                ""
                            )

                            if not perspective_text:
                                role = perspective.get("role", "unknown")
                                perspective_status = perspective.get("status", "unknown")
                                perspective_text = f"[{role}] Worker perspective (status: {perspective_status})"
                                logger.warning(
                                    f"[Fusion][R5] Perspective {i}: No perspective/summary text, "
                                    f"using fallback construction: '{perspective_text}'"
                                )

                            converted_p["perspective"] = perspective_text

                            confidence = perspective.get("confidence")
                            if confidence is not None:
                                converted_p["confidence"] = float(confidence)

                            perspective_status = perspective.get("status")
                            if perspective_status is not None:
                                converted_p["status"] = perspective_status

                            participant_type = perspective.get("participant_type")
                            if participant_type is not None:
                                converted_p["participant_type"] = participant_type

                            role = perspective.get("role")
                            if role is not None:
                                converted_p["role"] = role

                            evidence = perspective.get("evidence")
                            if evidence is not None:
                                converted_p["evidence"] = evidence

                            key_points = perspective.get("key_points")
                            if key_points is not None:
                                converted_p["key_points"] = key_points

                            concerns = perspective.get("concerns")
                            if concerns is not None:
                                converted_p["concerns"] = concerns

                            missing_fields = []
                            if not converted_p.get("participant_id"):
                                missing_fields.append("participant_id")
                            if not converted_p.get("profile_key"):
                                missing_fields.append("profile_key")
                            if not converted_p.get("perspective"):
                                missing_fields.append("perspective")

                            if missing_fields:
                                raise ValueError(
                                    f"Perspective {i}: Missing required fields after conversion: {missing_fields}. "
                                    f"Original keys: {list(perspective.keys())}, "
                                    f"Converted: {converted_p}"
                                )

                            logger.info(
                                f"[Fusion][R5] Perspective {i} conversion successful: "
                                f"participant_id={converted_p['participant_id']}, "
                                f"profile_key={converted_p['profile_key']}, "
                                f"perspective_len={len(converted_p['perspective'])}, "
                                f"status={converted_p.get('status', 'N/A')}, "
                                f"has_confidence={('confidence' in converted_p)}"
                            )

                            converted_perspectives.append(converted_p)

                        except Exception as p_err:
                            error_msg = (
                                f"[Fusion][R5] Perspective {i} conversion FAILED: {p_err}\n"
                                f"  perspective type: {type(perspective).__name__}\n"
                                f"  perspective keys: {list(perspective.keys()) if isinstance(perspective, dict) else 'N/A'}\n"
                                f"  participant_id: {perspective.get('participant_id', 'N/A') if isinstance(perspective, dict) else 'N/A'}\n"
                                f"  summary preview: {perspective.get('summary', 'N/A')[:100] if isinstance(perspective, dict) and perspective.get('summary') else 'N/A'}"
                            )
                            logger.error(error_msg, exc_info=True)
                            conversion_errors.append((i, str(p_err), perspective))

                    if conversion_errors:
                        error_summary = "\n".join([
                            f"  Perspective {i}: {err}"
                            for i, err, _ in conversion_errors[:3]  # Show first 3 errors
                        ])
                        raise ValueError(
                            f"Failed to convert {len(conversion_errors)} perspective(s) to API schema.\n"
                            f"First errors:\n{error_summary}\n"
                            f"Failing stage: response_schema_conversion\n"
                            f"Root cause: Domain Perspective lacks required API fields (profile_key, perspective->summary mapping)"
                        )

                    result_dict["perspectives"] = converted_perspectives
                    logger.info(
                        f"[Fusion][R5] Successfully converted {len(converted_perspectives)} perspectives: "
                        f"participant_ids=[{', '.join([p['participant_id'] for p in converted_perspectives[:5]])}]"
                    )
                else:
                    logger.warning(
                        f"[Fusion][R5] No perspectives in domain result! "
                        f"result_dict keys: {list(result_dict.keys())}, "
                        f"perspectives value: {result_dict.get('perspectives')}"
                    )

                api_result = FusionResult(**result_dict)

                api_result.metadata["route_impl"] = "real_llm_fusion"
                api_result.metadata["llm_enabled"] = True
                api_result.metadata["llm_flag_source"] = llm_flag_source
                api_result.metadata["llm_used"] = True
                api_result.metadata["latency_ms"] = latency_ms

                try:
                    registry = request.app.state.context.registry
                    worker_registry_store = registry.get('worker_registry_store')
                    worker_runtime_state_store = registry.get('worker_runtime_state_store')

                    if hasattr(service, '_availability_checker') and service._availability_checker:
                        checker_registry_store_id = id(service._availability_checker._registry_store) if hasattr(service._availability_checker, '_registry_store') else 0
                        checker_profile_binding_store_id = id(service._availability_checker._profile_binding_store) if hasattr(service._availability_checker, '_profile_binding_store') else 0
                    else:
                        checker_registry_store_id = 0
                        checker_profile_binding_store_id = 0

                    api_result.metadata["registry_store_source"] = "request.app.state.context.registry"
                    api_result.metadata["registry_store_instance_id"] = id(worker_registry_store) if worker_registry_store else 0
                    api_result.metadata["participant_checker_registry_store_instance_id"] = checker_registry_store_id
                    api_result.metadata["worker_crud_registry_store_instance_id"] = id(worker_registry_store) if worker_registry_store else 0
                    api_result.metadata["same_registry_store_instance"] = (id(worker_registry_store) == checker_registry_store_id) if worker_registry_store and checker_registry_store_id else False

                    worker_profile_content_store = registry.get('worker_profile_content_store')
                    worker_profile_binding_store = registry.get('worker_profile_binding_store')

                    api_result.metadata["profile_content_store_source"] = "request.app.state.context.registry"
                    api_result.metadata["profile_content_store_instance_id"] = id(worker_profile_content_store) if worker_profile_content_store else 0

                    api_result.metadata["profile_binding_store_source"] = "request.app.state.context.registry"
                    api_result.metadata["profile_binding_store_instance_id"] = id(worker_profile_binding_store) if worker_profile_binding_store else 0
                    api_result.metadata["fusion_profile_binding_store_instance_id"] = checker_profile_binding_store_id
                    api_result.metadata["same_profile_binding_store_instance"] = (id(worker_profile_binding_store) == checker_profile_binding_store_id) if worker_profile_binding_store and checker_profile_binding_store_id else False

                    fusion_profile_content_store_id = 0
                    if hasattr(service, '_provider') and service._provider:
                        provider = service._provider
                        if hasattr(provider, '_profile_source') and provider._profile_source:
                            profile_source = provider._profile_source
                            if hasattr(profile_source, '_api_source') and profile_source._api_source:
                                api_source = profile_source._api_source
                                if hasattr(api_source, '_content_store') and api_source._content_store:
                                    fusion_profile_content_store_id = id(api_source._content_store)

                    api_result.metadata["fusion_profile_content_store_instance_id"] = fusion_profile_content_store_id
                    api_result.metadata["same_profile_content_store_instance"] = (id(worker_profile_content_store) == fusion_profile_content_store_id) if worker_profile_content_store and fusion_profile_content_store_id else False

                    api_result.metadata["participants_requested_count"] = len(req.participants)
                    api_result.metadata["participants_resolved_count"] = len([p for p in result_dict.get("perspectives", []) if p])
                    api_result.metadata["online_workers_count"] = result_dict.get("metadata", {}).get("online_workers_count", 0)
                    api_result.metadata["active_profiles_loaded_count"] = result_dict.get("metadata", {}).get("active_profiles_loaded_count", 0)

                    result_profile_diagnostics = result_dict.get("metadata", {}).get("profile_diagnostics", {})
                    api_result.metadata["fallback_taken"] = result_profile_diagnostics.get("fallback_perspective_used", False)

                    profile_diagnostics = result_dict.get("metadata", {}).get("profile_diagnostics", {})
                    api_result.metadata["profile_content_loaded_count"] = profile_diagnostics.get("profile_content_loaded_count", 0)
                    api_result.metadata["profile_content_non_empty_count"] = profile_diagnostics.get("profile_content_non_empty_count", 0)
                    api_result.metadata["profile_content_length_min"] = profile_diagnostics.get("profile_content_length_min", 0)
                    api_result.metadata["profile_content_length_max"] = profile_diagnostics.get("profile_content_length_max", 0)
                    api_result.metadata["fallback_perspective_used"] = profile_diagnostics.get("fallback_perspective_used", False)
                    api_result.metadata["profile_loading_skip_reason"] = profile_diagnostics.get("profile_loading_skip_reason", "")

                    vector_supplement_diagnostics = result_dict.get("metadata", {}).get("vector_supplement_diagnostics", {})
                    if vector_supplement_diagnostics:
                        api_result.metadata["vector_supplement_enabled"] = vector_supplement_diagnostics.get("vector_supplement_enabled", False)
                        api_result.metadata["strict_participants"] = vector_supplement_diagnostics.get("strict_participants", True)
                        api_result.metadata["participants_before_supplement"] = vector_supplement_diagnostics.get("participants_before_supplement", [])
                        api_result.metadata["participants_resolved_before_supplement"] = vector_supplement_diagnostics.get("participants_resolved_before_supplement", 0)
                        api_result.metadata["supplement_trigger_condition_met"] = vector_supplement_diagnostics.get("supplement_trigger_condition_met", False)
                        api_result.metadata["supplement_search_called"] = vector_supplement_diagnostics.get("supplement_search_called", False)
                        api_result.metadata["supplement_candidates_count"] = vector_supplement_diagnostics.get("supplement_candidates_count", 0)
                        api_result.metadata["supplemented_participants_count"] = vector_supplement_diagnostics.get("supplemented_participants_count", 0)
                        api_result.metadata["participants_after_supplement"] = vector_supplement_diagnostics.get("participants_after_supplement", [])
                        api_result.metadata["supplement_skip_reason"] = vector_supplement_diagnostics.get("supplement_skip_reason")
                        logger.info(
                            f"[Fusion][R5][VectorSupplement] "
                            f"enabled={api_result.metadata.get('vector_supplement_enabled')}, "
                            f"strict={api_result.metadata.get('strict_participants')}, "
                            f"before={len(api_result.metadata.get('participants_before_supplement', []))}, "
                            f"search_called={api_result.metadata.get('supplement_search_called')}, "
                            f"candidates={api_result.metadata.get('supplement_candidates_count')}, "
                            f"supplemented={api_result.metadata.get('supplemented_participants_count')}, "
                            f"after={len(api_result.metadata.get('participants_after_supplement', []))}, "
                            f"reason={api_result.metadata.get('supplement_skip_reason')}"
                        )

                    result_meta = result_dict.get("metadata", {})
                    if "unavailable_participants" in result_meta:
                        api_result.metadata["unavailable_participants"] = result_meta["unavailable_participants"]
                    if "unavailable_reasons" in result_meta:
                        api_result.metadata["unavailable_reasons"] = result_meta["unavailable_reasons"]

                    logger.info(
                        f"[Fusion][R5][Diagnostics] "
                        f"registry_store_id={api_result.metadata['registry_store_instance_id']}, "
                        f"checker_store_id={checker_registry_store_id}, "
                        f"same_registry_instance={api_result.metadata['same_registry_store_instance']}, "
                        f"profile_content_store_id={api_result.metadata['profile_content_store_instance_id']}, "
                        f"fusion_profile_store_id={fusion_profile_content_store_id}, "
                        f"same_profile_instance={api_result.metadata['same_profile_content_store_instance']}, "
                        f"profile_binding_store_id={api_result.metadata['profile_binding_store_instance_id']}, "
                        f"fusion_binding_store_id={checker_profile_binding_store_id}, "
                        f"same_binding_instance={api_result.metadata['same_profile_binding_store_instance']}, "
                        f"participants_requested={api_result.metadata['participants_requested_count']}, "
                        f"participants_resolved={api_result.metadata['participants_resolved_count']}, "
                        f"active_profiles_loaded={api_result.metadata['active_profiles_loaded_count']}"
                    )
                except Exception as diag_err:
                    logger.warning(f"[Fusion][R5] Failed to add diagnostics: {diag_err}")
                    api_result.metadata["diagnostics_error"] = str(diag_err)

                result_metadata = result_dict.get("metadata", {})
                if "model_name" not in api_result.metadata:
                    model_name = (
                        result_metadata.get("llm_model") or
                        result_metadata.get("model_name") or
                        result_metadata.get("model") or
                        "GLM-4.7-Flash"  # Default from LLM_BASE_URL config
                    )
                    api_result.metadata["model_name"] = model_name

                logger.info(
                    f"[Fusion][R5] Real LLM fusion success: "
                    f"fusion_id={api_result.fusion_id}, "
                    f"perspectives={len(api_result.perspectives)}, "
                    f"implementation={api_result.metadata.get('implementation', 'unknown')}, "
                    f"llm_used={api_result.metadata.get('llm_used', False)}, "
                    f"model_name={api_result.metadata.get('model_name', 'N/A')}"
                )

                logger.info("[G5-TRACE] ========== TRACE-F: API Result Mapping ==========")
                logger.info("[G5-TRACE] domain_result_perspectives_count: %d", len(result_dict.get("perspectives", [])))
                logger.info("[G5-TRACE] api_result_perspectives_count: %d", len(api_result.perspectives))

                if result_dict.get("perspectives"):
                    domain_perspective_ids = [p.get("participant_id", "MISSING") for p in result_dict.get("perspectives", [])]
                    logger.info("[G5-TRACE] domain_result_perspective_ids: %s", domain_perspective_ids)

                if api_result.perspectives:
                    api_perspective_ids = [p.participant_id for p in api_result.perspectives]
                    logger.info("[G5-TRACE] api_result_perspective_ids: %s", api_perspective_ids)

                domain_count = len(result_dict.get("perspectives", []))
                api_count = len(api_result.perspectives)
                dropped_count = domain_count - api_count

                if dropped_count > 0:
                    logger.warning("[G5-TRACE] dropped_perspectives_count: %d", dropped_count)
                    logger.warning("[G5-TRACE] ⚠️ PERSPECTIVES DROPPED IN API MAPPING!")
                else:
                    logger.info("[G5-TRACE] ✅ No perspectives dropped in API mapping")

                logger.info("[G5-TRACE] response_perspectives_count: %d", api_count)
                logger.info("[G5-TRACE] ========== TRACE-F END ==========")

                return api_result
            else:
                logger.error(f"[Fusion][R5] Unexpected result type: {type(result)}")
                logger.error(f"[Fusion][R5] Expected: {DomainFusionResult}")
                raise RuntimeError(f"Unexpected result type from real fusion service: {type(result)}")

        except Exception as e:
            import traceback
            error_type = type(e).__name__
            error_message_preview = str(e)[:500]
            traceback_lines = traceback.format_exc().split('\n')
            top_stack_functions = [line.strip() for line in traceback_lines if line.strip().startswith('File "')][:5]

            failing_stage = "unknown"
            if "get_real_fusion_service" in str(traceback.format_exc()):
                failing_stage = "get_real_fusion_service"
            elif "get_availability_checker_from_request" in str(traceback.format_exc()):
                failing_stage = "get_availability_checker_from_request"
            elif "set_availability_checker" in str(traceback.format_exc()):
                failing_stage = "set_availability_checker"
            elif "service.fuse" in str(traceback.format_exc()) or "service_fuse" in str(traceback.format_exc()):
                failing_stage = "service_fuse"
            elif "FusionResult" in str(traceback.format_exc()) or "response_schema" in str(traceback.format_exc()):
                failing_stage = "response_schema_conversion"

            logger.error(
                f"[Fusion][R5] Real LLM fusion failed - DETAILED DIAGNOSTICS:\n"
                f"  error_type: {error_type}\n"
                f"  error_message_preview: {error_message_preview}\n"
                f"  failing_stage: {failing_stage}\n"
                f"  top_stack_functions: {top_stack_functions}\n"
                f"  route_impl_attempted: real_llm_fusion\n"
                f"  fallback_taken: true\n"
                f"Original error: {e}",
                exc_info=True
            )

            fusion_error_info = {
                "error_type": error_type,
                "error_message_preview": error_message_preview,
                "failing_stage": failing_stage,
                "route_impl_attempted": "real_llm_fusion",
                "fallback_taken": True
            }


    fallback_reason = None
    if not llm_enabled:
        fallback_reason = f"LLM not enabled (ENABLE_REAL_LLM={canonical_enabled}, LLM_ENABLED={legacy_enabled})"
    else:
        if 'fusion_error_info' in locals():
            error_info = fusion_error_info
            fallback_reason = (
                f"Real LLM fusion failed: {error_info['error_type']}: {error_info['error_message_preview']}\n"
                f"Failing stage: {error_info['failing_stage']}\n"
                f"Route attempted: {error_info['route_impl_attempted']}"
            )
        else:
            fallback_reason = f"Real LLM fusion failed: {str(e) if 'e' in locals() else 'unknown error'}"

    logger.info(f"[Fusion][R5] Using deterministic fallback: {fallback_reason}")

    provider_mode = os.environ.get("BCSFUSE_PROVIDER_MODE", "dev")
    if provider_mode == "runtime" and llm_enabled and 'fusion_error_info' in locals():
        logger.error(
            f"[Fusion][R5] RUNTIME MODE FAIL-FAST: "
            f"Real LLM fusion failed in runtime mode. "
            f"failing_stage={fusion_error_info['failing_stage']}, "
            f"error_type={fusion_error_info['error_type']}, "
            f"Refusing to fallback to deterministic_parity_fallback."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "REAL_FUSION_CONVERSION_ERROR",
                "message": f"Real LLM fusion succeeded but response conversion failed: {fusion_error_info['error_type']}",
                "failing_stage": fusion_error_info['failing_stage'],
                "error_type": fusion_error_info['error_type'],
                "error_preview": fusion_error_info['error_message_preview'],
                "route_impl_attempted": fusion_error_info['route_impl_attempted'],
                "provider_mode": provider_mode,
                "llm_enabled": llm_enabled,
            }
        )

    registry_store = _get_worker_registry_store(request)
    profile_store = _get_profile_content_store(request)

    fusion_id = f"fusion-{uuid.uuid4().hex[:12]}"


    if registry_store is None and profile_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "Both worker_registry_store and worker_profile_content_store not available",
                "mode": "minimal_public_safe",
            }
        )

    perspectives = []
    missing_participants = []

    for participant_id in req.participants:
        profile_content = None

        source = request.app.state.context.registry.get("worker_profile_source")
        if source is not None:
            try:
                profile = source.get_by_participant(participant_id)
                if profile is not None:
                    profile_content = {"description": profile.searchable_text}
            except Exception as error:  # noqa: BLE001 -- fallback reports missing content without exposing database details
                logger.warning("[Fusion] Current content unavailable: error_type=%s", type(error).__name__)

        if profile_content or not req.options.fail_fast:
            perspective = _create_minimal_perspective(
                participant_id=participant_id,
                profile_content=profile_content,
                question=req.question,
            )
            perspectives.append(perspective)

            if not profile_content:
                missing_participants.append(participant_id)
        else:
            missing_participants.append(participant_id)
            if req.options.fail_fast:
                break

    metadata = {
        "route_impl": "deterministic_parity_fallback",
        "llm_enabled": llm_enabled,
        "llm_flag_source": llm_flag_source,
        "llm_used": False,
        "fallback_reason": fallback_reason,
        "mode": "minimal_public_safe",
        "implementation": "deterministic_profile_summary",
        "vector_search_used": False,
        "conflict_detection": "not_evaluated",
        "expert_diagnosis": "not_evaluated",
        "group_id": group_id,
        "driver_bot_id": req.driver_bot_id,
        "parallel_collection": req.options.parallel,
        "include_recommendation": req.options.include_recommendation,
        "timeout_ms": req.options.timeout_ms,
        "fusion_mode": req.fusion_mode,
        "participant_count": len(req.participants),
        "perspective_count": len(perspectives),
        "missing_participant_count": len(missing_participants),
    }

    if req.metadata and req.metadata.trace_id:
        metadata["trace_id"] = req.metadata.trace_id
    else:
        metadata["trace_id"] = fusion_id

    logger.info(
        f"[Fusion][R5] result | "
        f"fusion_id={fusion_id}, "
        f"participants={len(req.participants)}, "
        f"perspectives={len(perspectives)}, "
        f"missing={len(missing_participants)}, "
        f"route_impl=deterministic_parity_fallback, "
        f"llm_enabled={llm_enabled}, "
        f"fallback_reason={fallback_reason}"
    )

    finished_at = datetime.now()
    started_at = datetime.fromtimestamp(finished_at.timestamp() - 0.01)
    duration_ms = 10  # Fallback is fast

    warnings = []
    if missing_participants:
        warnings.append(f"Missing profile content for {len(missing_participants)} participant(s): {', '.join(missing_participants)}")

    errors = []

    partial_success = len(perspectives) > 0 and len(missing_participants) > 0

    result = FusionResult(
        group_id=group_id,
        fusion_id=fusion_id,
        question=req.question,
        perspectives=perspectives,
        partial_success=partial_success,
        warnings=warnings,
        errors=errors,
        timing=TimingResponse(
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
        ),
        driver_bot_id=req.driver_bot_id,
        fusion_mode=req.fusion_mode,
        conflicts=[],  # Not evaluated in minimal implementation
        alignments=[],  # Not evaluated in minimal implementation
        risks=[],  # Not evaluated in minimal implementation
        recommendation=None,  # Not generated without LLM
        metadata=metadata,
    )

    return result



def include_r5_routes(app) -> None:
    """
    Mount R5 fusion route into FastAPI application.

    This function mounts the R5 fusion route with the /api/v1 prefix.

    Args:
        app: FastAPI application instance
    """
    app.include_router(router, prefix="/api/v1", tags=["R5-Fusion"])
    logger.info("[R5 Routes] R5 fusion route mounted successfully")


__all__ = ["router", "include_r5_routes"]