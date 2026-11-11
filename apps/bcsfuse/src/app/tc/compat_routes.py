"""Remaining v1 single-content and search adapters over the same TC catalog."""

from time import perf_counter

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from app.adapters.http.schemas.profile_management_schemas import ProfileSearchRequest
from app.adapters.http.schemas.worker_management_schemas import (
    ProfilePatchRequest,
    WorkerPatchRequest,
)
from app.tc.responses import profile_response, worker_response
from app.tc.routes import dependencies, mutate, require, run_search


class ProfileInput(ProfilePatchRequest):
    description: str | None = None


class SearchInput(ProfileSearchRequest):
    mode: str = "auto"
    top_k: int = Field(10, ge=1, le=50)


def create_compat_router():
    router = APIRouter()

    @router.patch("/workers/{worker_id}")
    async def patch_worker(
        worker_id: str, payload: WorkerPatchRequest, request: Request
    ):
        catalog, runtime = dependencies(request)
        fields = payload.model_dump(exclude_unset=True)
        fields = {
            key: value
            for key, value in fields.items()
            if key in {"name", "description", "domains", "responsibilities"}
        }
        row = await mutate(
            runtime, catalog.update, worker_id, fields, code="UPDATE_WORKER_ERROR"
        )
        return worker_response(catalog, row)

    @router.put("/workers/{worker_id}/profiles/{profile_id}")
    @router.patch("/workers/{worker_id}/profiles/{profile_id}")
    async def update_profile(
        worker_id: str, profile_id: str, payload: ProfileInput, request: Request
    ):
        catalog, runtime = dependencies(request)
        current = require(catalog, worker_id)
        # A legacy profile_id is only an alias to one current content record.
        fields = payload.model_dump(exclude_unset=True)
        updates = {}
        if fields.get("display_name") is not None:
            updates["name"] = fields["display_name"]
        if "description" in fields:
            updates["description"] = fields["description"]
        if fields.get("skill_sets") is not None:
            updates["skills"] = fields["skill_sets"]
        row = (
            await mutate(
                runtime, catalog.update, worker_id, updates, code="UPSERT_PROFILE_ERROR"
            )
            if updates
            else current
        )
        return profile_response(catalog, row)

    @router.get("/workers/config/by-source")
    async def workers_by_source(request: Request, source: str = ""):
        catalog, _ = dependencies(request)
        workers = [
            worker_response(catalog, row)
            for row in catalog.rows()
            if not source or row.card.source_card.get("source_type", "api") == source
        ]
        return {"source": source, "workers": workers, "total": len(workers)}

    @router.get("/workers/{worker_id}/profiles/quality")
    async def worker_quality(worker_id: str, request: Request):
        catalog, _ = dependencies(request)
        row = require(catalog, worker_id)
        content = profile_response(catalog, row)
        return {
            "worker_id": worker_id,
            "quality_score": content["quality_score"] or 0.0,
            "profile_count": 1,
            "active_profile_key": catalog.profile_key(row),
            "quality_details": {"quality_issues": content["quality_issues"]},
        }

    @router.get("/workers/{worker_id}/profiles/{profile_id}/quality")
    async def profile_quality(worker_id: str, profile_id: str, request: Request):
        catalog, _ = dependencies(request)
        row = require(catalog, worker_id)
        if catalog.resolve(f"{worker_id}:{profile_id}") != worker_id:
            raise HTTPException(404, detail={"code": "PROFILE_NOT_FOUND"})
        content = profile_response(catalog, row)
        return {
            "profile_key": catalog.profile_key(row),
            "worker_id": worker_id,
            "overall_score": content["quality_score"] or 0.0,
            "scores": [],
            "recommendations": content["quality_issues"],
            "last_analyzed": None,
        }

    @router.get("/workers/profiles/active-profiles")
    async def active_profiles(request: Request):
        catalog, _ = dependencies(request)
        profiles = [
            {
                "profile_key": catalog.profile_key(row),
                "worker_id": row.card.agent_id,
                "profile_id": row.card.source_card.get("profile_id", "default"),
                "display_name": row.card.source_card["name"],
                "is_active": True,
            }
            for row in catalog.rows()
        ]
        return {"profiles": profiles, "total": len(profiles)}

    @router.post("/workers/profiles/search")
    async def profile_search(payload: ProfileSearchRequest, request: Request):
        _, runtime = dependencies(request)
        candidates, _ = await run_search(
            runtime, payload.query, payload.top_k, filters=payload.filters
        )
        return {
            "results": [
                {
                    "profile_key": c.profile_key,
                    "worker_id": c.worker_id,
                    "score": c.score,
                    "matched_content": c.short_profile,
                    "highlights": [],
                }
                for c in candidates
            ],
            "total": len(candidates),
            "query": payload.query,
            "search_type": "hybrid",
            "trace_id": "",
        }

    @router.post("/search")
    async def search(payload: SearchInput, request: Request):
        catalog, runtime = dependencies(request)
        start = perf_counter()
        candidates, result = await run_search(
            runtime, payload.query, payload.top_k, filters=payload.filters
        )
        items = [
            {
                "profile_key": c.profile_key,
                "worker_id": c.worker_id,
                "profile_id": catalog.require(c.worker_id).card.source_card.get(
                    "profile_id", "default"
                ),
                "score": c.score,
                "aggregated_score": None,
                "fragment_matches": [],
                "short_profile": c.short_profile,
                "source": "hybrid",
            }
            for c in candidates
        ]
        return {
            "query": payload.query,
            "top_k": payload.top_k,
            "mode": payload.mode,
            "results_count": len(items),
            "results": items,
            "timing_ms": round((perf_counter() - start) * 1000, 2),
            "config_applied": {"min_score_compat_mode": "deprecated_ignored"},
            "status": result.status,
        }

    return router
