"""Shared OSS business-route support contracts.

This module owns protocol models, request-scoped provider access, response
projection helpers, and shared authentication. Keeping route mounting separate
from these support contracts prevents the bootstrap route module from
continuing to grow as an all-in-one transport implementation.
"""
__all__ = [
    "ProfileUpsertRequestOSS",
    "SearchRequestOSS",
    "WorkerCreateRequestOSS",
    "_check_provider_availability",
    "_dt_iso",
    "_enum_value",
    "_get_audit_log_store",
    "_get_embedding_provider",
    "_get_index_sync_adapter",
    "_get_profile_binding_store",
    "_get_profile_content_store",
    "_get_provider",
    "_get_provider_registry",
    "_get_runtime_state_service",
    "_get_runtime_state_store",
    "_get_vector_store",
    "_get_worker_registry_store",
    "_list_model_dump",
    "_model_dump",
    "_profile_to_contract_response",
    "_trust_gateway_enabled",
    "_worker_to_contract_response",
    "require_oss_auth",
]

import logging
from typing import Any, Optional

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator

from app.domain.services.search_filter_contract import (
    validate_v1_search_filters,
)

logger = logging.getLogger(__name__)


class SearchRequestOSS(BaseModel):
    """Search request (OSS wrapper)"""

    query: str = Field(
        ..., min_length=1, max_length=4096, description="Search query"
    )
    top_k: int = Field(default=10, ge=1, le=50, description="Number of results")
    mode: str = Field(default="auto", description="Search mode")
    min_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Minimum score threshold")
    filters: Optional[dict] = Field(default=None, description="Search filters")

    @model_validator(mode="after")
    def bounded_filters(self):
        validate_v1_search_filters(self.filters)
        return self


class WorkerCreateRequestOSS(BaseModel):
    """Worker create request (OSS wrapper)"""
    worker_id: str = Field(..., min_length=1, description="Worker ID")
    name: str = Field(..., min_length=1, description="Worker name")
    description: Optional[str] = Field(None, description="Worker description")
    skills: list[str] = Field(default_factory=list, description="Worker skills")
    domains: list[str] = Field(default_factory=list, description="Worker domains")
    is_public: bool = Field(default=True, description="Public visibility")


class ProfileUpsertRequestOSS(BaseModel):
    """
    Profile upsert request (OSS wrapper).

    DEPRECATED: This schema is kept for backwards compatibility but should not be used.
    Use ProfileRequest from worker_management_schemas instead.
    """
    profile_id: str = Field(..., min_length=1, description="Profile ID")
    content: str = Field(..., min_length=1, description="Profile content")
    metadata: Optional[dict] = Field(default_factory=dict, description="Profile metadata")


# Dependency injection helpers (use app.state.context, NOT global imports)
def _get_provider_registry(request: Request):
    """Get provider registry from ApplicationContext."""
    return request.app.state.context.registry


def _get_provider(request: Request, name: str) -> Optional[Any]:
    """Get a specific provider from the registry."""
    registry = _get_provider_registry(request)
    return registry.get(name)


# ========================================
# Provider Keys - MUST match registry keys in opensource.py
# ========================================
# Registry keys (from opensource.py):
# - worker_registry_store
# - worker_profile_content_store
# - vector_store
# - embedding_provider
# ========================================

def _get_worker_registry_store(request: Request):
    """Get worker registry store from provider registry.

    NOTE: Registry key is 'worker_registry_store', NOT 'worker_store'
    """
    return _get_provider(request, 'worker_registry_store')


def _get_profile_content_store(request: Request):
    """Get profile content store from provider registry.

    NOTE: Registry key is 'worker_profile_content_store', NOT 'profile_store'
    """
    return _get_provider(request, 'worker_profile_content_store')


def _get_profile_binding_store(request: Request):
    """Get profile binding store from provider registry.

    Phase B2 Fix: Ensures same instance across all paths (Profile CRUD, Fusion, etc.)
    """
    return _get_provider(request, 'worker_profile_binding_store')


def _get_runtime_state_store(request: Request):
    """Get runtime state store from provider registry."""
    return _get_provider(request, 'worker_runtime_state_store')


def _get_audit_log_store(request: Request):
    """Get audit log store from provider registry."""
    return _get_provider(request, 'worker_audit_log_store')


def _get_index_sync_adapter(request: Request):
    """Get index sync adapter from provider registry."""
    return _get_provider(request, 'worker_index_sync_adapter')


def _get_vector_store(request: Request):
    """Get vector store from provider registry (optional)."""
    return _get_provider(request, 'vector_store')


# ========================================
# Response Conversion Helpers (Contract-First)
# ========================================

def _enum_value(value):
    """Extract enum value safely."""
    if value is None:
        return None
    if hasattr(value, "value"):
        return value.value
    return str(value)


def _dt_iso(value):
    """Convert datetime to ISO format safely."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _model_dump(value):
    """Convert model to dict safely."""
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return value


def _list_model_dump(values):
    """Convert list of models to list of dicts safely."""
    if not values:
        return []
    return [_model_dump(v) for v in values]


def _worker_to_contract_response(worker) -> dict:
    """Convert worker object/dict to flat WorkerResponse contract.

    OpenAPI WorkerResponse fields:
    - success, id, type, name, handle, description
    - lifecycle_state, runtime_state, source_type, version
    - responsibilities, domains, availability, trust_level
    - created_at, updated_at
    """
    # Handle both dict and object
    if isinstance(worker, dict):
        w = worker
    elif hasattr(worker, 'model_dump'):
        w = worker.model_dump()
    else:
        w = dict(worker)

    # Extract identity
    identity = w.get('identity', {})
    if hasattr(identity, 'model_dump'):
        identity = identity.model_dump()
    elif not isinstance(identity, dict):
        identity = dict(identity) if identity else {}

    # Extract state
    state = w.get('state', {})
    if hasattr(state, 'model_dump'):
        state = state.model_dump()
    elif not isinstance(state, dict):
        state = dict(state) if state else {}

    return {
        "success": True,
        "id": w.get('id'),
        "type": _enum_value(w.get('type')),
        "name": identity.get('name'),
        "handle": identity.get('handle'),
        "description": identity.get('description'),
        "lifecycle_state": _enum_value(w.get('lifecycle_state')),
        "runtime_state": _enum_value(state.get('runtime_state')),
        "source_type": _enum_value(w.get('source_type')),
        "version": w.get('version'),
        "responsibilities": w.get('responsibilities', []),
        "domains": w.get('domains', []),
        "availability": _enum_value(state.get('availability')),
        "trust_level": _enum_value(state.get('trust_level')),
        "created_at": _dt_iso(w.get('created_at')),
        "updated_at": _dt_iso(w.get('updated_at')),
    }


def _profile_to_contract_response(content, *, is_active: bool = False) -> dict:
    """Convert profile content to flat ProfileResponse contract.

    OpenAPI ProfileResponse fields:
    - worker_id, profile_id, display_name, soul_md, agents_md, tools_md
    - boot_md, heartbeat_md, contents, skill_sets, metadata
    - content_type, is_active, version, quality_score, quality_issues
    - created_at, updated_at
    """
    # Handle both dict and object
    if isinstance(content, dict):
        c = content
    elif hasattr(content, 'model_dump'):
        c = content.model_dump()
    else:
        c = dict(content) if content else {}

    # Debug logging
    logger.debug(f"[PROFILE_TO_CONTRACT] content_type={type(content).__name__} keys={list(c.keys()) if c else []} worker_id={c.get('worker_id')} profile_id={c.get('profile_id')}")

    # Extract skill_sets
    skill_sets = c.get('skill_sets', [])
    if skill_sets and not isinstance(skill_sets, list):
        skill_sets = []
    elif skill_sets:
        skill_sets = _list_model_dump(skill_sets)

    return {
        "worker_id": c.get('worker_id'),
        "profile_id": c.get('profile_id'),
        "display_name": c.get('display_name'),
        "soul_md": c.get('soul_md'),
        "agents_md": c.get('agents_md'),
        "tools_md": c.get('tools_md'),
        "boot_md": c.get('boot_md'),
        "heartbeat_md": c.get('heartbeat_md'),
        "contents": c.get('contents') or {},
        "skill_sets": skill_sets,
        "metadata": c.get('metadata') or {},
        "content_type": _enum_value(c.get('content_type')) or 'api',
        "is_active": is_active,  # MUST be bool, not None
        "version": c.get('version', 1),
        "quality_score": c.get('quality_score'),
        "quality_issues": c.get('quality_issues') or [],
        "created_at": _dt_iso(c.get('created_at')),
        "updated_at": _dt_iso(c.get('updated_at')),
    }


def _get_runtime_state_service(request: Request):
    """Get WorkerRuntimeStateService with all required dependencies."""
    from app.application.services.worker_runtime_state_service import WorkerRuntimeStateService

    registry_store = _get_worker_registry_store(request)
    runtime_state_store = _get_runtime_state_store(request)
    audit_log_store = _get_audit_log_store(request)
    index_sync_adapter = _get_index_sync_adapter(request)
    vector_store = _get_vector_store(request)

    if registry_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_registry_store provider not available"
            }
        )

    if runtime_state_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_runtime_state_store provider not available"
            }
        )

    return WorkerRuntimeStateService(
        registry_store=registry_store,
        runtime_state_store=runtime_state_store,
        audit_log_adapter=audit_log_store,
        index_sync_adapter=index_sync_adapter,
        vector_store=vector_store,
    )


def _get_embedding_provider(request: Request):
    """Get embedding provider from provider registry."""
    return _get_provider(request, 'embedding_provider')


def _check_provider_availability(request: Request) -> dict:
    """Check which providers are available in the registry."""
    registry = _get_provider_registry(request)
    return {
        'worker_registry_store': registry.get('worker_registry_store') is not None,
        'worker_profile_content_store': registry.get('worker_profile_content_store') is not None,
        'embedding_provider': registry.get('embedding_provider') is not None,
        'vector_store': registry.get('vector_store') is not None,
    }


def _trust_gateway_enabled(config: Any) -> bool:
    """Read the gateway-trust deployment decision from composed configuration.

    The gateway authenticates the caller and forwards the request; when this
    explicit composition value is true, bcsfuse skips its own shared-Bearer
    check. Route code never reads the environment directly, so a stray
    ``BCSFUSE_TRUST_GATEWAY`` value cannot silently bypass composed auth.

    Scope: this bypass is GLOBAL — when the value is true, every protected
    bcsfuse route guarded by require_oss_auth skips the shared-Bearer check.
    Operational precondition: BCSFuse must then be reachable only through the
    gateway, not through a debug, singlebox, or direct port.
    """
    get_bool = getattr(config, "get_bool", None)
    if get_bool is None:
        return False
    return bool(get_bool("auth.trust_gateway", False))


def require_oss_auth(request: Request) -> None:
    """
    Require authentication for protected OSS routes.

    This function checks if a valid Bearer token is provided in the Authorization header.
    It reads the expected token from the auth provider registered in the application context.

    Args:
        request: FastAPI request object.

    Raises:
        HTTPException: 401 if authentication fails.
    """
    # Get auth provider from registry (NOT global)
    registry = _get_provider_registry(request)
    if _trust_gateway_enabled(registry.get("config")):
        return

    auth_provider = registry.get("auth")

    if auth_provider is None:
        # No auth provider configured - reject by default
        logger.error("[OSS Auth] No auth provider in registry")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": "Authentication required. Auth provider not configured."
            }
        )

    # Validate request
    if not auth_provider.validate_request(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": "Authentication required. Provide valid Bearer token in Authorization header."
            }
        )
