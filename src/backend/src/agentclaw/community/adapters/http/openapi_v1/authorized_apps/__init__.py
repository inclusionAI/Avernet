"""Owner-granted bot authorizations on the public API.

Two routers because they mount at different prefixes — the owner's operations
beneath a bot, the application's view at top level — over one record.
"""

from agentclaw.community.adapters.http.openapi_v1.authorized_apps.router import (
    app_view_router,
    router,
)

__all__ = ["app_view_router", "router"]
