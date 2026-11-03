"""Bot public API module."""
from fastapi import APIRouter

from agentclaw.community.adapters.http.bot_public.router import router as bot_public_router
from agentclaw.community.adapters.http.bot_public.router_auth import router as bot_public_auth_router
from agentclaw.community.adapters.http.bot_public.public_noauth_router import router as _public_noauth_router
from agentclaw.community.adapters.http.bot_public.overview_noauth_router import router as _overview_noauth_router

# Every unauthenticated ``/api/public/bots`` route, mounted once by ``app.py``.
bot_public_noauth_router = APIRouter()
bot_public_noauth_router.include_router(_public_noauth_router)
bot_public_noauth_router.include_router(_overview_noauth_router)

__all__ = ["bot_public_router", "bot_public_auth_router", "bot_public_noauth_router"]
