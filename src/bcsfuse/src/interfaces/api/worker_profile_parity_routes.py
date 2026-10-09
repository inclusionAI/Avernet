"""Compatibility exports and composition of Worker/Profile HTTP routes."""

import logging
import os

from .worker_profile_routes.common import (
    _get_worker_registry_store as _get_worker_registry_store,
    _get_profile_content_store as _get_profile_content_store,
    _get_profile_binding_store as _get_profile_binding_store,
    _get_runtime_state_store as _get_runtime_state_store,
    _get_audit_log_store as _get_audit_log_store,
    _require_auth as _require_auth,
    api_router as api_router,
    mgmt_router as mgmt_router,
    admin_router as admin_router,
    compat_router as compat_router,
)
from .worker_profile_routes.analysis import (
    _get_llm_analysis_executor as _get_llm_analysis_executor,
    _get_profile_analyzer as _get_profile_analyzer,
    _generate_fallback_profile as _generate_fallback_profile,
    _analyze_and_persist_async as _analyze_and_persist_async,
)
from .worker_profile_routes.vector_sync import (
    _sync_availability_to_vector_store as _sync_availability_to_vector_store,
    _sync_runtime_state_to_vector_store as _sync_runtime_state_to_vector_store,
)
from .worker_profile_routes.sync import (
    sync_worker as sync_worker,
    sync_worker_compat as sync_worker_compat,
)
from .worker_profile_routes.workers import (
    batch_query_workers as batch_query_workers,
    set_worker_availability as set_worker_availability,
    set_worker_availability_compat as set_worker_availability_compat,
    set_worker_trust_level as set_worker_trust_level,
    patch_worker as patch_worker,
)
from .worker_profile_routes.config import (
    get_worker_config as get_worker_config,
    update_worker_config as update_worker_config,
    batch_update_worker_configs as batch_update_worker_configs,
    query_workers_by_source as query_workers_by_source,
    get_worker_profile_quality as get_worker_profile_quality,
)
from .worker_profile_routes.profiles import (
    activate_profile_put as activate_profile_put,
    get_active_profiles as get_active_profiles,
    search_profiles as search_profiles,
    get_profile_quality as get_profile_quality,
    analyze_profile as analyze_profile,
)
from .worker_profile_routes.profile_patch import (
    patch_profile as patch_profile,
)

logger = logging.getLogger(__name__)


def include_r3_routes(app) -> None:
    """
    Mount R3 worker/profile routes into FastAPI application.

    Route categories:
    - api_router: External product APIs at /api/v1 (sync, availability — for 3rd-party callers)
    - mgmt_router: Management platform APIs at /v1 (trust-level, profiles, config reads — for admin portal)
    - compat_router: Backward-compatible aliases at /v1 (sync and availability, for BCS)
    - admin_router: Privileged admin APIs at /v1/admin (only when BCSFUSE_EXPOSE_ADMIN=true)

    These routes MUST be mounted BEFORE skeleton routes to avoid shadowing.

    Args:
        app: FastAPI application instance
    """
    # External product APIs — always exposed at /api/v1
    app.include_router(api_router, prefix="/api/v1", tags=["R3-Worker-Profile"])

    # Management platform APIs — always exposed at /v1
    app.include_router(mgmt_router, prefix="/v1", tags=["R3-Management"])

    # Backward-compatible routes — mounted at /v1 (deprecated)
    app.include_router(compat_router, prefix="/v1", tags=["R3-Compat"])

    # High-risk admin APIs — conditionally exposed at /v1/admin
    if os.getenv("BCSFUSE_EXPOSE_ADMIN", "false").lower() == "true":
        app.include_router(admin_router, prefix="/v1/admin", tags=["R3-Admin"])
        logger.info("[R3 Routes] R3 admin routes mounted at /v1/admin")
    else:
        logger.info("[R3 Routes] R3 admin routes NOT mounted (set BCSFUSE_EXPOSE_ADMIN=true to enable)")

    logger.info("[R3 Routes] R3 worker/profile routes mounted successfully")
