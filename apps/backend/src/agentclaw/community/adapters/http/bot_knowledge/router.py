"""Current application selection. Platform grants are managed outside this API."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.api.bot_knowledge_service import (
    BotKnowledgePublicationSelectionProtocol, BotKnowledgeServiceProtocol,
)
from agentclaw.community.core.bot_knowledge.agents_md_sync import (
    BotKnowledgeAgentsMdSync,
)
from agentclaw.community.di import Injected
from agentclaw.community.kernel.knowledge import BotKnowledgeScope, KnowledgeError
from agentclaw.community.utils.env_utils import get_current_env

router = APIRouter(prefix="/api/bot-knowledge", tags=["bot-knowledge"])


class EnableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    owner_id: str = Field(min_length=1, max_length=128)
    enabled: bool
    expected_revision: int = Field(ge=0)


def execute(call):
    try:
        return call()
    except KnowledgeError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from None
    except ValueError:
        raise HTTPException(400, detail={"code": "INVALID_KNOWLEDGE_INPUT"}) from None
    except Exception:
        raise HTTPException(
            503, detail={"code": "KNOWLEDGE_DEPENDENCY_UNAVAILABLE"}
        ) from None


@router.get("/{bot_id}")
def bindings(
    bot_id: str,
    owner_id: str,
    cursor: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    user: AuthenticatedUser = Depends(get_current_user),
    service: BotKnowledgeServiceProtocol = Injected(BotKnowledgeServiceProtocol),
):
    return execute(
        lambda: service.list_bindings(
            scope=BotKnowledgeScope(bot_id, owner_id, get_current_env()),
            actor_id=user.staffId,
            cursor=cursor,
            limit=limit,
        )
    )


@router.put("/{bot_id}/{kb_id}")
def enable(
    bot_id: str,
    kb_id: str,
    body: EnableRequest,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(get_current_user),
    service: BotKnowledgeServiceProtocol = Injected(BotKnowledgeServiceProtocol),
    agents_md_sync: BotKnowledgeAgentsMdSync = Injected(BotKnowledgeAgentsMdSync),
):
    result = execute(
        lambda: service.set_enabled(
            scope=BotKnowledgeScope(bot_id, body.owner_id, get_current_env()),
            actor_id=user.staffId,
            kb_id=kb_id,
            enabled=body.enabled,
            expected_revision=body.expected_revision,
        )
    )
    # Same best-effort projection as the knowledge-management enablement route:
    # the stored selection is the source of truth; the AGENTS.md section
    # follows after the response and never blocks or fails this request.
    background_tasks.add_task(
        agents_md_sync.sync,
        scope=BotKnowledgeScope(bot_id, body.owner_id, get_current_env()),
        actor_id=user.staffId,
    )
    return result


@router.get("/{bot_id}/publications/{publication_id}/selection")
def publication_selection(
    bot_id: str,
    publication_id: int,
    owner_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: BotKnowledgePublicationSelectionProtocol = Injected(BotKnowledgePublicationSelectionProtocol),
):
    """Last captured selection, not evidence of deployment or runtime access."""
    return execute(
        lambda: service.get_publication_selection(
            scope=BotKnowledgeScope(bot_id, owner_id, get_current_env()),
            actor_id=user.staffId,
            publication_id=publication_id,
        )
    )
