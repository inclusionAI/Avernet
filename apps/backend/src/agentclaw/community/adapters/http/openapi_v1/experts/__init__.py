from fastapi import APIRouter
from .router import router as discovery_router
from .create import router as creation_router

router = APIRouter()
router.include_router(discovery_router)
router.include_router(creation_router)

__all__ = ["router"]
