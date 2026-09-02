"""A bot's stored engine configuration — ``/openapi/v1/bots/{bot_id}/engine/config``.

Two operations, on their own router for one reason: they belong beside the
engine group by address and beside the bots component by contract.

**By address**, this is engine configuration, and a reader looking for what a
bot's engine does should find `/engine/status`, `/engine/capabilities`,
`/engine/available` and `/engine/config` together. It used to sit at
`/{bot_id}/engine-config`, one segment away from `/{bot_id}/engine/status` and
related to it only by a hyphen.

**By contract**, it is nothing like them. The engine-runtime groups reach a
live runtime and document a 501 and a 504 for the two ways that fails; reading
a stored configuration can produce neither. Mounting these with
``ENGINE_RUNTIME_ERROR_RESPONSES`` would publish two failures a client could
never receive, and the surface-wide test that asserts every operation documents
its table would enforce the lie.

Path prefix and mount are independent, so both are had for the price of one
``include_router``: the address sits under ``engine``, the error table is the
ordinary one.

The body is free-form JSON, passed to the device verbatim. It is the only
request body on this surface with no declared fields, which is why
``test_no_duplicate_request_fields`` cannot see into it — there is nothing to
see, and the bot, owner and entity all travel as arguments beside it rather
than inside it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Envelope,
    USER_SCOPED_403,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    RuntimeStage,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    StageQuery,
    WriteStageQuery,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.engine_config_service import EngineConfigServiceProtocol
from agentclaw.community.core.services.engine_config import (
    engine_config_coords_from_record,
)
from agentclaw.community.di import Injected
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/engine", tags=["bots"], route_class=PublicAPIRoute)


#: Where an engine-config call writes, and the ownership guard in the same
#: call. Lives in ``core`` so manifest apply reaches the identical resolution
#: without a request; bound here so both handlers below read as they did.
_engine_config_coords = engine_config_coords_from_record


#: Named explicitly, and the only two operations on this surface that are.
#:
#: FastAPI's default id is the handler's name plus the path with every non-word
#: character replaced by an underscore — under which ``…/engine-config`` and
#: ``…/engine/config`` collapse to the *same* string, because ``-`` and ``/``
#: both become ``_``. The retiring address keeps the id it published, so these
#: two need one of their own or the document carries duplicate operation ids and
#: a generated client picks one at random. Short rather than long-form because
#: they are new: no client has generated against them yet, so the readable
#: spelling costs nothing.
@router.get(
    "/config",
    response_model=Envelope[dict[str, Any]],
    responses=USER_SCOPED_403,
    operation_id="get_bot_engine_config",
)
@envelope_errors
async def get_bot_engine_config(
    bot_id: BotIdPath,
    request: Request,
    owner_id: UserIdDep,
    stage: StageQuery = RuntimeStage.DRAFT,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    engine_config_service: EngineConfigServiceProtocol = Injected(
        EngineConfigServiceProtocol
    ),
) -> Envelope[dict[str, Any]]:
    """Read a bot's engine configuration (free-form JSON).

    Reads the runtime named by the stage parameter — the bot's own workspace
    unless a published one is asked for.

    A bot whose engine configuration has never been written reads as an empty
    object — every engine keeps this configuration in a file its runtime creates
    on first use, so "not configured yet" is an ordinary state, not an error.
    """
    coords = _engine_config_coords(bot_id, owner_id, bot_service=bot_service)
    entity_id, entity_type, engine = (
        coords.entity_id, coords.entity_type, coords.engine_type
    )
    data = await engine_config_service.read_bot_config(
        bot_id=bot_id, owner_id=owner_id, entity_id=entity_id,
        entity_type=entity_type, engine_type=engine, stage=stage.value,
    )
    return envelope(data, request)


@router.put(
    "/config",
    response_model=Envelope[dict[str, Any]],
    responses=USER_SCOPED_403,
    operation_id="update_bot_engine_config",
)
@envelope_errors
async def update_bot_engine_config(
    bot_id: BotIdPath,
    body: dict[str, Any],
    request: Request,
    owner_id: UserIdDep,
    stage: WriteStageQuery = RuntimeStage.DRAFT,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    engine_config_service: EngineConfigServiceProtocol = Injected(
        EngineConfigServiceProtocol
    ),
) -> Envelope[dict[str, Any]]:
    """Write a bot's engine configuration (free-form JSON).

    Writes the bot's own workspace. A published runtime is what a release
    produced and is replaced by publishing again, never edited, so naming one is
    refused and nothing is written.
    """
    coords = _engine_config_coords(bot_id, owner_id, bot_service=bot_service)
    entity_id, entity_type, engine = (
        coords.entity_id, coords.entity_type, coords.engine_type
    )
    await engine_config_service.write_bot_config(
        bot_id=bot_id, owner_id=owner_id, entity_id=entity_id,
        entity_type=entity_type, engine_type=engine, config=body,
        stage=stage.value,
    )
    return envelope(body, request)
