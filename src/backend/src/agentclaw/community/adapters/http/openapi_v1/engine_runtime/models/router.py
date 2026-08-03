"""Models group — ``/openapi/v1/bots/models/{bot_id}``."""

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

router = APIRouter(prefix="/openapi/v1/bots/models", tags=["models"])

PrincipalDep = Annotated[Principal, Depends(require_principal)]


def _map_model(data: dict[str, Any]) -> Model:
    return Model(
        model_id=str(data.get("id", "")),
        name=str(data.get("name") or ""),
        provider=str(data.get("provider") or ""),
    )


@router.get("/{bot_id}", response_model=Envelope[Page[Model]])
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
    # The engine wraps this one: data is {"models": [...], "total": n}, not a
    # bare list. Reading it as a list yields an empty page on every call against
    # a real device.
    payload = result.data if isinstance(result.data, dict) else {}
    raw = payload.get("models")
    raw = raw if isinstance(raw, list) else []
    mapped = [_map_model(d) for d in raw if isinstance(d, dict)]
    # Prefer the engine's own count; fall back to what we mapped.
    reported = payload.get("total")
    total = reported if isinstance(reported, int) else len(mapped)
    start = (page.page - 1) * page.page_size
    return page_envelope(total, mapped[start : start + page.page_size], request)


@router.get("/{bot_id}/{model_id:path}", response_model=Envelope[Model])
@envelope_errors
async def get_model(
    bot_id: str,
    model_id: str,
    principal: PrincipalDep,
    request: Request,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[Model]:
    """Get one model by id.

    Provider-qualified ids contain a slash (`openai/gpt-5.3`); pass the value
    exactly as the list endpoint returned it.
    """
    # The id is caller-controlled and spans slashes, and it is concatenated into
    # the engine path. httpx normalises dot segments when building the request,
    # so ".." would let a caller reach engine routes this surface deliberately
    # does not wrap — on their own bot, but still outside the published scope.
    # A model id never contains a dot segment.
    if any(part in ("..", ".") for part in model_id.split("/")):
        raise EngineResourceNotFoundError("invalid model id")
    owner_id = caller_owner_id(principal)
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, method="GET",
        path=f"/api/models/{model_id}",
    )
    if not isinstance(result.data, dict):
        raise EngineResourceNotFoundError(f"no model {model_id}")
    return envelope(_map_model(result.data), request)
