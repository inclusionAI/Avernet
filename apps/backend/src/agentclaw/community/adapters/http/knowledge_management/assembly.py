"""One corp gate for every new KB surface, including the public Bot alias."""

from typing import Literal

from agentclaw.community.adapters.http.bot_knowledge.router import router as bot_router
from agentclaw.community.adapters.http.knowledge_management.content_router import (
    router as content_router,
)
from agentclaw.community.adapters.http.knowledge_management.router import (
    router as management_router,
)
from agentclaw.community.core.knowledge_management.config import (
    KnowledgeManagementConfig,
)
from agentclaw.community.di import Injected
from agentclaw.community.kernel.knowledge import KnowledgeError
from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError
from fastapi import APIRouter, Depends, HTTPException, Query


def require_enabled(
    config: KnowledgeManagementConfig = Injected(KnowledgeManagementConfig),
):
    try:
        config.require_enabled()
    except KnowledgeManagementError as exc:
        raise HTTPException(503, detail={"code": exc.code}) from None
    except KnowledgeError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from None


def require_current_selection(selection_stage: Literal["draft"] = Query("draft")):
    """Online selection is deferred; never return a draft value as an online one."""


router = APIRouter(dependencies=[Depends(require_enabled)])
router.include_router(bot_router, dependencies=[Depends(require_current_selection)])
router.include_router(management_router)
router.include_router(content_router)


# Community preserves its existing ungated Bot-selection routes. Only the new
# management/content surface shares the management capability gate.
management_surface = APIRouter(dependencies=[Depends(require_enabled)])
management_surface.include_router(management_router)
management_surface.include_router(content_router)
