"""Authenticated worker lifecycle routes for OSS product integrations."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError

from src.bootstrap.oss_business_routes import require_oss_auth
from src.domain.exceptions import DuplicateWorkerException, WorkerNotFoundException
from src.interfaces.api.schemas.worker_config_schemas import (
    BatchQueryConfigRequest,
    BatchQueryConfigResponse,
    WorkerConfigItem,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Workers"])
external_router = APIRouter(prefix="/api/v1", tags=["Workers"])


class WorkerCreateRequest(BaseModel):
    """Stable public worker-registration payload."""

    id: str = Field(min_length=1)
    type: str = "bot"
    name: str = Field(min_length=1)
    handle: str | None = None
    description: str | None = None
    responsibilities: list[str] = Field(default_factory=lambda: ["general"])
    domains: list[str] = Field(default_factory=list)
    capabilities: list[dict[str, Any]] = Field(default_factory=list)
    skills: list[dict[str, Any]] = Field(default_factory=list)
    resources: list[dict[str, Any]] = Field(default_factory=list)
    availability: str = "private"
    trust_level: str = "guarded"
    external_id: str | None = None
    profile_key: str | None = None
    created_by: str | None = None


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _get_import_service(request: Request):
    from src.application.services.worker_import_service import WorkerImportService
    from src.infra.adapters.in_memory_worker_index_sync_adapter import (
        InMemoryWorkerIndexSyncAdapter,
    )

    registry = request.app.state.context.registry
    required = {
        "worker_registry_store": registry.get("worker_registry_store"),
        "worker_runtime_state_store": registry.get("worker_runtime_state_store"),
        "worker_profile_binding_store": registry.get("worker_profile_binding_store"),
        "worker_audit_log_store": registry.get("worker_audit_log_store"),
    }
    missing = [key for key, provider in required.items() if provider is None]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": f"required worker providers unavailable: {', '.join(missing)}",
            },
        )

    index_sync = registry.get("worker_index_sync_adapter")
    if index_sync is None:
        index_sync = InMemoryWorkerIndexSyncAdapter()
    return WorkerImportService(
        registry_store=required["worker_registry_store"],
        runtime_state_store=required["worker_runtime_state_store"],
        profile_binding_store=required["worker_profile_binding_store"],
        audit_log_adapter=required["worker_audit_log_store"],
        index_sync_adapter=index_sync,
    )


def _get_runtime_service(request: Request):
    from src.application.services.worker_runtime_state_service import (
        WorkerRuntimeStateService,
    )

    registry = request.app.state.context.registry
    worker_store = registry.get("worker_registry_store")
    runtime_store = registry.get("worker_runtime_state_store")
    if worker_store is None or runtime_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker registry or runtime-state provider unavailable",
            },
        )
    return WorkerRuntimeStateService(
        registry_store=worker_store,
        runtime_state_store=runtime_store,
        audit_log_adapter=registry.get("worker_audit_log_store"),
        index_sync_adapter=registry.get("worker_index_sync_adapter"),
        vector_store=registry.get("vector_store"),
    )


def _runtime_response(worker) -> dict:
    return {
        "success": True,
        "worker_id": worker.id,
        "runtime_state": _enum_value(worker.state.runtime_state),
        "lifecycle_state": _enum_value(worker.lifecycle_state),
        "version": worker.version,
    }


@router.post(
    "/workers/config/batch",
    response_model=BatchQueryConfigResponse,
)
async def batch_query_worker_configs(
    payload: BatchQueryConfigRequest,
    request: Request,
) -> BatchQueryConfigResponse:
    """Preserve the gateway-facing batch config query contract."""
    require_oss_auth(request)
    store = request.app.state.context.registry.get("worker_registry_store")
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_registry_store provider not available",
            },
        )

    configs, not_found_ids = store.batch_get_configs(payload.worker_ids)
    return BatchQueryConfigResponse(
        success=True,
        data={
            worker_id: WorkerConfigItem(fusion_enable=config.fusion_enable)
            for worker_id, config in configs.items()
        },
        not_found_ids=not_found_ids,
    )


@router.post("/workers", status_code=status.HTTP_201_CREATED)
async def create_worker(payload: WorkerCreateRequest, request: Request) -> dict:
    """Register a worker through the provider-backed application service."""
    require_oss_auth(request)
    service = _get_import_service(request)
    try:
        worker = service.import_from_api(
            worker_data={
                "id": payload.id,
                "type": payload.type,
                "identity": {
                    "name": payload.name,
                    "handle": payload.handle or f"@{payload.id}",
                    "description": payload.description,
                },
                "responsibilities": payload.responsibilities,
                "domains": payload.domains,
                "capabilities": payload.capabilities
                or [{"name": "general", "level": "intermediate"}],
                "skills": payload.skills,
                "resources": payload.resources,
                "state": {
                    "availability": payload.availability,
                    "trust_level": payload.trust_level,
                },
                "external_id": payload.external_id,
                "active_profile_key": payload.profile_key,
            },
            actor=payload.created_by,
        )
    except DuplicateWorkerException as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "WORKER_ALREADY_EXISTS", "message": str(error)},
        ) from error
    except ValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=error.errors(),
        ) from error

    return {
        "success": True,
        "id": worker.id,
        "type": _enum_value(worker.type),
        "name": worker.identity.name,
        "handle": worker.identity.handle,
        "description": worker.identity.description,
        "lifecycle_state": _enum_value(worker.lifecycle_state),
        "runtime_state": _enum_value(worker.state.runtime_state),
        "source_type": _enum_value(worker.source_type),
        "version": worker.version,
        "responsibilities": worker.responsibilities,
        "domains": worker.domains,
    }


async def _set_runtime_state(worker_id: str, request: Request, online: bool) -> dict:
    require_oss_auth(request)
    service = _get_runtime_service(request)
    try:
        worker = (
            service.set_online(worker_id, updated_by="oss_api")
            if online
            else service.set_offline(worker_id, updated_by="oss_api")
        )
        return _runtime_response(worker)
    except WorkerNotFoundException as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "WORKER_NOT_FOUND", "message": str(error)},
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_STATE_TRANSITION", "message": str(error)},
        ) from error
    except Exception as error:
        logger.exception(
            "[Worker Lifecycle OSS] Failed to persist runtime state for %s",
            worker_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "RUNTIME_STATE_UPDATE_ERROR",
                "message": "Failed to persist worker runtime state",
            },
        ) from error


@router.put("/workers/{worker_id}/online")
async def set_worker_online(worker_id: str, request: Request) -> dict:
    return await _set_runtime_state(worker_id, request, online=True)


@router.put("/workers/{worker_id}/offline")
async def set_worker_offline(worker_id: str, request: Request) -> dict:
    return await _set_runtime_state(worker_id, request, online=False)


@external_router.put("/workers/{worker_id}/online")
async def set_worker_online_external(worker_id: str, request: Request) -> dict:
    return await _set_runtime_state(worker_id, request, online=True)


@external_router.put("/workers/{worker_id}/offline")
async def set_worker_offline_external(worker_id: str, request: Request) -> dict:
    return await _set_runtime_state(worker_id, request, online=False)


@router.put("/workers/{worker_id}/profiles/{profile_id}/activate")
async def activate_worker_profile(
    worker_id: str,
    profile_id: str,
    request: Request,
) -> dict:
    """Activate a profile only after its visibility binding is durable."""
    require_oss_auth(request)
    registry = request.app.state.context.registry
    profile_store = registry.get("worker_profile_content_store")
    binding_store = registry.get("worker_profile_binding_store")
    worker_store = registry.get("worker_registry_store")
    if profile_store is None or binding_store is None or worker_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "profile content, binding, or worker provider unavailable",
            },
        )
    worker = worker_store.get_by_id(worker_id)
    if worker is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "WORKER_NOT_FOUND",
                "message": f"Worker {worker_id} not found",
            },
        )
    target_profile = profile_store.get(worker_id, profile_id)
    if target_profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "PROFILE_NOT_FOUND",
                "message": f"Profile {profile_id} not found for worker {worker_id}",
            },
        )

    from src.domain.models.worker_source_info import WorkerSourceType

    previous_binding = binding_store.get_active_binding(worker_id)
    previous_active_profile = profile_store.get_active(worker_id)
    binding_written = False
    try:
        binding_store.bind_profile(
            worker_id=worker_id,
            profile_key=f"{worker_id}:{profile_id}",
            source_type=WorkerSourceType.API,
        )
        binding_written = True
        activated = profile_store.activate(worker_id, profile_id)
        if activated is None:
            raise RuntimeError("profile activation did not update a record")
        updated_worker = worker.model_copy(deep=True)
        updated_worker.active_profile_key = f"{worker_id}:{profile_id}"
        worker_store.update(updated_worker)
    except Exception as error:
        if (
            previous_active_profile is not None
            and previous_active_profile.profile_id != profile_id
        ):
            try:
                profile_store.activate(
                    worker_id,
                    previous_active_profile.profile_id,
                )
            except Exception:
                logger.exception(
                    "[Profiles OSS] Failed to compensate profile activation for worker %s",
                    worker_id,
                )
        if binding_written:
            try:
                if previous_binding is None:
                    binding_store.unbind_profile(
                        worker_id,
                        f"{worker_id}:{profile_id}",
                    )
                else:
                    binding_store.bind_profile(
                        worker_id=previous_binding.worker_id,
                        profile_key=previous_binding.profile_key,
                        source_type=previous_binding.source_type,
                    )
            except Exception:
                logger.exception(
                    "[Profiles OSS] Failed to compensate binding for worker %s",
                    worker_id,
                )
        logger.exception(
            "[Profiles OSS] Failed to activate profile %s for worker %s",
            profile_id,
            worker_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "ACTIVATE_PROFILE_ERROR",
                "message": "Failed to persist profile activation",
            },
        ) from error

    return {
        "worker_id": worker_id,
        "profile_id": profile_id,
        "is_active": True,
        "binding_updated": True,
        "worker_updated": True,
        "message": f"Profile {profile_id} activated successfully for worker {worker_id}",
    }


def _get_profile_service():
    from src.interfaces.api.dependencies.worker_dependencies import (
        get_worker_profile_content_service,
    )

    return get_worker_profile_content_service()


@router.delete("/workers/{worker_id}")
async def delete_worker(worker_id: str, request: Request) -> dict:
    """Delete an existing worker through the always-mounted product API."""
    require_oss_auth(request)
    registry = request.app.state.context.registry
    store = registry.get("worker_registry_store")
    profile_store = registry.get("worker_profile_content_store")
    binding_store = registry.get("worker_profile_binding_store")
    if store is None or profile_store is None or binding_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker, profile content, or binding provider unavailable",
            },
        )
    if store.get_by_id(worker_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "WORKER_NOT_FOUND",
                "message": f"Worker {worker_id} not found",
            },
        )

    try:
        profile_service = _get_profile_service()
        if not profile_service.has_vector_cleanup():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "VECTOR_CLEANUP_UNAVAILABLE",
                    "message": "worker vector cleanup provider not available",
                },
            )
        profiles = profile_service.list_profiles(worker_id)
        for profile in profiles.items:
            profile_service.delete_profile_vectors(worker_id, profile.profile_id)
        active_binding = binding_store.get_active_binding(worker_id)
        store.delete(worker_id)
        for profile in profiles.items:
            profile_store.delete(worker_id, profile.profile_id)
        if active_binding is not None:
            binding_store.unbind_profile(worker_id, active_binding.profile_key)
        return {"success": True, "worker_id": worker_id, "deleted": True}
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("[Workers OSS] Failed to delete worker %s", worker_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "DELETE_WORKER_ERROR", "message": str(error)},
        ) from error
