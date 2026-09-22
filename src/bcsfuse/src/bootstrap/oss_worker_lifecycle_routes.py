"""Authenticated worker lifecycle routes for OSS product integrations."""

import logging

from fastapi import APIRouter, HTTPException, Request, status

from src.bootstrap.oss_business_routes import require_oss_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Workers"])


@router.delete("/workers/{worker_id}")
async def delete_worker(worker_id: str, request: Request) -> dict:
    """Delete an existing worker through the always-mounted product API."""
    require_oss_auth(request)
    store = request.app.state.context.registry.get("worker_registry_store")
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_registry_store provider not available in OSS mode",
            },
        )

    try:
        if store.get_by_id(worker_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "WORKER_NOT_FOUND",
                    "message": f"Worker {worker_id} not found",
                },
            )
        store.delete(worker_id)
        return {"success": True, "worker_id": worker_id, "deleted": True}
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("[Workers OSS] Failed to delete worker %s", worker_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "DELETE_WORKER_ERROR", "message": str(error)},
        ) from error
