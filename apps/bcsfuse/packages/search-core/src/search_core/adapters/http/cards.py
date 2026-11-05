"""Card management: caller fields in, durable acknowledgements out."""

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from search_core.adapters.http.routes import Authenticator
from search_core.api.cards import CardManagement, CatalogConflict
from search_core.api.models import VerifiedIdentity
from search_core.catalog.cards import business_card

CardId = Annotated[str, Path(min_length=1, max_length=128, pattern=r"^\S+$")]


class CardWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card: dict[str, JsonValue] | None = Field(default=None, min_length=1)
    labels: dict[str, list[str]] | None = None

    # Omission preserves an existing type; new resources default to agent.
    resource_type: Literal["agent", "group"] | None = None

    @model_validator(mode="after")
    def ensure_changes(self):
        if self.card is None and self.labels is None:
            raise ValueError("provide card or labels")
        return self


class Saved(BaseModel):
    saved: Literal[True] = True
    card_version: int


class Deleted(BaseModel):
    deleted: Literal[True] = True


class CardDetails(BaseModel):
    labels: dict[str, list[str]]
    card: dict[str, JsonValue]
    resource_type: Literal["agent", "group"]
    card_version: int


class CardStatus(BaseModel):
    # Translate internal synchronization names only at the HTTP boundary.
    agent_id: str
    card_version: int = Field(validation_alias="saved_revision")
    deleted: bool
    doc_ready: bool = Field(
        validation_alias="prepared", description="当前 Card 版本的 Doc 是否已生成"
    )
    doc_error: str = Field(
        validation_alias="preparation_error", description="当前索引版本的 Doc 构建错误"
    )
    instance_id: str
    environment: str
    index_key: str
    indexed_card_version: int = Field(
        validation_alias="local_applied_revision",
        description="当前实例最近成功同步的 Card 版本；不是实时查询 Qdrant",
    )
    index_error: str = Field(
        validation_alias="local_error", description="当前实例的索引同步错误"
    )


class ErrorBody(BaseModel):
    detail: str


def create_card_router(
    service: CardManagement,
    authorize_operator: Authenticator,
    *,
    status_model: type[CardStatus] = CardStatus,
):
    router = APIRouter(
        prefix="/api/v2/agent-cards",
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
                body.card or {},
                body.resource_type,
                {},
                labels=body.labels,
            )
        except CatalogConflict as error:
            raise HTTPException(409, "Card resource type cannot change") from error
        except ValueError as error:
            raise HTTPException(400, "invalid Card management metadata") from error
        except Exception as error:
            raise HTTPException(503, "Card persistence unavailable") from error
        return Saved(card_version=row.revision)

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
            labels=row.card.labels,
            card=business_card(row.card),
            resource_type=row.card.resource_type,
            card_version=row.revision,
        )

    return router
