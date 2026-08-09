"""Models group — ``/openapi/v1/bots/models/{bot_id}``.

**Private bots only** — private personal bots, and a service bot's
pre-publication draft workspace; see ``engine_runtime/gating.py``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.models.schemas import (
    Model,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.gating import (
    resolve_operable_bot,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.stage import STAGE_DRAFT
from agentclaw.community.core.engine_runtime.errors import EngineResourceNotFoundError
from agentclaw.community.di import Injected

router = APIRouter(prefix="/openapi/v1/bots/models", tags=["models"])


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
    owner_id: UserIdDep,
    request: Request,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[Page[Model]]:
    """List the models this bot's engine can route to."""
    facts = await resolve_operable_bot(
        relay, bot_id, owner_id, stage=STAGE_DRAFT, surface="models"
    )
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, facts=facts, stage=STAGE_DRAFT,
        method="GET", path="/api/models",
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
    owner_id: UserIdDep,
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
    facts = await resolve_operable_bot(
        relay, bot_id, owner_id, stage=STAGE_DRAFT, surface="models"
    )
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, facts=facts, stage=STAGE_DRAFT,
        method="GET",
        path=f"/api/models/{model_id}",
    )
    if not isinstance(result.data, dict):
        raise EngineResourceNotFoundError(f"no model {model_id}")
    return envelope(_map_model(result.data), request)
