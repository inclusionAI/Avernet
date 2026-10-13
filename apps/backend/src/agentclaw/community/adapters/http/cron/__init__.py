"""Cron HTTP routes, composed at the adapter package boundary."""
from fastapi import APIRouter

from agentclaw.community.adapters.http.cron.admin_router import router as admin_router
from agentclaw.community.adapters.http.cron.router import router as owner_router

router = APIRouter()
router.include_router(admin_router)
router.include_router(owner_router)

__all__ = ["router"]
