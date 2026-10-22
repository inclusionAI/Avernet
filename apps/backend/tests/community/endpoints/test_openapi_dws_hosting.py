"""Drive every DWS route through the real app and its signed principal boundary."""

import hashlib
import hmac
import json
import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.api.system_config_service import SystemConfigServiceProtocol
from agentclaw.community.core.engine_runtime.models import BotFacts, EngineResult
from agentclaw.community.di.dws_management_config import DwsManagementConfig
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)
from tests.community.framework.di_seams import bind_overrides

_OWNER = "dws-owner"
_BOT = "dws-bot"
_BASE = "/openapi/v1/bots/{bot_id}/dws-hosting"
_SIGNING_KEY = "dws-framework-principal-signing-key-for-offline-tests"


def management_config():
    return DwsManagementConfig(_SIGNING_KEY.encode())


class _Secret:
    secret_user = "test"
    secret_value = _SIGNING_KEY


class _Resolver:
    def get_secret(self, _name):
        return _Secret()


def _headers():
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "user",
                    "subject": {"id": _OWNER, "username": "dws-owner@example.test"},
                }
            ],
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )
    return {PRINCIPAL_HEADER: token}


def _seed(operation, expected_data):
    def seed(world):
        init_principal_verifier_config(_Resolver(), "test-key", strict=False)
        bind_overrides(
            world,
            SystemConfigServiceProtocol,
            {"get_config": lambda self, **kw: {"mode": "all"}},
        )

        async def resolve(_self, bot_id, owner_id, caller_id):
            assert (bot_id, owner_id, caller_id) == (_BOT, _OWNER, _OWNER)
            return BotFacts(
                bot_id=_BOT,
                owner_id=_OWNER,
                bot_type="personal",
                active_engine="openclaw",
            )

        async def call(
            _self, *, method, path, body, bot_id, owner_id, stage, **_kwargs
        ):
            assert (method, path, stage) == (
                "POST",
                "/api/dws-hosting/command",
                "draft",
            )
            assert (bot_id, owner_id) == (_BOT, _OWNER)
            payload = json.loads(body["payload"])
            assert (
                payload["operation"] == operation and payload["data"] == expected_data
            )
            assert payload["bot_id"] == _BOT and payload["actor_id"] == _OWNER
            assert (
                payload["nonce"]
                and abs(payload["timestamp_ms"] - time.time() * 1000) < 30000
            )
            assert hmac.compare_digest(
                body["signature"],
                hmac.new(
                    management_config().bot_key(_BOT, _OWNER),
                    body["payload"].encode(),
                    hashlib.sha256,
                ).hexdigest(),
            )
            return EngineResult(data={"operation": operation})

        bind_overrides(
            world,
            EngineRuntimeRelayProtocol,
            {"resolve_bot_off_loop": resolve, "call": call},
        )

    return seed


_SETTINGS = {
    "rules": "Reply to work questions",
    "window": "always",
    "custom_window": "",
    "timezone": "Asia/Shanghai",
    "persona": "",
    "label_suffix": "",
}
_CASES = (
    ("GET", "/eligibility", "eligibility", None, {}),
    ("GET", "", "status", None, {}),
    ("GET", "/settings", "settings.get", None, {}),
    (
        "PUT",
        "/settings",
        "settings.save",
        {"expected_version": 0, "settings": _SETTINGS},
        {"expected_version": 0, "settings": _SETTINGS},
    ),
    (
        "PUT",
        "/enabled",
        "enabled",
        {"expected_version": 1, "enabled": True},
        {"expected_version": 1, "enabled": True},
    ),
    ("POST", "/authorization", "authorization.start", None, {}),
    ("POST", "/authorization/check", "authorization.check", None, {}),
    ("GET", "/conversations", "conversations", None, {"offset": 0, "limit": 20}),
    (
        "PUT",
        "/takeover",
        "takeover",
        {"conversation_id": "c1", "taken_over": True},
        {"conversation_id": "c1", "taken_over": True},
    ),
)


for _method, _suffix, _operation, _body, _data in _CASES:
    endpoint_test(
        method=_method,
        path=_BASE + _suffix,
        scenario="dws_signed_owner_command",
        input=CaseInput(
            path_params={"bot_id": _BOT},
            query_params={"user_id": _OWNER},
            headers=_headers(),
            json_body=_body,
        ),
        seed=_seed(_operation, _data),
        expect=ExpectSuccess(
            status=200,
            json_contains={
                "code": 200000,
                "data": {"available": True}
                if _operation == "eligibility"
                else {"operation": _operation},
            },
        ),
    )(lambda: None)
    endpoint_test(
        method=_method,
        path=_BASE + _suffix,
        scenario="dws_wrong_user",
        input=CaseInput(
            path_params={"bot_id": _BOT},
            query_params={"user_id": "not-the-caller"},
            headers=_headers(),
            json_body=_body,
        ),
        seed=_seed(_operation, _data),
        expect=ExpectError(status=403, json_contains={"code": 403000, "data": None}),
    )(lambda: None)
