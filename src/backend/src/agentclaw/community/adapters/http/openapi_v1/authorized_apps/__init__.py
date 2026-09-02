"""Owner-granted bot authorizations on the public API.

Two routers because they mount at different prefixes — the owner's operations
beneath a bot, the application's view at top level — over one record; and a
third, ``user_router``, over the account-level record beneath the org group.
"""

from agentclaw.community.adapters.http.openapi_v1.authorized_apps.router import (
    app_view_router,
    router,
)
from agentclaw.community.adapters.http.openapi_v1.authorized_apps.user_router import (
    router as user_router,
)

__all__ = ["app_view_router", "router", "user_router"]
