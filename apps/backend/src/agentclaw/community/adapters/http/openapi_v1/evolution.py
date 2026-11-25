"""Thin RSI ingress: shared principal/Bot gates, then internal service delivery.

No scheduler, database, user login, worker execution or promotion lives here.
Only the explicit control operations below can be relayed, never Worker routes.
"""

from collections.abc import Awaitable, Callable
from functools import partial
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_management.addressing import ResolvedBotContext
from agentclaw.community.di import Injected
from agentclaw.community.di.evolution_control_config import (
    EvolutionControlClientFactory,
    EvolutionControlConfig,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.gateway_principal_config import (
    get_principal_verifier_config,
)
from avernet_evolution.adapters.backend_access import HEADER, fingerprint, issue_grant
from avernet_evolution.control_contract import (
    BindingsBody,
    CancelBody,
    ReviewBody,
    RunBody,
)
from avernet_genome.models import BotRef
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response

from .authorization import PublicAPIRoute
from .authorization_rules_evolution import BASE
from .bot_access import resolve_bot_permission, resolve_legacy_bot
from .bots.config_manifest_support import audit_actor
from .contracts import Envelope
from .engine_runtime.params import OwnerIdDep
from .errors import BotAccessRefusedError
from .principal import ActingCallerDep, UserIdDep

router = APIRouter(prefix=BASE, tags=["evolution"], route_class=PublicAPIRoute)
BotDep = Annotated[ResolvedBotContext, Depends(resolve_legacy_bot)]
ConfigDep = Annotated[EvolutionControlConfig, Injected(EvolutionControlConfig)]
ClientDep = Annotated[
    EvolutionControlClientFactory, Injected(EvolutionControlClientFactory)
]


async def delivery(
    request: Request,
    bot: BotDep,
    user_id: UserIdDep,
    caller: ActingCallerDep,
    config: ConfigDep,
    clients: ClientDep,
) -> Callable[[], Awaitable[Response]]:
    # Construct the delivery only. Handler validation must finish before any
    # network side effect, including idempotent submission or binding writes.
    return partial(relay, request, bot, user_id, caller, config, clients)


async def relay(request, bot, user_id, caller, config, clients) -> Response:
    if not config.upstream:
        raise HTTPException(503, "Evolution service is not configured")
    key = get_principal_verifier_config().signing_key
    if len(key.encode()) < 32:
        raise HTTPException(503, "Evolution service trust is not configured")
    # Bot facts come from the very same cached dependency the shared gate used.
    # entity_id is stable resource scope; owner is NOT the acting user.
    target = BotRef(
        entity_id=bot.entity_id,
        bot_id=bot.bot_id,
        owner_id=bot.current_owner_id,
        engine_type=str(bot.bot.get("active_engine") or ""),
        bot_type=str(bot.bot.get("bot_type") or "personal"),
        entity_type=str(bot.bot.get("entity_type") or "staff"),
    )
    body = await request.body()
    if len(body) > 512 * 1024:
        raise HTTPException(413, "Evolution control request too large")
    # Path is reconstructed from route-owned suffix and encoded path parameters;
    # neither request URL nor client headers may choose a host or worker API.
    route_path = request.scope["route"].path
    suffix = route_path.removeprefix(BASE)
    tenant = get_current_avernet_tenant()
    operator = user_id if config.builtin_data_tenant == tenant and not caller.is_application else None
    if operator is not None and request.method == "POST" and suffix == "/runs":
        level = await resolve_bot_permission(
            request, bot_id=bot.bot_id, caller_id=operator, owner_id=bot.entity_id,
            explicit=True, resolved_bot=bot,
        )
        if level < PermissionLevel.ADMIN:
            raise BotAccessRefusedError("Bot access unavailable")
    if "run_id" in request.path_params:
        suffix = suffix.replace(
            "{run_id}", quote(request.path_params["run_id"], safe="")
        )
    if "candidate_id" in request.path_params:
        suffix = suffix.replace(
            "{candidate_id}", quote(request.path_params["candidate_id"], safe="")
        )
    path = f"/openapi/v1/bots/{quote(bot.bot_id, safe='')}/evolution{suffix}"
    params = {
        name: request.query_params[name]
        for name in ("page", "page_size", "after", "status", "min_tier")
        if name in request.query_params
    }
    params["entity_id"] = bot.entity_id
    headers = {
        name: request.headers[name]
        for name in ("if-match", "idempotency-key")
        if name in request.headers
    }
    headers["content-type"] = "application/json"
    try:
        # No ambient proxy, cookie jar, redirect or automatic retry. A submit
        # timeout is unconfirmed; the UI keeps its existing idempotency key.
        async with clients.open() as client:
            outgoing = client.build_request(
                request.method, path, params=params, headers=headers, content=body
            )
            outgoing.headers[HEADER] = issue_grant(
                key=key,
                bot=target,
                actor=audit_actor(caller, user_id),
                tenant=tenant,
                operator_user_id=operator,
                request_hash=fingerprint(
                    request.method,
                    outgoing.url.path,
                    outgoing.url.query.decode(),
                    outgoing.content,
                    outgoing.headers,
                ),
            )
            upstream = await client.send(outgoing, stream=True)
            try:
                chunks, size = [], 0
                async for chunk in upstream.aiter_bytes():
                    size += len(chunk)
                    if size > 5 * 1024 * 1024:
                        raise HTTPException(502, "Invalid Evolution response")
                    chunks.append(chunk)
                content = b"".join(chunks)
            finally:
                await upstream.aclose()
    except httpx.TimeoutException:
        raise HTTPException(504, "Evolution operation unconfirmed") from None
    except httpx.HTTPError:
        raise HTTPException(502, "Evolution service unavailable") from None
    if upstream.status_code not in {
        200,
        202,
        400,
        403,
        404,
        409,
        412,
        422,
        428,
        500,
        503,
    }:
        raise HTTPException(502, "Invalid Evolution response")
    # Forward only the sanitized service envelope, not cookies or redirects.
    import json

    try:
        data = json.loads(content)
        if (
            not isinstance(data, dict)
            or data.get("code") != upstream.status_code * 1000
        ):
            raise ValueError("invalid envelope")
    except (ValueError, TypeError):
        raise HTTPException(502, "Invalid Evolution response") from None
    return Response(
        content,
        status_code=upstream.status_code,
        media_type="application/json",
        headers={
            name: upstream.headers[name]
            for name in ("etag", "x-trace-id")
            if name in upstream.headers
        },
    )


Reply = Annotated[Callable[[], Awaitable[Response]], Depends(delivery)]
RunId = Annotated[
    str, Path(description="Evolution run ID scoped to the addressed Bot.")
]
CandidateId = Annotated[
    str, Path(description="Candidate content hash, e.g. sha256:c41e…")
]


@router.get("/bindings", response_model=Envelope[dict[str, Any]])
async def bindings(owner_id: OwnerIdDep, reply: Reply):
    return await reply()


@router.put("/bindings", response_model=Envelope[dict[str, Any]])
async def put_bindings(owner_id: OwnerIdDep, payload: BindingsBody, reply: Reply):
    return await reply()


@router.post("/runs", status_code=202, response_model=Envelope[dict[str, Any]])
async def submit(owner_id: OwnerIdDep, payload: RunBody, reply: Reply):
    return await reply()


@router.get("/runs", response_model=Envelope[dict[str, Any]])
async def runs(
    owner_id: OwnerIdDep,
    reply: Reply,
    page: int = Query(1, ge=1, description="One-based page number."),
    page_size: int = Query(
        20, ge=1, le=100, description="Maximum runs returned per page."
    ),
):
    return await reply()


@router.get("/runs/{run_id}", response_model=Envelope[dict[str, Any]])
async def run(owner_id: OwnerIdDep, run_id: RunId, reply: Reply):
    return await reply()


@router.post("/runs/{run_id}:cancel", response_model=Envelope[dict[str, Any]])
async def cancel(
    owner_id: OwnerIdDep, run_id: RunId, payload: CancelBody, reply: Reply
):
    return await reply()


@router.get("/runs/{run_id}/candidates", response_model=Envelope[dict[str, Any]])
async def candidates(owner_id: OwnerIdDep, run_id: RunId, reply: Reply):
    return await reply()


@router.get("/runs/{run_id}/evidence", response_model=Envelope[dict[str, Any]])
async def evidence(
    owner_id: OwnerIdDep,
    run_id: RunId,
    reply: Reply,
    after: int = Query(
        0, ge=0, description="Return evidence events after this cursor."
    ),
):
    return await reply()


@router.get("/strategies", response_model=Envelope[dict[str, Any]])
async def strategies(owner_id: OwnerIdDep, reply: Reply):
    return await reply()


# -- 09 §10.1 candidate report and review queue ------------------------------------------------
# Same relay, same Bot gate; the report joins verification, gate and review state.


@router.get("/candidates/{candidate_id}", response_model=Envelope[dict[str, Any]])
async def candidate_report(
    owner_id: OwnerIdDep, candidate_id: CandidateId, reply: Reply
):
    return await reply()


@router.get("/review-queue", response_model=Envelope[dict[str, Any]])
async def review_queue(
    owner_id: OwnerIdDep,
    reply: Reply,
    status: str = Query(
        "open", description="open, approved, rejected, superseded or all."
    ),
    min_tier: str | None = Query(None, description="Lowest risk tier to include."),
    page: int = Query(1, ge=1, description="One-based page number."),
    page_size: int = Query(
        20, ge=1, le=100, description="Maximum review items returned per page."
    ),
):
    return await reply()


@router.post(
    "/candidates/{candidate_id}:approve", response_model=Envelope[dict[str, Any]]
)
async def approve_candidate(
    owner_id: OwnerIdDep,
    candidate_id: CandidateId,
    payload: ReviewBody,
    reply: Reply,
):
    return await reply()


@router.post(
    "/candidates/{candidate_id}:reject", response_model=Envelope[dict[str, Any]]
)
async def reject_candidate(
    owner_id: OwnerIdDep,
    candidate_id: CandidateId,
    payload: ReviewBody,
    reply: Reply,
):
    return await reply()
