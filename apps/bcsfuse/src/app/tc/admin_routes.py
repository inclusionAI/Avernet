"""Conditional legacy management aliases; no independent profile lifecycle."""

from fastapi import APIRouter, HTTPException, Request

from app.adapters.http.schemas.profile_management_schemas import ProfileAnalyzeRequest
from app.adapters.http.schemas.worker_management_schemas import WorkerConfigBatchUpdate
from app.tc.routes import dependencies, mutate, require


def create_admin_router(core_router, compatibility_router):
    router = APIRouter()
    aliases = {
        ("/workers", "POST"),
        ("/workers/{worker_id}", "PATCH"),
        ("/workers/{worker_id}", "DELETE"),
        ("/workers/{worker_id}/config", "PUT"),
    }
    for route in [*core_router.routes, *compatibility_router.routes]:
        if any((route.path, method) in aliases for method in route.methods):
            router.add_api_route(
                route.path,
                route.endpoint,
                methods=list(route.methods),
                status_code=route.status_code,
            )

    @router.post("/workers/config/batch")
    async def batch_config(payload: WorkerConfigBatchUpdate, request: Request):
        catalog, runtime = dependencies(request)
        updated, failed = [], []
        for item in payload.updates:
            key = item.get("worker_id", "")
            fields = dict(item.get("config") or {})
            if "fusion_enable" in item:
                fields["fusion_enable"] = item["fusion_enable"]
            try:
                await mutate(
                    runtime,
                    catalog.save_config,
                    key,
                    fields,
                    code="UPDATE_CONFIG_ERROR",
                )
                updated.append(key)
            except HTTPException as error:
                failed.append(
                    {
                        "worker_id": key,
                        "reason": str(error.detail.get("code", "UPDATE_CONFIG_ERROR")),
                    }
                )
        return {"updated": updated, "failed": failed, "total": len(payload.updates)}

    @router.delete("/workers/{worker_id}/profiles/{profile_id}")
    async def delete_content(worker_id: str, profile_id: str, request: Request):
        catalog, runtime = dependencies(request)
        require(catalog, worker_id)
        if catalog.resolve(f"{worker_id}:{profile_id}") != worker_id:
            raise HTTPException(404, detail={"code": "PROFILE_NOT_FOUND"})
        await mutate(
            runtime,
            catalog.update,
            worker_id,
            {"description": None, "skills": []},
            code="DELETE_PROFILE_ERROR",
        )
        return {"success": True, "worker_id": worker_id, "profile_id": profile_id}

    @router.post("/workers/{worker_id}/profiles/{profile_id}/analyze")
    async def analyze(
        worker_id: str,
        profile_id: str,
        payload: ProfileAnalyzeRequest,
        request: Request,
    ):
        catalog, _ = dependencies(request)
        require(catalog, worker_id)
        raise HTTPException(
            503,
            detail={
                "code": "SERVICE_UNAVAILABLE",
                "message": "Automatic profile analysis is not enabled",
            },
        )

    return router
