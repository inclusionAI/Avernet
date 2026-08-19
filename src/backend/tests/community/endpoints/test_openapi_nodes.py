"""Endpoint-framework coverage for the public node inventory."""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.engine_runtime.models import BotFacts, EngineResult
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_OWNER = "openapi-nodes-owner"
_BOT = "openapi-nodes-bot"
_MISSING_BOT = "openapi-nodes-missing"
_KEY = "openapi-nodes-signing-key-at-least-32-bytes"
_PATH = "/openapi/v1/bots/{bot_id}/nodes"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "user",
                    "subject": {
                        "id": _OWNER,
                        "username": f"{_OWNER}@example.test",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


class _NodeRelay:
    async def resolve_bot_off_loop(
        self, bot_id: str, owner_id: str, caller_id: str
    ) -> BotFacts:
        if (bot_id, owner_id, caller_id) != (_BOT, _OWNER, _OWNER):
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        return BotFacts(
            bot_id=_BOT,
            bot_type="personal",
            active_engine="openclaw",
            owner_id=_OWNER,
        )

    async def call(self, **_kwargs) -> EngineResult:
        return EngineResult(
            data=[
                {
                    "nodeId": "node-01",
                    "displayName": "Desktop",
                    "platform": "darwin",
                    "version": "1.2.0",
                    "capabilities": ["screen"],
                    "commands": ["system.run"],
                    "remoteIp": "203.0.113.10",
                    "status": "online",
                }
            ]
        )


def _seed(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    world.injector.binder.bind(
        EngineRuntimeRelayProtocol,
        to=_NodeRelay(),
        scope=None,
    )


_INPUT = {
    "query_params": {"user_id": _OWNER},
    "headers": {PRINCIPAL_HEADER: _principal()},
}


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="lists_runtime_nodes",
    input=CaseInput(path_params={"bot_id": _BOT}, **_INPUT),
    seed=_seed,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": [
                {
                    "node_id": "node-01",
                    "display_name": "Desktop",
                    "platform": "darwin",
                    "status": "online",
                }
            ],
        },
    ),
)
def list_nodes_ok():
    """The assembled public route returns the stable node projection."""


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="unknown_bot",
    input=CaseInput(path_params={"bot_id": _MISSING_BOT}, **_INPUT),
    seed=_seed,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def list_nodes_unknown_bot():
    """An absent or unauthorized Bot is masked before the device call."""
