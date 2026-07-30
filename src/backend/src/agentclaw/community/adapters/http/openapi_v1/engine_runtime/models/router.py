"""Models group — ``/openapi/v1/bots/{bot_id}/models``."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.models.schemas import (
    Model,
)
from agentclaw.community.adapters.http.openapi_v1.principal import caller_owner_id
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.errors import EngineResourceNotFoundError
from agentclaw.community.di import Injected

router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/models", tags=["models"])

PrincipalDep = Annotated[Principal, Depends(require_principal)]


def _map_model(data: dict[str, Any]) -> Model:
    return Model(
        model_id=str(data.get("id", "")),
        name=str(data.get("name") or ""),
        provider=str(data.get("provider") or ""),
    )


@router.get("", response_model=Envelope[Page[Model]])
@envelope_errors
async def list_models(
    bot_id: str,
    page: PageParamsDep,
    principal: PrincipalDep,
    request: Request,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[Page[Model]]:
    """List the models this bot's engine can route to."""
    owner_id = caller_owner_id(principal)
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, method="GET", path="/api/models",
    )
    raw = result.data if isinstance(result.data, list) else []
    mapped = [_map_model(d) for d in raw if isinstance(d, dict)]
    start = (page.page - 1) * page.page_size
    return page_envelope(
        len(mapped), mapped[start : start + page.page_size], request
    )


@router.get("/{model_id:path}", response_model=Envelope[Model])
@envelope_errors
async def get_model(
    bot_id: str,
    model_id: str,
    principal: PrincipalDep,
    request: Request,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[Model]:
    """Get one model by its normalised id.

    Uses a ``:path`` converter because provider-qualified ids contain a slash
    (``openai/gpt-5.3``), matching how the engine addresses them. The id is used
    verbatim — no encoding is applied or required.
    """
    owner_id = caller_owner_id(principal)
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, method="GET",
        path=f"/api/models/{model_id}",
    )
    if not isinstance(result.data, dict):
        raise EngineResourceNotFoundError(f"no model {model_id}")
    return envelope(_map_model(result.data), request)
