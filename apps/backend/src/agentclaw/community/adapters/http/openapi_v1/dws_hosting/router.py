"""Owner-only DWS management relay. No shared database or chat policy changes."""

import hashlib
import hmac
import json
import time
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    caller_owner_id,
    refuse_app_only_caller,
    require_user_id,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.openapi_v1.response_envelope import (
    error_response,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.di import Injected
from agentclaw.community.di.dws_management_config import (
    DwsManagementConfig,
    get_dws_management_config,
)
from .schemas import EnabledUpdate, SettingsUpdate, TakeoverUpdate
from .admission import PATH
from agentclaw.community.api.system_config_service import SystemConfigServiceProtocol
from agentclaw.community.core.system_config.dws_rollout import is_allowed, is_stop
from agentclaw.community.plugin_api.dws_identity import DwsRolloutIdentity
from agentclaw.community.utils.env_utils import get_current_env
from starlette.concurrency import run_in_threadpool

router = APIRouter(
    prefix=PATH,
    tags=["dws-hosting"],
    route_class=PublicAPIRoute,
    dependencies=[Depends(refuse_app_only_caller), Depends(require_user_id)],
)


class OwnerEndpoint:
    def __init__(self, bot_id, actor, relay, config, rollout=None, identity=DwsRolloutIdentity()):
        self.bot_id, self.actor, self.relay, self.config = bot_id, actor, relay, config
        self.rollout = rollout
        self.identity = identity

    async def call(self, operation, data, request):
        facts = await self.relay.resolve_bot_off_loop(
            self.bot_id, self.actor, self.actor
        )
        if facts.owner_id != self.actor:
            return error_response(404, "Not Found", request)
        if facts.bot_type != "personal" or facts.active_engine != "openclaw":
            return error_response(
                409, "DWS hosting requires a personal OpenClaw Bot", request
            )
        allowed = await run_in_threadpool(
            is_allowed, self.rollout, self.actor, get_current_env(), self.identity
        )
        if operation == "eligibility":
            return envelope({"available": allowed}, request)
        # COSEC: UI visibility is not authorization; only safe stop operations
        # remain available after revocation. Identity comes from signed principal.
        if not allowed and not is_stop(operation, data):
            return error_response(403, "DWS hosting is not available", request)
        if not self.config.key:
            return error_response(503, "DWS hosting is not configured", request)
        # COSEC: sign only server-resolved identity and a fixed operation; never
        # forward a browser-supplied identity, destination, command or credential.
        payload = json.dumps(
            {
                "bot_id": self.bot_id,
                "actor_id": self.actor,
                "operation": operation,
                "data": data,
                "timestamp_ms": int(time.time() * 1000),
                "nonce": uuid4().hex,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        signature = hmac.new(
            self.config.bot_key(self.bot_id, self.actor),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        result = await self.relay.call(
            bot_id=self.bot_id,
            owner_id=self.actor,
            facts=facts,
            stage="draft",
            method="POST",
            path="/api/dws-hosting/command",
            body={"payload": payload, "signature": signature},
        )
        if isinstance(result.data, dict) and "management_error" in result.data:
            return error_response(
                409,
                "Hosting operation refused; refresh status and saved settings",
                request,
            )
        return envelope(result.data, request)


async def endpoint(
    bot_id: BotIdPath,
    principal: Annotated[Principal, Depends(require_principal)],
    config: Annotated[DwsManagementConfig, Depends(get_dws_management_config)],
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    rollout: SystemConfigServiceProtocol = Injected(SystemConfigServiceProtocol),
    identity: DwsRolloutIdentity = Injected(DwsRolloutIdentity),
):
    actor = caller_owner_id(principal)
    return OwnerEndpoint(bot_id, actor, relay, config, rollout, identity)


Endpoint = Annotated[OwnerEndpoint, Depends(endpoint)]


@router.get("/eligibility", response_model=Envelope[dict])
@envelope_errors
async def eligibility(request: Request, service: Endpoint):
    return await service.call("eligibility", {}, request)


@router.get("", response_model=Envelope[dict])
@envelope_errors
async def status(request: Request, service: Endpoint):
    return await service.call("status", {}, request)


@router.get("/settings", response_model=Envelope[dict])
@envelope_errors
async def settings(request: Request, service: Endpoint):
    return await service.call("settings.get", {}, request)


@router.put("/settings", response_model=Envelope[dict])
@envelope_errors
async def save_settings(body: SettingsUpdate, request: Request, service: Endpoint):
    return await service.call("settings.save", body.model_dump(), request)


@router.put("/enabled", response_model=Envelope[dict])
@envelope_errors
async def enabled(body: EnabledUpdate, request: Request, service: Endpoint):
    return await service.call("enabled", body.model_dump(), request)


@router.post("/authorization", response_model=Envelope[dict])
@envelope_errors
async def authorize(request: Request, service: Endpoint):
    return await service.call("authorization.start", {}, request)


@router.post("/authorization/check", response_model=Envelope[dict])
@envelope_errors
async def check_authorization(request: Request, service: Endpoint):
    return await service.call("authorization.check", {}, request)


@router.get("/conversations", response_model=Envelope[dict])
@envelope_errors
async def conversations(
    request: Request,
    service: Endpoint,
    offset: Annotated[
        int, Query(ge=0, description="Zero-based conversation offset.")
    ] = 0,
    limit: Annotated[
        int, Query(ge=1, le=100, description="Maximum conversations to return (1-100).")
    ] = 20,
):
    return await service.call(
        "conversations", {"offset": offset, "limit": limit}, request
    )


@router.put("/takeover", response_model=Envelope[dict])
@envelope_errors
async def takeover(body: TakeoverUpdate, request: Request, service: Endpoint):
    return await service.call("takeover", body.model_dump(), request)
