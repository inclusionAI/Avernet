"""Engine group — ``/openapi/v1/bots/{bot_id}/engine``.

An **operator console**: served to the addressed bot's owner and its
member-level collaborators, for the stage the request names (``?stage=``,
draft by default), and device-wide — see ``engine_runtime/gating.py`` and
``core/engine_runtime/gate.py``.

Three reads, one write:

- ``switch`` is deliberately **not** wrapped: it would be a back door around the
  rule that a bot's engine is fixed at creation (``PUT /openapi/v1/bots/{bot_id}``
  rejects it). The legacy ``POST /api/bots/switch-engine`` was the last surface
  that still flipped ``active_engine`` after creation; it has been removed, so
  the rule now holds across every surface rather than only the public one.
- ``restart`` *is* wrapped here as ``POST /openapi/v1/bots/{bot_id}/engine/restart``,
  relaying the device-side engine daemon's ``POST /api/engine/restart`` (**not**
  ``shell exec supervisorctl``). This is *not* the same verb as
  ``POST /openapi/v1/bots/{bot_id}/restart`` — that one re-provisions the whole
  container via BaaS (``restart_bot``), dropping sessions; this one restarts only
  the engine process and leaves the container/session state alone. The legacy
  frontend reached the engine daemon directly via the agentclawproxy proxypass to
  ``<binding>:20003/api/engine/restart``; the public openapi surface wraps that same
  device call behind ``EngineRuntimeRelay``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.engine.schemas import (
    EngineCapabilities,
    EngineInfo,
    EngineRestartResult,
    EngineStatus,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    RuntimeStage,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
    StageQuery,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.gating import (
    resolve_operable_bot,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.errors import EngineUpstreamError
from agentclaw.community.di import Injected
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/engine", tags=["engine"], route_class=PublicAPIRoute)


def _names(raw: Any) -> list[str]:
    """Capability names from either a list or a ``{name: explanation}`` map.

    The engine reports ``supported`` as a list but ``limited`` and ``fallback``
    as dicts whose **values are internal engineering text** — English-only by
    accident, e.g. "通过 mcporter 命令启动". Only the keys are published: they
    are the stable capability vocabulary, and they leak nothing.
    """
    if isinstance(raw, dict):
        return sorted(str(k) for k in raw)
    if isinstance(raw, list):
        return sorted(str(v) for v in raw)
    return []


@router.get("/status", response_model=Envelope[EngineStatus])
@envelope_errors
async def get_engine_status(
    bot_id: BotIdPath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[EngineStatus]:
    """Runtime state of the bot's engine."""
    facts = await resolve_operable_bot(
        relay,
        bot_id,
        caller_id=user_id,
        owner_id=owner_id,
        stage=stage.value,
        surface="engine",
    )
    # enveloped=False: this engine route answers with its status payload raw —
    # no `success` key and no `data` wrapper. The only such route wrapped here.
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, facts=facts, stage=stage.value,
        method="GET", path="/api/engine/status",
        enveloped=False,
    )
    raw = result.data if isinstance(result.data, dict) else {}
    process = raw.get("process") if isinstance(raw.get("process"), dict) else {}
    # `process` and `transition` are open dicts assembled ad hoc by the engine;
    # only the one field with a stable meaning is published.
    return envelope(
        EngineStatus(
            engine=str(raw.get("engine") or ""),
            active_connections=int(raw.get("active_connections") or 0),
            running=bool(process.get("running", False)),
        ),
        request,
    )


@router.get("/capabilities", response_model=Envelope[EngineCapabilities])
@envelope_errors
async def get_engine_capabilities(
    bot_id: BotIdPath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[EngineCapabilities]:
    """What this bot can do.

    The discovery endpoint for these groups: capabilities differ per bot, so the
    same request can succeed for one of your bots and be refused for another.
    """
    facts = await resolve_operable_bot(
        relay,
        bot_id,
        caller_id=user_id,
        owner_id=owner_id,
        stage=stage.value,
        surface="engine",
    )
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, facts=facts, stage=stage.value,
        method="GET",
        path="/api/engine/capabilities",
    )
    raw = result.data if isinstance(result.data, dict) else {}
    return envelope(
        EngineCapabilities(
            supported=_names(raw.get("supported")),
            limited=_names(raw.get("limited")),
            # The engine calls these "fallback": declared, not served directly,
            # with an internal note on how to achieve it another way. From a
            # caller's side that is simply unavailable — and the note is
            # internal text, so only the names cross the boundary.
            unavailable=_names(raw.get("fallback")),
        ),
        request,
    )


@router.get("/available", response_model=Envelope[list[EngineInfo]])
@envelope_errors
async def list_available_engines(
    bot_id: BotIdPath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[list[EngineInfo]]:
    """Engines available on this bot, with the active one marked."""
    # Publicly a noun; the engine models the same read under a verb path.
    facts = await resolve_operable_bot(
        relay,
        bot_id,
        caller_id=user_id,
        owner_id=owner_id,
        stage=stage.value,
        surface="engine",
    )
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, facts=facts, stage=stage.value,
        method="GET", path="/api/engine/list",
    )
    raw = result.data
    if isinstance(raw, dict):
        raw = raw.get("engines") or raw.get("items") or []
    if not isinstance(raw, list):
        raise EngineUpstreamError("engine list payload is not a list")
    return envelope(
        [
            EngineInfo(
                engine=str(e.get("name") or e.get("engine") or ""),
                version=str(e.get("version") or ""),
                active=bool(e.get("active", False)),
            )
            for e in raw
            if isinstance(e, dict)
        ],
        request,
    )


@router.post(
    "/restart",
    response_model=Envelope[EngineRestartResult],
)
@envelope_errors
async def restart_bot_engine(
    bot_id: BotIdPath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[EngineRestartResult]:
    """Restart the bot's engine process.

    Relays the device-side engine daemon's own restart endpoint — the same one
    the legacy frontend reached through the gateway proxy to the bot's binding.
    The daemon owns the restart; the public surface here only resolves the
    addressed bot, checks the caller is its operator, and forwards the call,
    mirroring the three read routes in this group. The restart is in-flight by
    the time the response returns; confirm completion via the engine status
    endpoint.

    This is NOT the bot-level restart endpoint: that one re-provisions the
    whole container via BaaS and drops sessions; this one restarts only the
    engine process. See the module docstring for the verb split.
    """
    facts = await resolve_operable_bot(
        relay,
        bot_id,
        caller_id=user_id,
        owner_id=owner_id,
        stage=stage.value,
        surface="engine",
    )
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, facts=facts, stage=stage.value,
        method="POST", path="/api/engine/restart",
        # The device endpoint is typed ``engine_restart(request:
        # EngineRestartRequest)``: FastAPI rejects a bodyless POST with 422
        # (which the relay would flatten into a public 502) however optional
        # the model's fields are. Always carry the JSON envelope; force stays
        # off until a public contract exposes it.
        body={"force": False},
    )
    raw = result.data if isinstance(result.data, dict) else {}
    return envelope(
        EngineRestartResult(
            bot_id=bot_id,
            status=str(raw.get("status") or ""),
        ),
        request,
    )
