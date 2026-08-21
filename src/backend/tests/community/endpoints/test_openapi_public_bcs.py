"""Endpoint-framework coverage for the BCS publish-to-users operation.

``POST /openapi/v1/bots/{bot_id}/public-bcs`` opens a botpublish approval
ticket — it talks to an external approval system, a step no test host can run.
So both cases stand the service in through the sanctioned DI seam (``bind``
helpers), not a class-level mock that would outlive the case or lie about
coverage: the happy path binds ``public_bcs_bot`` to a completed result, the
error path binds it to the production ``BotNotFoundError`` the route is meant to
surface as 404. The route, middleware, principal/``user_id`` resolution, and
envelope serialization all stay real — that is what the coverage gate reads.

``test_bot_public_router.py`` covers the sibling ``/api/bots/{bot_id}/public``
publish endpoint; the public-BCS surface is its own route under
``collaboration_bots`` and the gate keys them apart by ``(method, path)``.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.collaboration_bots.schemas import (
    BcsPublishResult,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.core.bot_public.services.bot_public_service import (
    BotNotFoundError,
)
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)
from tests.community.framework.di_seams import bind_failing_method, bind_method

_PATH = "/openapi/v1/bots/{bot_id}/public-bcs"
_CALLER = "bcs-publisher"
_KEY = "public-bcs-framework-signing-key-32b"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    """A user-only identity — the operator who publishes under their own ``user_id``."""
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
                        "id": _CALLER,
                        "username": "publisher@example.test",
                        "display_name": "Publisher",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


def _boot_verifier(_world) -> None:
    """Install the shared key both cases are judged against.

    The error case needs it as much as the happy one: without a booted verifier
    a 401 from the auth seam would confound the 404 the route is meant to return
    when the bot is not found, and the case would assert the right code for the
    wrong reason.
    """
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _happy(_self, *_args, **_kwargs) -> BcsPublishResult:
    """The stand-in for the approval-ticket submit (no approval host on a test box)."""
    return BcsPublishResult(
        success=True,
        puid="puid-bcs-ticket-framework",
        state="PROCESSING",
    )


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="starts_the_publish_approval_ticket",
    input=CaseInput(
        path_params={"bot_id": "bcs-target-bot"},
        headers={PRINCIPAL_HEADER: _principal()},
        query_params={"user_id": _CALLER},
        json_body={"public_scope": "user"},
    ),
    seed=lambda world: (
        _boot_verifier(world),
        bind_method(world, BotPublicServiceProtocol, "public_bcs_bot", _happy),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "message": "OK",
            "data": {"success": True},
        },
    ),
)
def public_bcs_opens_approval():
    """Body intentionally empty — the framework owns invocation."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="unknown_bot_surfaces_not_found",
    input=CaseInput(
        path_params={"bot_id": "no-such-bot"},
        headers={PRINCIPAL_HEADER: _principal()},
        query_params={"user_id": _CALLER},
        json_body={"public_scope": "user"},
    ),
    seed=lambda world: (
        _boot_verifier(world),
        bind_failing_method(
            world,
            BotPublicServiceProtocol,
            "public_bcs_bot",
            BotNotFoundError("bot not found: no-such-bot"),
        ),
    ),
    expect=ExpectError(
        status=404,
        json_contains={"code": 404000, "message": "Not found", "data": None},
    ),
)
def public_bcs_unknown_bot_is_not_found():
    """The missing-bot branch the ``@envelope_errors`` mapping pins (→ 404).

    ``bind_failing_method`` raises the production ``BotNotFoundError`` on the
    per-test injector so the case documents the mapping it pins rather than name
    a different error the route never raises.
    """
