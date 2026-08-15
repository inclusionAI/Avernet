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
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.engine_config_service import EngineConfigServiceProtocol
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.di import Injected

router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/engine", tags=["bots"])


def _engine_config_target(bot: dict[str, Any]) -> tuple[str, str, str]:
    """Resolve (entity_id, entity_type, engine) for an engine-config call."""
    entity_id = bot.get("entity_id")
    if not entity_id:
        raise BotNotFoundError("bot has no associated entity")
    entity_type = bot.get("entity_type") or "staff"
    engine = bot.get("active_engine") or DEFAULT_ENGINE_TYPE
    return entity_id, entity_type, engine


@router.get(
    "/config",
    response_model=Envelope[dict[str, Any]],
    responses=USER_SCOPED_403,
)
@envelope_errors
async def get_bot_engine_config(
    bot_id: BotIdPath,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    engine_config_service: EngineConfigServiceProtocol = Injected(
        EngineConfigServiceProtocol
    ),
) -> Envelope[dict[str, Any]]:
    """Read a bot's engine configuration (free-form JSON)."""
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard
    entity_id, entity_type, engine = _engine_config_target(bot)
    data = await engine_config_service.read_bot_config(
        bot_id=bot_id, owner_id=owner_id, entity_id=entity_id,
        entity_type=entity_type, engine_type=engine,
    )
    return envelope(data, request)


@router.put(
    "/config",
    response_model=Envelope[dict[str, Any]],
    responses=USER_SCOPED_403,
)
@envelope_errors
async def update_bot_engine_config(
    bot_id: BotIdPath,
    body: dict[str, Any],
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    engine_config_service: EngineConfigServiceProtocol = Injected(
        EngineConfigServiceProtocol
    ),
) -> Envelope[dict[str, Any]]:
    """Write a bot's engine configuration (free-form JSON)."""
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard
    entity_id, entity_type, engine = _engine_config_target(bot)
    await engine_config_service.write_bot_config(
        bot_id=bot_id, owner_id=owner_id, entity_id=entity_id,
        entity_type=entity_type, engine_type=engine, config=body,
    )
    return envelope(body, request)
