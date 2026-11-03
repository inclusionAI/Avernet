"""Card management: caller fields in, durable acknowledgements out."""

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from search_core.adapters.http.routes import Authenticator
from search_core.api.cards import CardManagement, CatalogConflict
from search_core.api.models import VerifiedIdentity
from search_core.catalog.cards import business_card

CardId = Annotated[str, Path(min_length=1, max_length=128, pattern=r"^\S+$")]


class CardWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card: dict[str, JsonValue] = Field(min_length=1)
    # Omission preserves an existing type; new resources default to agent.
    resource_type: Literal["agent", "group"] | None = None


class Saved(BaseModel):
    saved: Literal[True] = True
    saved_revision: int


class Deleted(BaseModel):
    deleted: Literal[True] = True


class CardDetails(BaseModel):
    card: dict[str, JsonValue]
    resource_type: Literal["agent", "group"]
    saved_revision: int


class CardStatus(BaseModel):
    agent_id: str
    saved_revision: int
    deleted: bool
    prepared: bool
    preparation_error: str
    instance_id: str
    environment: str
    index_key: str
    local_applied_revision: int
    local_error: str


class ErrorBody(BaseModel):
    detail: str


def create_card_router(
    service: CardManagement,
    authorize_operator: Authenticator,
    *,
    status_model: type[CardStatus] = CardStatus,
):
    router = APIRouter(
        prefix="/api/v1/agent-cards",
        tags=["agent-cards"],
        responses={403: {"model": ErrorBody}, 503: {"model": ErrorBody}},
    )

    @router.put(
        "/{agent_id}",
        status_code=202,
        response_model=Saved,
        responses={400: {"model": ErrorBody}, 409: {"model": ErrorBody}},
    )
    async def save(
        agent_id: CardId,
        body: CardWrite,
        identity: Annotated[VerifiedIdentity, Depends(authorize_operator)],
    ):
        try:
            row = await asyncio.to_thread(
                service.upsert,
                agent_id,
                body.card,
                body.resource_type,
                {},
            )
        except CatalogConflict as error:
            raise HTTPException(409, "Card resource type cannot change") from error
        except ValueError as error:
            raise HTTPException(400, "invalid Card management metadata") from error
        except Exception as error:
            raise HTTPException(503, "Card persistence unavailable") from error
        return Saved(saved_revision=row.revision)

    @router.delete("/{agent_id}", status_code=202, response_model=Deleted)
    async def delete(
        agent_id: CardId,
        identity: Annotated[VerifiedIdentity, Depends(authorize_operator)],
    ):
        try:
            await asyncio.to_thread(service.remove, agent_id)
        except Exception as error:
            raise HTTPException(503, "Card deletion unavailable") from error
        return Deleted()

    @router.get(
        "/{agent_id}/index-status",
        response_model=status_model,
        responses={404: {"model": ErrorBody}},
    )
    async def status(
        agent_id: CardId,
        identity: Annotated[VerifiedIdentity, Depends(authorize_operator)],
    ):
        try:
            return await asyncio.to_thread(service.status, agent_id)
        except KeyError as error:
            raise HTTPException(404, "Card not found") from error
        except Exception as error:
            raise HTTPException(503, "Card status unavailable") from error

    @router.get(
        "/{agent_id}",
        response_model=CardDetails,
        responses={404: {"model": ErrorBody}},
    )
    async def read(
        agent_id: CardId,
        identity: Annotated[VerifiedIdentity, Depends(authorize_operator)],
    ):
        try:
            row = await asyncio.to_thread(service.read, agent_id)
            if row.card.deleted:
                raise KeyError(agent_id)
        except KeyError as error:
            raise HTTPException(404, "Card not found") from error
        except Exception as error:
            raise HTTPException(503, "Card read unavailable") from error
        return CardDetails(
            card=business_card(row.card),
            resource_type=row.card.resource_type,
            saved_revision=row.revision,
        )

    return router
