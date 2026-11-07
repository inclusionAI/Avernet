"""Application index repair extends Avernet's shared Card management router."""

import asyncio
from typing import Annotated, Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from search_core.adapters.http.card_diagnostics import DocumentContent, QdrantContent
from search_core.adapters.http.cards import (
    CardId,
    ErrorBody,
)
from search_core.adapters.http.cards import (
    CardStatus as SharedCardStatus,
)
from search_core.adapters.http.cards import (
    create_card_router as create_shared_card_router,
)
from search_core.adapters.http.routes import Authenticator
from search_core.api.cards import CardManagement, CatalogConflict, IndexTargetMismatch
from search_core.api.models import VerifiedIdentity


class CardStatus(SharedCardStatus):
    documents: list[DocumentContent] = Field(
        default_factory=list, description="数据库当前版本的 Doc 内容，包含删除记录"
    )
    qdrant: QdrantContent = Field(
        default_factory=lambda: QdrantContent(
            status="unsupported", error="INDEX_INSPECTION_UNSUPPORTED"
        ),
        description="响应实例的 Qdrant 实时内容；两类集合分别读取",
    )
    doc_version: int = Field(
        validation_alias="document_version",
        description="当前索引版本在数据库中的 Doc 版本",
    )
    indexed_doc_version: int = Field(
        validation_alias="local_applied_document_version",
        description="当前实例最近成功同步的 Doc 版本；不是实时查询 Qdrant",
    )


class CardRebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index_key: str = Field(min_length=1, max_length=64)


class CardRebuildResult(BaseModel):
    published: Literal[True] = True
    index_key: str
    card_version: int = Field(validation_alias="saved_revision")


def create_card_router(service: CardManagement, authorize_operator: Authenticator):
    router = create_shared_card_router(
        service, authorize_operator, status_model=CardStatus
    )

    @router.post(
        "/{agent_id}/rebuild",
        response_model=CardRebuildResult,
        responses={404: {"model": ErrorBody}, 409: {"model": ErrorBody}},
    )
    async def rebuild(
        agent_id: CardId,
        body: CardRebuildRequest,
        identity: Annotated[VerifiedIdentity, Depends(authorize_operator)],
    ):
        try:
            return await asyncio.to_thread(service.rebuild, agent_id, body.index_key)
        except KeyError as error:
            raise HTTPException(404, "Card not found") from error
        except IndexTargetMismatch as error:
            raise HTTPException(
                409, "requested index_key is not loaded on this instance"
            ) from error
        except CatalogConflict as error:
            raise HTTPException(
                409, "Card rebuild conflicted; reload status and retry"
            ) from error
        except Exception as error:
            raise HTTPException(503, "Card rebuild unavailable") from error

    return router
