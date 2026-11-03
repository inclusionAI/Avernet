"""Compose Bot delivery while retaining the historical router module export."""

from . import router
from .owner_handoff import router as owner_handoff_router

router.router.include_router(owner_handoff_router)
