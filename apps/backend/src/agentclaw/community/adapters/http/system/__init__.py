from agentclaw.community.adapters.http.system.router import readiness_router as system_readiness_router
from agentclaw.community.adapters.http.system.router import router as system_health_router
from agentclaw.community.adapters.http.system.router import disk_usage_router as system_disk_usage_router

__all__ = ["system_health_router", "system_readiness_router", "system_disk_usage_router"]

# The transitional content-store backfill route rides the readiness router's
# ``/api/system`` prefix: ``adapters/http/app.py`` is at its line cap, so the
# route is attached here rather than with a new ``include_router`` there.
from agentclaw.community.adapters.http.system.content_store_backfill import content_store_router  # noqa: E402

system_readiness_router.include_router(content_store_router)
