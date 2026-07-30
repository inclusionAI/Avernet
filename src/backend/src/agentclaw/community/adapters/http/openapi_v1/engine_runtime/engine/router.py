"""Engine group (read-only) — ``/openapi/v1/bots/{bot_id}/engine``.

Three reads. ``switch`` and ``restart`` are deliberately **not** wrapped:
wrapping ``switch`` would be a back door around the rule that a bot's engine is
fixed at creation (``PUT /openapi/v1/bots/{bot_id}`` rejects it), and
``POST /openapi/v1/bots/{bot_id}/restart`` already re-provisions the device.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.engine.schemas import (
    EngineCapabilities,
    EngineInfo,
    EngineStatus,
)
from agentclaw.community.adapters.http.openapi_v1.principal import caller_owner_id
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.errors import EngineUpstreamError
from agentclaw.community.di import Injected

router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/engine", tags=["engine"])

PrincipalDep = Annotated[Principal, Depends(require_principal)]


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
    bot_id: str,
    principal: PrincipalDep,
    request: Request,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[EngineStatus]:
    """Runtime state of the bot's active engine."""
    owner_id = caller_owner_id(principal)
    # enveloped=False: this engine route answers with EngineManager.status()
    # raw — no `success`, no `data` wrapper. The only such route on this surface.
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, method="GET", path="/api/engine/status",
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
    bot_id: str,
    principal: PrincipalDep,
    request: Request,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[EngineCapabilities]:
    """What this bot's engine can do.

    The discovery endpoint for the whole engine-runtime surface: the supported
    set differs per engine, so the same public path can succeed on one of a
    caller's bots and answer 501 on another.
    """
    owner_id = caller_owner_id(principal)
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, method="GET",
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
    bot_id: str,
    principal: PrincipalDep,
    request: Request,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[list[EngineInfo]]:
    """Engines registered on the bot's device, with the active one marked.

    Publicly a noun (`available`); the engine models the same read as
    ``/api/engine/list``.
    """
    owner_id = caller_owner_id(principal)
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, method="GET", path="/api/engine/list",
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
