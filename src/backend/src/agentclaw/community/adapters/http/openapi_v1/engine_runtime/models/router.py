"""Models group — ``/openapi/v1/bots/{bot_id}/models``.

An **operator console**: served to the addressed bot's owner and its
member-level collaborators, for the stage the request names (``?stage=``,
draft by default), and device-wide — see ``engine_runtime/gating.py`` and
``core/engine_runtime/gate.py``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.models.schemas import (
    Model,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    RuntimeStage,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
    StageQuery,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.friend_chat import (
    FriendUserIdQuery,
    authorize_friend_chat,
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
from agentclaw.community.api.human_bot_friendship_service import (
    HumanBotFriendshipServiceProtocol,
)
from agentclaw.community.core.engine_runtime.errors import EngineResourceNotFoundError
from agentclaw.community.di import Injected
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/models",
    tags=["models"],
    route_class=PublicAPIRoute,
)

#: The path parameter naming the model an operation addresses.
ModelIdPath = Annotated[
    str,
    Path(
        description="The model's id, exactly as returned by the model "
        "listing for this bot."
    ),
]


def _map_model(data: dict[str, Any]) -> Model:
    return Model(
        model_id=str(data.get("id", "")),
        name=str(data.get("name") or ""),
        provider=str(data.get("provider") or ""),
    )


@router.get("", response_model=Envelope[Page[Model]])
@envelope_errors
async def list_models(
    bot_id: BotIdPath,
    page: PageParamsDep,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    f_user_id: FriendUserIdQuery = None,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    friendships: HumanBotFriendshipServiceProtocol = Injected(
        HumanBotFriendshipServiceProtocol
    ),
) -> Envelope[Page[Model]]:
    """List the models this bot's engine can route to."""
    if f_user_id is not None:
        if stage is not RuntimeStage.DRAFT:
            raise EngineResourceNotFoundError(
                "friend models are available only in draft"
            )
        await authorize_friend_chat(
            request=request,
            bot_id=bot_id,
            caller_id=user_id,
            owner_id=owner_id,
            friend_user_id=f_user_id,
            friendships=friendships,
        )
    facts = await resolve_operable_bot(
        relay,
        bot_id,
        caller_id=owner_id if f_user_id is not None else user_id,
        owner_id=owner_id,
        stage=stage.value,
        surface="models",
    )
    result = await relay.call(
        bot_id=bot_id,
        owner_id=owner_id,
        facts=facts,
        stage=stage.value,
        method="GET",
        path="/api/models",
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


@router.get("/{model_id:path}", response_model=Envelope[Model])
@envelope_errors
async def get_model(
    bot_id: BotIdPath,
    model_id: ModelIdPath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    f_user_id: FriendUserIdQuery = None,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    friendships: HumanBotFriendshipServiceProtocol = Injected(
        HumanBotFriendshipServiceProtocol
    ),
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
    if f_user_id is not None:
        if stage is not RuntimeStage.DRAFT:
            raise EngineResourceNotFoundError(
                "friend models are available only in draft"
            )
        await authorize_friend_chat(
            request=request,
            bot_id=bot_id,
            caller_id=user_id,
            owner_id=owner_id,
            friend_user_id=f_user_id,
            friendships=friendships,
        )
    facts = await resolve_operable_bot(
        relay,
        bot_id,
        caller_id=owner_id if f_user_id is not None else user_id,
        owner_id=owner_id,
        stage=stage.value,
        surface="models",
    )
    result = await relay.call(
        bot_id=bot_id,
        owner_id=owner_id,
        facts=facts,
        stage=stage.value,
        method="GET",
        path=f"/api/models/{model_id}",
    )
    if not isinstance(result.data, dict):
        raise EngineResourceNotFoundError(f"no model {model_id}")
    return envelope(_map_model(result.data), request)
