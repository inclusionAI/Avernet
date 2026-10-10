"""Worker/Profile compatibility routes: config."""

import logging
from typing import Optional
from fastapi import Request, HTTPException, status
from src.interfaces.api.schemas.worker_management_schemas import (
    WorkerConfigResponse,
    WorkerConfigUpdate,
    WorkerConfigBatchUpdate,
    WorkerConfigBatchResponse,
    WorkersBySourceResponse,
    WorkerProfileQualityResponse,
)

from . import common

logger = logging.getLogger(__name__)


@common.mgmt_router.get(
    "/workers/{worker_id}/config",
    summary="Get worker config",
    description="Get worker configuration.",
    response_model=WorkerConfigResponse,
    tags=["Workers"],
)
async def get_worker_config(worker_id: str, request: Request):
    """P1: Get worker config."""
    common._require_auth(request)

    store = common._get_worker_registry_store(request)
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


@common.mgmt_router.put(
    "/workers/{worker_id}/config",
    summary="Update worker config",
    description="Update worker configuration.",
    response_model=WorkerConfigResponse,
    tags=["Workers"],
)
async def update_worker_config(worker_id: str, request: Request, req: WorkerConfigUpdate):
    """P1: Update worker config."""
    common._require_auth(request)

    store = common._get_worker_registry_store(request)
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

        old_fusion_enable = worker.config.fusion_enable

        # Build config update
        config_update = req.config or {}
        if req.fusion_enable is not None:
            config_update["fusion_enable"] = req.fusion_enable

        # Update worker config
        for key, value in config_update.items():
            setattr(worker.config, key, value)

        # Update worker with correct method signature
        updated_worker = store.update(worker)

        audit_log_store = common._get_audit_log_store(request)
        if audit_log_store is not None:
            from src.domain.models.worker_audit_log import (
                WorkerAuditAction,
                WorkerAuditLog,
            )
            from src.domain.models.worker_source_info import WorkerSourceType

            audit_log_store.append_log(
                WorkerAuditLog(
                    worker_id=worker_id,
                    action=WorkerAuditAction.CONFIG_CHANGED,
                    old_value=str(old_fusion_enable),
                    new_value=str(updated_worker.config.fusion_enable),
                    source_type=WorkerSourceType.API,
                    performed_by=request.query_params.get("updated_by"),
                )
            )

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


@common.admin_router.post(
    "/workers/config/batch",
    summary="Batch update worker configs",
    description="Update configurations for multiple workers.",
    response_model=WorkerConfigBatchResponse,
    tags=["Workers"],
)
async def batch_update_worker_configs(request: Request, req: WorkerConfigBatchUpdate):
    """P1: Batch update worker configs."""
    common._require_auth(request)

    store = common._get_worker_registry_store(request)
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


@common.mgmt_router.get(
    "/workers/config/by-source",
    summary="Query workers by source",
    description="Get workers filtered by source type.",
    response_model=WorkersBySourceResponse,
    tags=["Workers"],
)
async def query_workers_by_source(request: Request, source: Optional[str] = None):
    """P1: Query workers by source type."""
    common._require_auth(request)

    store = common._get_worker_registry_store(request)
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


@common.mgmt_router.get(
    "/workers/{worker_id}/profiles/quality",
    summary="Get worker profile quality",
    description="Get quality metrics for worker's profiles.",
    response_model=WorkerProfileQualityResponse,
    tags=["Workers"],
)
async def get_worker_profile_quality(worker_id: str, request: Request):
    """P1: Get worker profile quality."""
    common._require_auth(request)

    worker_store = common._get_worker_registry_store(request)
    profile_store = common._get_profile_content_store(request)

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
