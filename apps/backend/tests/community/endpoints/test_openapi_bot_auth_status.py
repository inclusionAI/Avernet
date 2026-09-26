"""Endpoint-framework coverage for the public auth-status poll (POST).

Exercised through the assembled public app: the real gateway-principal
verification, the engine registry check, and — because the community passport
plugin answers ISSUED — the real completion path, ending in an actually
created bot. A **user** principal is enough: ``require_granted_bot`` is a
no-op for a human caller.

The retiring GET spelling stays on ``coverage_baseline.txt`` with the rest of
the deprecated addresses; behavioural parity between the two spellings is
pinned in ``tests/…/openapi_v1/test_bots_endpoints.py``, which drives both
through one mocked harness.
"""

from __future__ import annotations

import time
from typing import Annotated

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.system_config import SystemConfigService
from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_BAAS
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
    http_envelope_response,
)


_OWNER = "auth-status-owner"
_BOT_ID = "auth-status-poll-bot"
_KEY = "auth-status-framework-signing-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    """A gateway-signed principal naming a user and no application."""
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
                    "subject": {"id": _OWNER, "username": "auth@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER}


def _seed_verifier(world) -> None:
    """Only the verifier — the bot is created by the poll itself."""
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_baas_template_config(world) -> None:
    """Seed the BaaS template routing config device allocation resolves."""
    env = get_current_env()
    service = world.get(SystemConfigService)
    try:
        service.create_category(
            category="system",
            category_name="System",
            description="endpoint test",
            env=env,
            operator="endpoint-test",
        )
    except Exception:
        pass  # Category may already exist
    service.set_config(
        category="system",
        config_key="baas_template_uid_routing_config",
        config_value={
            "version": "test-1",
            "selectors": [{"engine": "openclaw", "template_uid": "test_openclaw"}],
            "templates": {
                "test_openclaw": {"template_uuid": "TEMPLATE-test-openclaw-001"},
            },
        },
        env=env,
        operator="endpoint-test",
    )


def _install_baas(world) -> None:
    """Stub the BaaS edge so device allocation for the created bot succeeds."""

    def _get(path: str, **_kw):
        if "/progress" in path:
            return http_envelope_response(
                {
                    "status": "SUCCESS",
                    "device_details": [],
                    "overall_progress": {},
                    "failed_devices": [],
                }
            )
        return http_envelope_response({})

    def _post(path: str, **_kw):
        return http_envelope_response(
            {"bot_uuid": "BOT-auth-status-001", "publish_id": 991}
        )

    client = world.get(Annotated[HttpClient, QUALIFIER_BAAS])
    client.set_override("get", _get)
    client.set_override("post", _post)


def _seed_for_completion(world) -> None:
    """Everything the poll's real completion path touches, bot excluded —
    creating the bot is the operation under test."""
    _seed_verifier(world)
    make_staff_user(world, user_id=_OWNER)
    _seed_baas_template_config(world)
    _install_baas(world)


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/auth-status",
    scenario="completes_creation_on_issued",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={
            "engine": "openclaw",
            "bot_name": "Auth Status Poll Bot",
            "bot_desc": "created by the poll",
        },
    ),
    seed=_seed_for_completion,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "status": "ISSUED",
                "bot": {
                    "bot_id": _BOT_ID,
                    "bot_name": "Auth Status Poll Bot",
                    "engine": "openclaw",
                },
            },
        },
    ),
)
def post_auth_status_issued_creates_the_bot():
    """The community passport answers ISSUED, so the poll finishes creation
    with the echoed attributes — the bot in the response is the proof."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/auth-status",
    scenario="rejects_an_unknown_engine",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"engine": "not-a-real-engine"},
    ),
    seed=_seed_verifier,
    expect=ExpectError(status=400, json_contains={"code": 400000, "data": None}),
)
def post_auth_status_unknown_engine():
    """The registry check runs before Passport is queried, so nothing is
    created for a request that cannot succeed."""
