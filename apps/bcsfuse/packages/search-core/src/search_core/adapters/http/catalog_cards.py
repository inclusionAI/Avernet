"""Application index repair extends Avernet's shared Card management router."""

import asyncio
from typing import Annotated, Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

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
from search_core.api.cards import CardManagement, CatalogConflict
from search_core.api.models import VerifiedIdentity


class CardStatus(SharedCardStatus):
    document_version: int
    local_applied_document_version: int


class CardRebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index_key: str = Field(min_length=1, max_length=128)


class CardRebuildResult(BaseModel):
    published: Literal[True] = True
    index_key: str
    saved_revision: int


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
        except CatalogConflict as error:
            raise HTTPException(
                409, "Card rebuild conflicted; reload status and retry"
            ) from error
        except Exception as error:
            raise HTTPException(503, "Card rebuild unavailable") from error

    return router
