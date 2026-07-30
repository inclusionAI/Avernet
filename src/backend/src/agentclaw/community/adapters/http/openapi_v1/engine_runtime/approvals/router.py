"""Approvals group — ``/openapi/v1/bots/{bot_id}/approvals``."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.approvals.schemas import (
    ApprovalModeInfo,
    ApprovalModeSet,
    ApprovalState,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    ApprovalMode,
)
from agentclaw.community.adapters.http.openapi_v1.principal import caller_owner_id
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.errors import (
    EngineCapabilityUnsupportedError,
    EngineUpstreamError,
)
from agentclaw.community.di import Injected

router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/approvals", tags=["approvals"])

PrincipalDep = Annotated[Principal, Depends(require_principal)]

#: Engine capability the approval reads/writes need.
_APPROVAL_GET = "approval.get"

#: The public meaning of each advertised mode. Sourced from the engine's own
#: ``GET /api/approvals/modes`` labels, but written here in fixed English: the
#: engine's descriptions are Chinese ("每个操作都需要确认"), and this surface
#: promises English.
_MODE_DESCRIPTIONS: dict[ApprovalMode, str] = {
    ApprovalMode.APPROVE: "Ask before every action.",
    ApprovalMode.ON_MISS: "Ask only when the bot's policy cannot decide on its own.",
    ApprovalMode.NEVER: "Never ask; act autonomously.",
}


def _state(raw: Any, session_key: str) -> ApprovalState:
    data = raw if isinstance(raw, dict) else {}
    return ApprovalState(
        session_key=str(data.get("sessionKey") or session_key),
        mode=str(data.get("mode") or ""),
    )


def _reject_refused_set(raw: Any) -> None:
    """Raise when the engine acknowledged the write but refused it.

    ``exec.approvals.set`` reports two independent outcomes: the *call* worked
    (outer ``success``), and the mode change was *applied* (``data.ok``). The
    relay only sees the first, so a refusal arrives here as a success envelope
    whose payload says otherwise — and echoing the requested mode back would
    tell the caller a change took effect that did not.

    ``is False`` rather than falsy: the read route's payload carries no ``ok``
    at all, and a missing flag is not a refusal.
    """
    data = raw if isinstance(raw, dict) else {}
    if data.get("ok") is False:
        raise EngineUpstreamError("engine refused the approval-mode change")


@router.get("/mode", response_model=Envelope[ApprovalState])
@envelope_errors
async def get_approval_mode(
    bot_id: str,
    principal: PrincipalDep,
    request: Request,
    session_key: Annotated[str, Query(description="Session to read the mode for.")],
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[ApprovalState]:
    """Read the approval mode in force for a session."""
    # Publicly a GET with a query parameter; the engine models the same read as
    # a POST with a body.
    owner_id = caller_owner_id(principal)
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, method="POST",
        path="/api/approvals/mode/get",
        # user_id is filled from the principal; the engine uses it to route.
        body={"session_key": session_key, "user_id": owner_id},
    )
    return envelope(_state(result.data, session_key), request)


@router.put("/mode", response_model=Envelope[ApprovalState])
@envelope_errors
async def set_approval_mode(
    bot_id: str,
    body: ApprovalModeSet,
    principal: PrincipalDep,
    request: Request,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[ApprovalState]:
    """Set the approval mode for a session."""
    # The enum's value is forwarded verbatim: all three are already in the
    # engine's accept-set, so no translation is needed and none is applied.
    owner_id = caller_owner_id(principal)
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, method="POST",
        path="/api/approvals/mode/set",
        body={
            "session_key": body.session_key,
            "mode": body.mode.value,
            "user_id": owner_id,
        },
    )
    _reject_refused_set(result.data)
    return envelope(_state(result.data, body.session_key), request)


@router.get("/modes", response_model=Envelope[list[ApprovalModeInfo]])
@envelope_errors
async def list_approval_modes(
    bot_id: str,
    principal: PrincipalDep,
    request: Request,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[list[ApprovalModeInfo]]:
    """List the approval modes that can be set on this bot.

    Answers 501 when the bot does not support approvals, matching the read and
    write endpoints.
    """
    # Deliberate divergence: the engine's own modes route is its one route with
    # no capability gate, so on an engine declaring neither approval capability
    # it advertises three modes while get and set both answer 501. Gating here
    # keeps all three consistent per bot. The list is served from the public
    # enum rather than relayed, because the engine's descriptions are Chinese
    # and this surface promises English.
    owner_id = caller_owner_id(principal)
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, method="GET",
        path="/api/engine/capabilities",
    )
    caps = result.data if isinstance(result.data, dict) else {}
    supported = set(caps.get("supported") or []) | set(caps.get("limited") or [])
    if _APPROVAL_GET not in supported:
        raise EngineCapabilityUnsupportedError(
            f"engine does not declare {_APPROVAL_GET}"
        )
    return envelope(
        [
            ApprovalModeInfo(value=mode, description=text)
            for mode, text in _MODE_DESCRIPTIONS.items()
        ],
        request,
    )
