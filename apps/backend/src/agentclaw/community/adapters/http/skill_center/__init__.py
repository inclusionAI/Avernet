"""Skill Center API — Facade layer (schemas + router)."""
from fastapi import APIRouter
from typing import Any

from agentclaw.community.adapters.http.skill_center import skills, skillsets, skill_scan, skill_auth  # noqa: F401

_combined_router = None


def _get_combined_router() -> APIRouter:
    """Lazily build and return the combined router."""
    global _combined_router
    if _combined_router is None:
        import agentclaw.community.adapters.http.skill_center.verify as verify
        import agentclaw.community.adapters.http.skill_center.sync as sync
        import agentclaw.community.adapters.http.skill_center.skill_category as skill_category
        import agentclaw.community.adapters.http.skill_center.batch_sync as batch_sync

        _combined_router = APIRouter()
        _combined_router.include_router(verify.router)
        _combined_router.include_router(sync.router)
        _combined_router.include_router(skill_category.router)
        _combined_router.include_router(batch_sync.router)
    return _combined_router


class _LazyRouter:
    """Wrapper that delegates all attribute access to the lazily-built combined router."""

    def __getattr__(self, name: str) -> Any:
        return getattr(_get_combined_router(), name)


router = _LazyRouter()
