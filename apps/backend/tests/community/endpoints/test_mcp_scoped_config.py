"""Assembled HTTP coverage for both MCP Header-group adapters."""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.mcp_scoped_config_service import MCPScopedConfigServiceProtocol
from agentclaw.community.core.mcp.scoped_config_contract import (
    HeaderGroup,
    ScopedMCPConfig,
    URLRule,
)
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)

_OWNER = "scoped-mcp-owner"
_SERVER = "mcp.weather"
_KEY = "mcp-scoped-framework-signing-key-32-bytes"
_PUBLIC = "/openapi/v1/bots/mcp/servers/{server_code}/config-groups"
_INTERNAL = "/api/mcp/config-groups"
_BODY = {
    "endpoint_env": "PROD",
    "transport_protocol": "SSE",
    "params": [{"key": "X-Region", "value": "east", "bots": []}],
    "url_rules": [{"url": "https://example.test/mcp", "bots": ["bot-x"]}],
}


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway", "aud": "backend", "iat": now, "exp": now + 3600,
            "principals": [{
                "type": "user",
                "subject": {"id": _OWNER, "username": "scoped@example.test"},
            }],
        },
        _KEY,
        algorithm="HS256",
    )


_PUBLIC_HEADERS = {PRINCIPAL_HEADER: _principal()}
_INTERNAL_HEADERS = {"x-user-id": _OWNER}


def _seed_auth(world) -> None:
    make_staff_user(world, user_id=_OWNER)
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_scoped_service(world) -> None:
    _seed_auth(world)

    def read(_self, *, user_id: str, server_code: str) -> ScopedMCPConfig:
        assert user_id == _OWNER
        assert server_code == _SERVER
        return ScopedMCPConfig(
            server_code=server_code, endpoint_env="PROD",
            transport_protocol="SSE",
            params=(HeaderGroup(key="X-Region", value="east", bots=()),),
            url_rules=(URLRule(url="https://example.test/mcp", bots=("bot-x",)),),
        )

    async def replace(_self, **kwargs) -> ScopedMCPConfig:
        assert kwargs["user_id"] == _OWNER
        assert kwargs["server_code"] == _SERVER
        assert kwargs["params"] == (HeaderGroup(key="X-Region", value="east", bots=()),)
        assert kwargs["url_rules"] == (
            URLRule(url="https://example.test/mcp", bots=("bot-x",)),
        )
        return read(_self, user_id=_OWNER, server_code=_SERVER)

    bind_overrides(
        world, MCPScopedConfigServiceProtocol,
        {"read": read, "replace": replace},
    )


@endpoint_test(
    method="GET", path=_INTERNAL, scenario="scoped_read",
    input=CaseInput(query_params={"server_code": _SERVER}, headers=_INTERNAL_HEADERS),
    seed=_seed_scoped_service,
    expect=ExpectSuccess(status=200, json_contains={"data": {
        "params": _BODY["params"], "url_rules": _BODY["url_rules"],
    }}),
)
def internal_read():
    pass


@endpoint_test(
    method="GET", path=_INTERNAL, scenario="missing_server_code",
    input=CaseInput(headers=_INTERNAL_HEADERS), seed=_seed_auth,
    expect=ExpectError(status=422),
)
def internal_read_error():
    pass


@endpoint_test(
    method="POST", path=_INTERNAL, scenario="scoped_replace",
    input=CaseInput(
        headers=_INTERNAL_HEADERS, json_body={"server_code": _SERVER, **_BODY}
    ),
    seed=_seed_scoped_service,
    expect=ExpectSuccess(status=200, json_contains={"data": {
        "params": _BODY["params"], "url_rules": _BODY["url_rules"],
    }}),
)
def internal_replace():
    pass


@endpoint_test(
    method="POST", path=_INTERNAL, scenario="missing_params",
    input=CaseInput(
        headers=_INTERNAL_HEADERS,
        json_body={"server_code": _SERVER, "endpoint_env": "PROD", "transport_protocol": "SSE"},
    ),
    seed=_seed_auth, expect=ExpectError(status=422),
)
def internal_replace_error():
    pass


@endpoint_test(
    method="GET", path=_PUBLIC, scenario="scoped_read",
    input=CaseInput(
        path_params={"server_code": _SERVER}, query_params={"user_id": _OWNER},
        headers=_PUBLIC_HEADERS,
    ),
    seed=_seed_scoped_service,
    expect=ExpectSuccess(status=200, json_contains={"data": {
        "params": _BODY["params"], "url_rules": _BODY["url_rules"],
    }}),
)
def public_read():
    pass


@endpoint_test(
    method="GET", path=_PUBLIC, scenario="missing_user_id",
    input=CaseInput(path_params={"server_code": _SERVER}, headers=_PUBLIC_HEADERS),
    seed=_seed_auth, expect=ExpectError(status=422),
)
def public_read_error():
    pass


@endpoint_test(
    method="PUT", path=_PUBLIC, scenario="scoped_replace",
    input=CaseInput(
        path_params={"server_code": _SERVER}, query_params={"user_id": _OWNER},
        headers=_PUBLIC_HEADERS, json_body=_BODY,
    ),
    seed=_seed_scoped_service,
    expect=ExpectSuccess(status=200, json_contains={"data": {
        "params": _BODY["params"], "url_rules": _BODY["url_rules"],
    }}),
)
def public_replace():
    pass


@endpoint_test(
    method="PUT", path=_PUBLIC, scenario="missing_params",
    input=CaseInput(
        path_params={"server_code": _SERVER}, query_params={"user_id": _OWNER},
        headers=_PUBLIC_HEADERS,
        json_body={"endpoint_env": "PROD", "transport_protocol": "SSE"},
    ),
    seed=_seed_auth, expect=ExpectError(status=422),
)
def public_replace_error():
    pass


@endpoint_test(
    method="PUT", path=_PUBLIC, scenario="null_url_rules",
    input=CaseInput(
        path_params={"server_code": _SERVER}, query_params={"user_id": _OWNER},
        headers=_PUBLIC_HEADERS, json_body={**_BODY, "url_rules": None},
    ),
    seed=_seed_auth, expect=ExpectError(status=422),
)
def public_null_url_rules_error():
    pass


_BOT_CONFIG = "/openapi/v1/bots/{bot_id}/mcps/{server_code}/config"
_BOT_ID = "scoped-mcp-bot"
_BOT_CONFIG_INPUT = {
    "path_params": {"bot_id": _BOT_ID, "server_code": _SERVER},
    "query_params": {"user_id": _OWNER},
    "headers": _PUBLIC_HEADERS,
}


def _seed_addressed_bot(world) -> None:
    _seed_auth(world)
    make_bot(world, bot_id=_BOT_ID, owner_id=_OWNER)


@endpoint_test(
    method="GET", path=_BOT_CONFIG, scenario="read_explicit_overrides",
    input=CaseInput(**_BOT_CONFIG_INPUT), seed=_seed_addressed_bot,
    expect=ExpectSuccess(status=200, json_contains={
        "data": {"server_code": _SERVER, "headers": {}, "url": None}
    }),
)
def bot_config_read():
    pass


@endpoint_test(
    method="GET", path=_BOT_CONFIG, scenario="missing_addressed_bot",
    input=CaseInput(**_BOT_CONFIG_INPUT), seed=_seed_auth,
    expect=ExpectError(status=404),
)
def bot_config_read_missing():
    pass


@endpoint_test(
    method="PUT", path=_BOT_CONFIG, scenario="persist_bot_overrides",
    input=CaseInput(**_BOT_CONFIG_INPUT, json_body={
        "headers": {"X-Region": "east"}, "url": "https://bot.example.invalid/mcp",
    }),
    seed=_seed_addressed_bot,
    expect=ExpectSuccess(status=200, json_contains={"data": {
        "headers": {"X-Region": "****"}, "url": "****",
        "desired_state": {"changed": True, "status": "COMMITTED"},
    }}),
)
def bot_config_write():
    pass


@endpoint_test(
    method="PUT", path=_BOT_CONFIG, scenario="refuse_non_http_url",
    input=CaseInput(**_BOT_CONFIG_INPUT, json_body={"url": "file:///tmp/mcp"}),
    seed=_seed_addressed_bot, expect=ExpectError(status=400),
)
def bot_config_write_invalid_url():
    pass
