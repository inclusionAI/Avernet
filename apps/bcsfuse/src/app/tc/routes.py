"""TC v1 delivery: old HTTP shapes, single current Card, no legacy score gates."""

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from search_core.api.cards import CatalogConflict
from starlette.concurrency import run_in_threadpool

from app.adapters.http.schemas.worker_config_schemas import BatchQueryConfigRequest
from app.adapters.http.schemas.worker_management_schemas import (
    WorkerAvailabilityUpdate,
    WorkerBatchQueryRequest,
    WorkerSyncRequest,
    WorkerTrustLevelUpdate,
)
from app.domain.exceptions import WorkerNotFoundException
from app.domain.models.bot_recommendation import (
    BotRecommendationRequest,
    create_bot_recommendation_response,
)
from app.infra.trace_context import get_trace_id
from app.tc.responses import profile_response, worker_response

logger = logging.getLogger(__name__)


def dependencies(request):
    from app.tc.auth import require_oss_auth

    require_oss_auth(request)
    registry = request.app.state.context.registry
    return registry.require("tc_catalog"), registry.require("tc_search")


def config_response(row):
    return {
        "success": True,
        "worker_id": row.card.agent_id,
        "fusion_enable": (row.config or {}).get("fusion_enable", False),
        "config": row.config or {},
        "version": row.revision,
        "updated_at": None,
    }


def runtime_response(row):
    data = row.card.source_card
    return {
        "success": True,
        "worker_id": row.card.agent_id,
        "runtime_state": data.get("runtime_state"),
        "lifecycle_state": data.get("lifecycle_state", "active"),
        "version": row.revision,
    }


async def mutate(runtime, operation, *args, code, **kwargs):
    try:
        # Invalidate result caches before a write, including when the reply is lost.
        with runtime.caches.changing():
            result = await run_in_threadpool(operation, *args, **kwargs)
            await run_in_threadpool(runtime.updates.run_once)
            status = await run_in_threadpool(runtime.updates.status, args[0])
            if (
                not runtime.updates.ready
                or status["local_applied_revision"] != status["saved_revision"]
            ):
                raise HTTPException(
                    503,
                    detail={
                        "code": "INDEX_NOT_READY",
                        "message": "Worker saved; index synchronization pending",
                    },
                )
        return result
    except HTTPException:
        raise
    except WorkerNotFoundException as error:
        raise HTTPException(
            404, detail={"code": "WORKER_NOT_FOUND", "message": "Worker not found"}
        ) from error
    except CatalogConflict as error:
        raise HTTPException(
            409, detail={"code": "VERSION_CONFLICT", "message": "Concurrent update"}
        ) from error
    except (ValueError, ValidationError) as error:
        raise HTTPException(
            400, detail={"code": "INVALID_REQUEST", "message": "Invalid Worker data"}
        ) from error
    except Exception as error:
        logger.error(
            "TC mutation failed: operation=%s error_type=%s", code, type(error).__name__
        )
        raise HTTPException(
            500,
            detail={"code": code, "message": "Failed to persist or synchronize Worker"},
        ) from error


async def run_search(runtime, *args, **kwargs):
    from search_core.api.errors import SearchOverloaded, SearchUnavailable

    from app.tc.search import InvalidTcFilters

    try:
        return await runtime.search(*args, **kwargs)
    except InvalidTcFilters as error:
        raise HTTPException(400, detail={"code": "INVALID_FILTERS"}) from error
    except (SearchUnavailable, SearchOverloaded) as error:
        raise HTTPException(503, detail={"code": "SEARCH_UNAVAILABLE"}) from error


def require(catalog, worker_id):
    try:
        return catalog.require(worker_id)
    except WorkerNotFoundException as error:
        raise HTTPException(
            404, detail={"code": "WORKER_NOT_FOUND", "message": "Worker not found"}
        ) from error


def create_router():
    router = APIRouter()

    @router.post("/recommend")
    async def recommend(payload: BotRecommendationRequest, request: Request):
        _, runtime = dependencies(request)
        question = payload.question
        if payload.group_id:
            from app.tc.group_context import rewrite_with_group_context

            question = await rewrite_with_group_context(
                question, payload.group_id, get_trace_id()
            )
        candidates, result = await run_search(
            runtime,
            question,
            payload.topK,
            filters=payload.filters,
            rerank=payload.enable_rerank,
            expand_factor=payload.expand_factor,
            reranker_model=payload.reranker_model,
        )
        from app.domain.models.candidate_recommendation import (
            CandidateRecommendationResponse,
        )
        from app.domain.models.retrieval_mode import RetrievalMode

        response = create_bot_recommendation_response(
            CandidateRecommendationResponse(
                question=payload.question,
                mode=RetrievalMode.EXPERT_DIAGNOSIS,
                recommendations=candidates,
                total_candidates=len(candidates),
                selected_candidates=len(candidates),
                metadata={
                    "min_score_compat_mode": "deprecated_ignored",
                    "min_score_stage": "none",
                    "enable_rerank": payload.enable_rerank,
                    "candidate_source": f"tc_{runtime.storage_kind}",
                    "status": result.status,
                    "reasons": list(result.reasons),
                    "score_sources": [i.score_source for i in result.items],
                },
            ),
            driver_bot_id=payload.driver_bot_id,
            trace_id=get_trace_id(),
            query_type=payload.type,
        )
        return response

    @router.post("/workers/{worker_id}/sync")
    async def sync(worker_id: str, payload: WorkerSyncRequest, request: Request):
        catalog, runtime = dependencies(request)
        row, created = await mutate(
            runtime,
            catalog.sync,
            worker_id,
            payload.model_dump(exclude_unset=True),
            code="SYNC_WORKER_ERROR",
        )
        return {
            "success": True,
            "worker_id": worker_id,
            "created": created,
            "runtime_state": row.card.source_card.get("runtime_state"),
            "profile_id": row.card.source_card.get("profile_id", "default"),
            "profile_activated": True,
        }

    @router.post("/workers/config/batch")
    async def batch_config(request: Request, payload: BatchQueryConfigRequest):
        catalog, _ = dependencies(request)
        ids = payload.worker_ids
        data, missing = {}, []
        for key in ids:
            row = catalog.get(key)
            if row:
                data[key] = {
                    "fusion_enable": (row.config or {}).get("fusion_enable", False)
                }
            else:
                missing.append(key)
        return {"success": True, "data": data, "not_found_ids": missing}

    @router.post("/workers/batch")
    async def batch_workers(request: Request, payload: WorkerBatchQueryRequest):
        catalog, _ = dependencies(request)
        data, workers, missing = {}, [], []
        for key in payload.worker_ids:
            row = catalog.get(key)
            if row is None:
                missing.append(key)
                continue
            source = row.card.source_card
            worker = catalog.worker(row).model_dump(mode="json")
            data[key] = {
                "name": source["name"],
                "runtime_state": source.get("runtime_state"),
                "availability": worker["state"]["availability"],
                "profile_tags": {"trust_level": source.get("trust_level", "guarded")},
            }
            workers.append(worker)
        return {
            "success": True,
            "data": data,
            "workers": workers,
            "not_found_ids": missing,
            "total": len(workers),
        }

    @router.get("/workers")
    async def workers(
        request: Request,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        catalog, _ = dependencies(request)
        rows = list(catalog.rows())
        values = [
            worker_response(catalog, r)
            for r in rows[offset : offset + min(limit, 1000)]
        ]
        return {
            "success": True,
            "items": values,
            "total": len(rows),
            "limit": limit,
            "offset": offset,
        }

    @router.post("/workers", status_code=201)
    async def create_worker(request: Request, payload: dict):
        catalog, runtime = dependencies(request)
        worker_id = payload.get("worker_id") or payload.get("id")
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise HTTPException(422, detail={"code": "INVALID_WORKER_ID"})
        if catalog.get(worker_id):
            raise HTTPException(409, detail={"code": "WORKER_ALREADY_EXISTS"})
        payload = {
            **payload,
            "runtime_state": payload.get("runtime_state") or "offline",
            "availability": payload.get("availability", "private"),
        }
        try:
            parsed = WorkerSyncRequest.model_validate(
                {**payload, "profile": payload.get("profile", {})}
            )
        except ValidationError as error:
            raise RequestValidationError(error.errors()) from error
        row, _ = await mutate(
            runtime,
            catalog.sync,
            worker_id,
            {
                **parsed.model_dump(exclude_unset=True),
                **({"handle": payload["handle"]} if payload.get("handle") else {}),
            },
            code="CREATE_WORKER_ERROR",
        )
        return worker_response(catalog, row)

    @router.get("/workers/{worker_id}")
    async def worker(worker_id: str, request: Request):
        catalog, _ = dependencies(request)
        row = require(catalog, worker_id)
        return worker_response(catalog, row)

    @router.get("/workers/{worker_id}/config")
    async def get_config(worker_id: str, request: Request):
        catalog, _ = dependencies(request)
        return config_response(require(catalog, worker_id))

    @router.put("/workers/{worker_id}/config")
    async def update_config(worker_id: str, request: Request, payload: dict):
        catalog, runtime = dependencies(request)
        fields = dict(payload.get("config") or {})
        if "fusion_enable" in payload:
            fields["fusion_enable"] = payload["fusion_enable"]
        row = await mutate(
            runtime,
            catalog.save_config,
            worker_id,
            fields,
            version=payload.get("version"),
            code="UPDATE_CONFIG_ERROR",
        )
        return config_response(row)

    @router.put("/workers/{worker_id}/online")
    async def online(worker_id: str, request: Request):
        catalog, runtime = dependencies(request)
        row = await mutate(
            runtime,
            catalog.update,
            worker_id,
            {"runtime_state": "online"},
            code="RUNTIME_STATE_UPDATE_ERROR",
        )
        return runtime_response(row)

    @router.put("/workers/{worker_id}/offline")
    async def offline(worker_id: str, request: Request):
        catalog, runtime = dependencies(request)
        row = await mutate(
            runtime,
            catalog.update,
            worker_id,
            {"runtime_state": "offline"},
            code="RUNTIME_STATE_UPDATE_ERROR",
        )
        return runtime_response(row)

    @router.put("/workers/{worker_id}/availability")
    async def availability(
        worker_id: str, request: Request, payload: WorkerAvailabilityUpdate
    ):
        catalog, runtime = dependencies(request)
        row = await mutate(
            runtime,
            catalog.update,
            worker_id,
            {"availability": payload.availability.value},
            code="UPDATE_AVAILABILITY_ERROR",
        )
        return {
            **runtime_response(row),
            "availability": row.card.source_card["availability"],
        }

    @router.put("/workers/{worker_id}/trust-level")
    async def trust(worker_id: str, request: Request, payload: WorkerTrustLevelUpdate):
        catalog, runtime = dependencies(request)
        row = await mutate(
            runtime,
            catalog.update,
            worker_id,
            {"trust_level": payload.trust_level.value},
            code="UPDATE_TRUST_LEVEL_ERROR",
        )
        return {
            **runtime_response(row),
            "trust_level": row.card.source_card["trust_level"],
        }

    @router.delete("/workers/{worker_id}")
    async def delete(worker_id: str, request: Request):
        catalog, runtime = dependencies(request)
        await mutate(runtime, catalog.delete, worker_id, code="DELETE_WORKER_ERROR")
        return {"success": True, "worker_id": worker_id}

    @router.get("/workers/{worker_id}/profiles")
    async def profiles(worker_id: str, request: Request):
        catalog, _ = dependencies(request)
        row = require(catalog, worker_id)
        return {
            "success": True,
            "worker_id": worker_id,
            "items": [profile_response(catalog, row)],
            "active_profile_id": row.card.source_card.get("profile_id", "default"),
            "total": 1,
        }

    @router.get("/workers/{worker_id}/profiles/active")
    @router.get("/workers/{worker_id}/profiles/{profile_id}")
    async def profile(worker_id: str, request: Request, profile_id: str = "active"):
        catalog, _ = dependencies(request)
        row = catalog.get(worker_id)
        if row is None and profile_id == "active":
            from app.adapters.http.schemas.worker_management_schemas import (
                ProfileResponse,
            )

            return ProfileResponse(
                worker_id=worker_id, profile_id="default"
            ).model_dump(mode="json")
        row = require(catalog, worker_id)
        if profile_id not in {
            "active",
            row.card.source_card.get("profile_id", "default"),
        }:
            raise HTTPException(404, detail={"code": "PROFILE_NOT_FOUND"})
        return profile_response(catalog, row)

    @router.put("/workers/{worker_id}/profiles/{profile_id}/activate")
    async def activate(worker_id: str, profile_id: str, request: Request):
        catalog, _ = dependencies(request)
        row = require(catalog, worker_id)
        if catalog.resolve(f"{worker_id}:{profile_id}") != worker_id:
            raise HTTPException(404, detail={"code": "PROFILE_NOT_FOUND"})
        return {
            "success": True,
            "worker_id": worker_id,
            "profile_id": row.card.source_card.get("profile_id", "default"),
            "profile_key": catalog.profile_key(row),
            "profile_activated": True,
            "is_active": True,
            "binding_updated": False,
            "worker_updated": False,
            "message": "Current single Card",
        }

    return router
