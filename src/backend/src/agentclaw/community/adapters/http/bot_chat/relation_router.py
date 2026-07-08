"""Business relation ingest endpoint for bot-chat logs."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from agentclaw.community.core.bot_chat.repository import BotChatDbRepository
from agentclaw.community.core.bot_chat.schemas import ApiResponse
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.database import DatabasePlugin

router = APIRouter(prefix="/api/bot-chat", tags=["bot-chat"])


class LogRelationRef(BaseModel):
    ref_type: str = Field(..., min_length=1, max_length=128)
    ref_value: str = Field(..., min_length=1, max_length=1024)
    metadata: dict[str, Any] | None = None

    @field_validator("ref_type", "ref_value")
    @classmethod
    def _strip_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class LogRelationRequest(BaseModel):
    biz_scene: str = Field(..., min_length=1, max_length=256)
    biz_task_id: str = Field(..., min_length=1, max_length=256)
    engine: str | None = Field(default=None, max_length=64)
    collector: str | None = Field(default=None, max_length=128)
    user_id: str | None = Field(default=None, max_length=128)
    bot_id: str | None = Field(default=None, max_length=256)
    refs: list[LogRelationRef] = Field(..., min_length=1, max_length=100)
    metadata: dict[str, Any] | None = None

    @field_validator("biz_scene", "biz_task_id")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class LogRelationResult(BaseModel):
    inserted: int = 0
    updated: int = 0
    total: int = 0


@router.post("/log-relations", response_model=ApiResponse[LogRelationResult])
async def ingest_log_relations(
    request: LogRelationRequest,
    db: DatabasePlugin = Injected(DatabasePlugin),
):
    """Persist biz task to runtime-id relations for later log lookup."""
    repo = BotChatDbRepository(db)
    result = repo.upsert_biz_refs(request.model_dump())
    return ApiResponse(
        success=True,
        message="ok",
        error_code=200,
        data=LogRelationResult(**result),
    )
