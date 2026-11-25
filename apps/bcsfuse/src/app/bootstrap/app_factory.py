"""Transport composition for a fully assembled BCSFuse application context."""

import logging

from fastapi import APIRouter, FastAPI

from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.logging_setup import (
    configure_business_logging,
    resolve_business_log_level,
)

logger = logging.getLogger(__name__)


def _configure_fusion_dependencies(context: ApplicationContext) -> None:
    from app.adapters.http.dependencies import fusion_dependencies
    from app.application.services.bot_fuse.fused_profile_storage_service import (
        FusedProfileStorageService,
    )

    fusion_dependencies.set_app_context(context)
    fused_store = context.registry.get("fused_profile_store")
    storage_service = None
    if fused_store is not None:
        storage_service = FusedProfileStorageService(repository=fused_store)
        logger.info(
            "[App Factory] Composed fused_profile_store registered: %s",
            type(fused_store).__name__,
        )
    fusion_dependencies.set_fused_profile_storage_service(storage_service)


def create_bcsfuse_app(
    context: ApplicationContext,
    *,
    search_router: APIRouter | None = None,
) -> FastAPI:
    """Assemble TC over Cards; Alipay composes search_core independently."""
    from app.tc.bootstrap import create_tc_app

    context.registry.require("config")
    context.registry.require("secret_provider")
    configure_business_logging(resolve_business_log_level())
    return create_tc_app(context, search_router=search_router)
