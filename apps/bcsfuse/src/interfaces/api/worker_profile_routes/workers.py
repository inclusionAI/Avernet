"""Worker/Profile compatibility routes: workers."""

import logging
from fastapi import Request, HTTPException, status
from src.interfaces.api.schemas.worker_management_schemas import (
    WorkerBatchQueryRequest,
    WorkerBatchQueryResponse,
    WorkerAvailabilityUpdate,
    WorkerAvailabilityResponse,
    WorkerTrustLevelUpdate,
    WorkerTrustLevelResponse,
    WorkerPatchRequest,
    WorkerPatchResponse,
)

from . import common
from . import vector_sync

logger = logging.getLogger(__name__)


@common.mgmt_router.post(
    "/workers/batch",
    summary="Batch query workers",
    description="Query multiple workers by their IDs.",
    response_model=WorkerBatchQueryResponse,
    tags=["Workers"],
)
async def batch_query_workers(request: Request, req: WorkerBatchQueryRequest):
    """P1: Batch query workers by IDs."""
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


@common.api_router.put(
    "/workers/{worker_id}/availability",
    summary="Set worker availability",
    description="Update worker availability status.",
    response_model=WorkerAvailabilityResponse,
    tags=["Workers"],
)
async def set_worker_availability(worker_id: str, request: Request, req: WorkerAvailabilityUpdate):
    """P1: Set worker availability."""
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
            vector_sync._sync_availability_to_vector_store(worker_id, avail_value)

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
            vector_sync._sync_availability_to_vector_store(worker_id, avail_value)

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


@common.compat_router.put(
    "/workers/{worker_id}/availability",
    summary="Set worker availability (backward-compatible)",
    description="Update worker availability through the shared BCS lifecycle contract.",
    response_model=WorkerAvailabilityResponse,
    tags=["Workers"],
    deprecated=True,
)
async def set_worker_availability_compat(
    worker_id: str,
    request: Request,
    req: WorkerAvailabilityUpdate,
):
    """Backward-compatible alias for the BCS-facing /v1 contract."""
    return await set_worker_availability(worker_id, request, req)


@common.mgmt_router.put(
    "/workers/{worker_id}/trust-level",
    summary="Set worker trust level",
    description="Update worker trust level.",
    response_model=WorkerTrustLevelResponse,
    tags=["Workers"],
)
async def set_worker_trust_level(worker_id: str, request: Request, req: WorkerTrustLevelUpdate):
    """P1: Set worker trust level."""
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


@common.admin_router.patch(
    "/workers/{worker_id}",
    summary="Patch worker",
    description="Partial update of worker fields.",
    response_model=WorkerPatchResponse,
    tags=["Workers"],
)
async def patch_worker(worker_id: str, request: Request, req: WorkerPatchRequest):
    """P1: Patch worker."""
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

        # Convert dict to Worker object if needed
        from src.domain.models.worker import Worker

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
