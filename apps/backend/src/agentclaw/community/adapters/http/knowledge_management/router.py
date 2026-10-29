"""Authenticated user management; target owner selects the Bot, never the actor."""

from typing import Literal

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.knowledge_management.errors import KnowledgeRoute
from agentclaw.community.adapters.http.knowledge_management.schemas import (
    AuthorizedBotPageResponse,
    BotCandidatePageResponse,
    CatalogItem,
    CatalogPageResponse,
    EnablementRequest,
    GrantRequest,
    GrantStatesRequest,
)
from agentclaw.community.api.bot_knowledge_service import BotKnowledgeServiceProtocol
from agentclaw.community.api.knowledge_management_service import (
    KnowledgeManagementServiceProtocol,
)
from agentclaw.community.core.bot_knowledge.agents_md_sync import (
    BotKnowledgeAgentsMdSync,
)
from agentclaw.community.core.knowledge_management.config import (
    KnowledgeManagementConfig,
)
from agentclaw.community.di import Injected
from agentclaw.community.kernel.knowledge import BotKnowledgeScope, KnowledgeError
from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError
from agentclaw.community.utils.env_utils import get_current_env
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

router = APIRouter(
    prefix="/api/knowledge", tags=["knowledge-management"], route_class=KnowledgeRoute
)


def execute(config, operation):
    config.require_enabled()
    try:
        return operation()
    except (KnowledgeManagementError, KnowledgeError):
        raise
    except ValueError:
        raise KnowledgeManagementError("INVALID_KNOWLEDGE_INPUT") from None
    except Exception:
        raise KnowledgeManagementError("KNOWLEDGE_DEPENDENCY_UNAVAILABLE") from None


@router.get("/bots/{bot_id}/knowledge-bases")
def bindings(
    bot_id: str,
    owner_id: str,
    cursor: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    selection_stage: Literal["draft"] = Query("draft"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: BotKnowledgeServiceProtocol = Injected(BotKnowledgeServiceProtocol),
    config: KnowledgeManagementConfig = Injected(KnowledgeManagementConfig),
):
    def operation():
        return service.list_bindings(
            scope=BotKnowledgeScope(bot_id, owner_id, get_current_env()),
            actor_id=user.staffId,
            cursor=cursor,
            limit=limit,
        )

    return execute(config, operation)


@router.put("/bots/{bot_id}/knowledge-bases/{kb_id}/enablement")
def set_enabled(
    bot_id: str,
    kb_id: str,
    body: EnablementRequest,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(get_current_user),
    service: BotKnowledgeServiceProtocol = Injected(BotKnowledgeServiceProtocol),
    agents_md_sync: BotKnowledgeAgentsMdSync = Injected(BotKnowledgeAgentsMdSync),
    config: KnowledgeManagementConfig = Injected(KnowledgeManagementConfig),
):
    def operation():
        return service.set_enabled(
            scope=BotKnowledgeScope(bot_id, body.target_owner_id, get_current_env()),
            actor_id=user.staffId,
            kb_id=kb_id,
            enabled=body.enabled,
            expected_revision=body.expected_revision,
        )

    result = execute(config, operation)
    # The stored selection is the source of truth; the AGENTS.md section is a
    # best-effort projection of it, so the sync runs after the response and
    # swallows its own failures (never blocks or fails this request).
    background_tasks.add_task(
        agents_md_sync.sync,
        scope=BotKnowledgeScope(bot_id, body.target_owner_id, get_current_env()),
        actor_id=user.staffId,
    )
    return result


def resolve_limit(limit, page_size):
    if limit is not None and page_size is not None and limit != page_size:
        raise HTTPException(400, detail={"code": "CONFLICTING_PAGE_SIZE"})
    return limit if limit is not None else page_size if page_size is not None else 20


@router.get(
    "/spaces/{space_id}/knowledge-bases",
    response_model=CatalogPageResponse,
    response_model_exclude_unset=True,
)
def catalog(
    space_id: int,
    cursor: str = Query("", max_length=128),
    limit: int | None = Query(None, ge=1, le=100),
    page_size: int | None = Query(None, ge=1, le=100),
    user: AuthenticatedUser = Depends(get_current_user),
    service: KnowledgeManagementServiceProtocol = Injected(
        KnowledgeManagementServiceProtocol
    ),
    config: KnowledgeManagementConfig = Injected(KnowledgeManagementConfig),
):
    size = resolve_limit(limit, page_size)

    def operation():
        return service.list_in_space(
            space_id=space_id, actor_id=user.staffId, cursor=cursor, limit=size
        )

    return execute(config, operation)


@router.get(
    "/spaces/{space_id}/knowledge-bases/{kb_id}/catalog-detail",
    response_model=CatalogItem,
    response_model_exclude_unset=True,
)
def detail(
    space_id: int,
    kb_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: KnowledgeManagementServiceProtocol = Injected(
        KnowledgeManagementServiceProtocol
    ),
    config: KnowledgeManagementConfig = Injected(KnowledgeManagementConfig),
):
    def operation():
        return service.detail(space_id=space_id, kb_id=kb_id, actor_id=user.staffId)

    return execute(config, operation)


@router.get(
    "/spaces/{space_id}/knowledge-bases/{kb_id}/authorized-bots",
    response_model=AuthorizedBotPageResponse,
)
def authorized_bots(
    space_id: int,
    kb_id: str,
    cursor: int = Query(0, ge=0, le=2**63 - 1),
    limit: int = Query(20, ge=1, le=100),
    selection_stage: Literal["draft"] = Query("draft"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: KnowledgeManagementServiceProtocol = Injected(
        KnowledgeManagementServiceProtocol
    ),
    config: KnowledgeManagementConfig = Injected(KnowledgeManagementConfig),
):
    def operation():
        return service.authorized_bots(
            space_id=space_id,
            kb_id=kb_id,
            actor_id=user.staffId,
            cursor=cursor,
            limit=limit,
        )

    return execute(config, operation)


@router.get(
    "/spaces/{space_id}/knowledge-bases/{kb_id}/bot-candidates",
    response_model=BotCandidatePageResponse,
)
def kb_candidates(
    space_id: int,
    kb_id: str,
    cursor: int = Query(0, ge=0, le=2**63 - 1),
    limit: int = Query(20, ge=1, le=100),
    keyword: str = Query("", max_length=128),
    user: AuthenticatedUser = Depends(get_current_user),
    service: KnowledgeManagementServiceProtocol = Injected(
        KnowledgeManagementServiceProtocol
    ),
    config: KnowledgeManagementConfig = Injected(KnowledgeManagementConfig),
):
    def operation():
        return service.candidates(
            space_id=space_id,
            kb_id=kb_id,
            actor_id=user.staffId,
            cursor=cursor,
            limit=limit,
            keyword=keyword,
        )

    return execute(config, operation)


@router.post("/spaces/{space_id}/knowledge-bases/{kb_id}/bot-authorization-states")
def grant_states(
    space_id: int,
    kb_id: str,
    body: GrantStatesRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: KnowledgeManagementServiceProtocol = Injected(
        KnowledgeManagementServiceProtocol
    ),
    config: KnowledgeManagementConfig = Injected(KnowledgeManagementConfig),
):
    def fetch_grant_states():
        return service.grant_states(
            space_id=space_id,
            kb_id=kb_id,
            actor_id=user.staffId,
            identities=[(target.bot_id, target.owner_id) for target in body.targets],
        )

    return execute(config, fetch_grant_states)


@router.put("/spaces/{space_id}/knowledge-bases/{kb_id}/bot-authorizations/{bot_id}")
def set_grant(
    space_id: int,
    kb_id: str,
    bot_id: str,
    body: GrantRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: KnowledgeManagementServiceProtocol = Injected(
        KnowledgeManagementServiceProtocol
    ),
    config: KnowledgeManagementConfig = Injected(KnowledgeManagementConfig),
):
    def operation():
        return service.set_grant(
            space_id=space_id,
            kb_id=kb_id,
            actor_id=user.staffId,
            bot_id=bot_id,
            owner_id=body.target_owner_id,
            granted=body.granted,
            expected_revision=body.expected_revision,
        )

    return execute(config, operation)


@router.get("/bots/{bot_id}/available-knowledge-bases")
def available(
    bot_id: str,
    owner_id: str,
    cursor: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=20),
    user: AuthenticatedUser = Depends(get_current_user),
    service: KnowledgeManagementServiceProtocol = Injected(
        KnowledgeManagementServiceProtocol
    ),
    config: KnowledgeManagementConfig = Injected(KnowledgeManagementConfig),
):
    def operation():
        return service.available_for_bot(
            scope=BotKnowledgeScope(bot_id, owner_id, get_current_env()),
            actor_id=user.staffId,
            cursor=cursor,
            limit=limit,
        )

    return execute(config, operation)
