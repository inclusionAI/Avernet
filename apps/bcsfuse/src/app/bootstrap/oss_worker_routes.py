"""OSS worker management transport routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, HTTPException, status

from app.bootstrap.index_write_guard import ensure_writable_index
from app.bootstrap.oss_business_support import (
    WorkerCreateRequestOSS,
    _enum_value,
    _get_runtime_state_service,
    _get_runtime_state_store,
    _get_worker_registry_store,
    _worker_to_contract_response,
    require_oss_auth,
)

logger = logging.getLogger(__name__)


def include_oss_worker_routes(
    *, business_router: APIRouter, mgmt_router: APIRouter, admin_router: APIRouter
) -> None:
    """Register the OpenAPI worker management endpoints on supplied routers."""
    @mgmt_router.get("/workers", tags=["Workers"])
    async def list_workers_oss(request: Request):
        """List workers (OSS wrapper)."""
        # Require authentication for protected endpoint
        require_oss_auth(request)

        store = _get_worker_registry_store(request)
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": "worker_registry_store provider not available in OSS mode"
                }
            )

        try:
            workers = store.list()
            # Handle both dict and object returns
            items = []
            for w in (workers or [])[:100]:
                if isinstance(w, dict):
                    items.append({
                        "id": w.get("worker_id", ""),
                        "name": w.get("identity", {}).get("name", "unknown"),
                        "type": w.get("worker_type", "bot"),
                    })
                else:
                    items.append({
                        "id": w.id if hasattr(w, 'id') else "",
                        "name": w.identity.name if hasattr(w, 'identity') and w.identity else "unknown",
                        "type": str(w.type) if hasattr(w, 'type') else "bot",
                    })

            return {
                "success": True,
                "items": items,
                "total": len(workers) if workers else 0,
            }
        except Exception as e:
            logger.error(f"[Workers OSS] Failed to list workers: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "LIST_WORKERS_ERROR", "message": str(e)}
            )

    @admin_router.post("/workers", tags=["Workers"])
    async def create_worker_oss(request: Request, create_req: WorkerCreateRequestOSS):
        """Create worker (OSS wrapper for test mode)."""
        # Require authentication for protected endpoint
        require_oss_auth(request)

        store = _get_worker_registry_store(request)
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": "worker_registry_store provider not available in OSS mode"
                }
            )

        try:
            # Import domain models
            from app.domain.models.worker import Worker, WorkerType, WorkerIdentity, WorkerState
            from app.domain.models.worker_lifecycle_state import WorkerLifecycleState
            from app.domain.models.worker_source_info import WorkerSourceType
            from app.domain.models.worker import Availability, TrustLevel

            # Create minimal worker object for test mode
            worker = Worker(
                id=create_req.worker_id,
                type=WorkerType.BOT,
                identity=WorkerIdentity(
                    name=create_req.name,
                    handle=create_req.worker_id,  # Use worker_id as handle
                    description=create_req.description or f"OSS test worker: {create_req.name}",
                ),
                responsibilities=["bot"],
                domains=create_req.domains,
                capabilities=[],
                constraints=[],
                skills=[],
                resources=[],
                memory_refs=[],
                state=WorkerState(
                    availability=Availability.PRIVATE,
                    trust_level=TrustLevel.UNVERIFIED,
                ),
                lifecycle_state=WorkerLifecycleState.INACTIVE,
                source_type=WorkerSourceType.API,
                version=1,  # Version must be >= 1
            )

            # Try to create in store
            created = store.create(worker)

            # Handle both dict and object returns (compatibility for different stores)
            if isinstance(created, dict):
                worker_id = created.get("worker_id", create_req.worker_id)
                name = created.get("identity", {}).get("name", create_req.name)
            else:
                worker_id = created.id if hasattr(created, 'id') else create_req.worker_id
                name = created.identity.name if hasattr(created, 'identity') else create_req.name

            return {
                "success": True,
                "worker_id": worker_id,
                "name": name,
            }
        except Exception as e:
            logger.error(f"[Workers OSS] Failed to create worker: {e}", exc_info=True)
            # Check for duplicate worker
            if "DuplicateWorker" in str(type(e).__name__) or "already exists" in str(e).lower():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "WORKER_ALREADY_EXISTS", "message": f"Worker {create_req.worker_id} already exists"}
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "CREATE_WORKER_ERROR", "message": str(e)}
            )

    @mgmt_router.get("/workers/{worker_id}", tags=["Workers"])
    async def get_worker_oss(worker_id: str, request: Request):
        """Get worker by ID (OSS wrapper)."""
        # Require authentication for protected endpoint
        require_oss_auth(request)

        store = _get_worker_registry_store(request)
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": "worker_registry_store provider not available in OSS mode"
                }
            )

        try:
            worker = store.get_by_id(worker_id)
            if worker is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "WORKER_NOT_FOUND", "message": f"Worker {worker_id} not found"}
                )

            # Handle both dict and object returns
            if isinstance(worker, dict):
                worker_dict = worker
            elif hasattr(worker, 'model_dump'):
                worker_dict = worker.model_dump()
            elif hasattr(worker, 'dict'):
                worker_dict = worker.dict()
            else:
                worker_dict = dict(worker)

            # Fetch runtime state from runtime_state_store
            runtime_state_store = _get_runtime_state_store(request)
            if runtime_state_store:
                try:
                    runtime_state_data = runtime_state_store.get_runtime_state(worker_id)
                    if runtime_state_data:
                        # Add runtime_state to worker dict
                        worker_dict["runtime_state"] = runtime_state_data.get("state", "offline")
                        worker_dict["is_online"] = runtime_state_data.get("state", "offline") == "online"
                    else:
                        # No runtime state record = offline
                        worker_dict["runtime_state"] = "offline"
                        worker_dict["is_online"] = False
                except Exception as e:
                    logger.warning(f"[Workers OSS] Failed to get runtime state for {worker_id}: {e}")
                    worker_dict["runtime_state"] = "offline"
                    worker_dict["is_online"] = False
            else:
                worker_dict["runtime_state"] = "unknown"
                worker_dict["is_online"] = False

            # Return flat WorkerResponse (OpenAPI contract)
            logger.info(f"[OSS_WORKER_GET_CONTRACT_RESPONSE] worker_id={worker_id} response_shape=flat worker_class={type(worker).__name__} store_class={type(store).__name__}")
            return _worker_to_contract_response(worker)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[Workers OSS] Failed to get worker {worker_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "GET_WORKER_ERROR", "message": str(e)}
            )

    @business_router.put("/workers/{worker_id}/online", tags=["Workers"])
    async def set_worker_online_oss(worker_id: str, request: Request):
        """Set worker online (OSS wrapper for test mode)."""
        # Require authentication for protected endpoint
        require_oss_auth(request)
        ensure_writable_index(request)

        # Get runtime state service
        try:
            runtime_state_service = _get_runtime_state_service(request)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[Workers OSS] Failed to get runtime state service: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": f"Failed to initialize runtime state service: {str(e)}"
                }
            )

        try:
            # Use WorkerRuntimeStateService.set_online() - proper implementation
            updated = runtime_state_service.set_online(worker_id, updated_by="oss_api")

            # Extract runtime state from worker
            if hasattr(updated, 'state') and hasattr(updated.state, 'runtime_state'):
                if hasattr(updated.state.runtime_state, 'value'):
                    runtime_state = str(updated.state.runtime_state.value)
                else:
                    runtime_state = str(updated.state.runtime_state)
            else:
                runtime_state = "online"

            if hasattr(updated, 'lifecycle_state'):
                if hasattr(updated.lifecycle_state, 'value'):
                    lifecycle_state = str(updated.lifecycle_state.value)
                else:
                    lifecycle_state = str(updated.lifecycle_state)
            else:
                lifecycle_state = "active"

            # Sync runtime_state to vector store payloads (Faiss + Qdrant)
            from app.adapters.http.worker_profile_parity_routes import _sync_runtime_state_to_vector_store
            _sync_runtime_state_to_vector_store(worker_id, "online")

            return {
                "success": True,
                "worker_id": worker_id,
                "status": "online",
                "runtime_state": runtime_state,
                "lifecycle_state": lifecycle_state,
            }
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[Workers OSS] Failed to set worker {worker_id} online: {e}", exc_info=True)
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[Workers OSS] Failed to set worker {worker_id} online: {e}", exc_info=True)

            # Check for specific errors
            if "not found" in error_msg.lower() or "WorkerNotFoundException" in type(e).__name__:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "WORKER_NOT_FOUND", "message": f"Worker {worker_id} not found"}
                )
            elif "DISABLED" in error_msg or "INACTIVE" in error_msg:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": "INVALID_STATE_TRANSITION", "message": error_msg}
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={"code": "SET_WORKER_ONLINE_ERROR", "message": error_msg}
                )

    @business_router.put("/workers/{worker_id}/offline", tags=["Workers"])
    async def set_worker_offline_oss(worker_id: str, request: Request):
        """Set worker offline (OSS wrapper for test mode)."""
        # Require authentication for protected endpoint
        require_oss_auth(request)
        ensure_writable_index(request)

        # Get runtime state service
        try:
            runtime_state_service = _get_runtime_state_service(request)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[Workers OSS] Failed to get runtime state service: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": f"Failed to initialize runtime state service: {str(e)}"
                }
            )

        try:
            # Use WorkerRuntimeStateService.set_offline() - proper implementation
            updated = runtime_state_service.set_offline(worker_id, updated_by="oss_api")

            # Extract version
            version = 1
            if hasattr(updated, 'version'):
                version = updated.version
            elif isinstance(updated, dict):
                version = updated.get('version', 1)

            # Return flat RuntimeStateResponse (OpenAPI contract)
            result = {
                "success": True,
                "worker_id": worker_id,
                "runtime_state": _enum_value(updated.state.runtime_state if hasattr(updated, 'state') else 'offline'),
                "lifecycle_state": _enum_value(updated.lifecycle_state if hasattr(updated, 'lifecycle_state') else 'inactive'),
                "version": version,
            }

            state_type = type(updated).__name__ if updated else 'None'
            logger.info(f"[OSS_WORKER_OFFLINE_CONTRACT_RESPONSE] worker_id={worker_id} state_type={state_type} runtime_state={result['runtime_state']} lifecycle_state={result['lifecycle_state']} version={version} audit_log_adapter_class={type(runtime_state_service._audit_log_adapter).__name__ if runtime_state_service._audit_log_adapter else None} result=success")

            # Sync runtime_state to vector store payloads (Faiss + Qdrant)
            from app.adapters.http.worker_profile_parity_routes import _sync_runtime_state_to_vector_store
            _sync_runtime_state_to_vector_store(worker_id, "offline")

            return result
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[Workers OSS] Failed to set worker {worker_id} offline: {e}", exc_info=True)

            # Check for specific errors
            if "not found" in error_msg.lower() or "WorkerNotFoundException" in type(e).__name__:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "WORKER_NOT_FOUND", "message": f"Worker {worker_id} not found"}
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={"code": "SET_WORKER_OFFLINE_ERROR", "message": error_msg}
                )

    # ===== End OpenAPI P1 Worker Core Routes =====

    # ========================================
    # OpenAPI P1 Worker Config Routes (Phase 3.2.3)
    @admin_router.delete("/workers/{worker_id}", tags=["Workers"])
    async def delete_worker_oss(worker_id: str, request: Request):
        """Delete worker (OSS wrapper for test mode)."""
        # Require authentication for protected endpoint
        require_oss_auth(request)

        store = _get_worker_registry_store(request)
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PROVIDER_NOT_AVAILABLE",
                    "message": "worker_registry_store provider not available in OSS mode"
                }
            )

        try:
            # Check if worker exists first
            worker = store.get_by_id(worker_id)
            if worker is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "WORKER_NOT_FOUND", "message": f"Worker {worker_id} not found"}
                )

            # Delete worker
            store.delete(worker_id)

            return {
                "success": True,
                "worker_id": worker_id,
                "deleted": True,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[Workers OSS] Failed to delete worker {worker_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "DELETE_WORKER_ERROR", "message": str(e)}
            )
