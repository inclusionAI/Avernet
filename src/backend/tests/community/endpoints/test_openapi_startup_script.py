"""Endpoint-framework coverage for the public startup-script operations (#926).

Four routes, exercised through the assembled public app rather than a mocked
router: the real gateway-principal verification, the ownership guard, the
support resolver, the size cap, and the repository round-trip.

These are declared cases, not baseline entries. The claim that ``/openapi/v1``
routes cannot be covered until the #651 principal minter exists does not hold
for them — a case can mint its own gateway principal (as the skills group
already does), and a **user** principal is enough here because
``require_granted_bot`` is a no-op for a human caller; only an application
needs a live grant.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.bot_startup_script.services.startup_script_service import (
    MAX_SCRIPT_BYTES,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotRepository,
    BotStartupScriptRepositoryProtocol,
)
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_OWNER = "startup-script-owner"
_BOT_ID = "startup-script-bot"
_TECLAW_BOT_ID = "startup-script-teclaw-bot"
_KEY = "startup-script-framework-signing-key-at-least-32-bytes"
_SCRIPT = "#!/usr/bin/env bash\npip install ripgrep\n"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    """A gateway-signed principal naming a **user** and no application.

    No ``app`` entry on purpose: that is what makes the caller a human, and a
    human is waved through ``require_granted_bot`` because the operation's own
    owner-scoped resolve already refuses a bot that is not theirs.
    """
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
                    "subject": {"id": _OWNER, "username": "startup@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER}


def _insert_bot(world, *, bot_id: str, engine: str = "openclaw") -> None:
    """A bot with **no** binding — PENDING between create and first start.

    That is deliberately the supported case: it is exactly when an owner wants
    to attach a script, and it keeps these cases free of a device-binding
    fixture that would test the binding lookup rather than this surface.
    """
    world.get(BotRepository).insert(
        {
            "bot_id": bot_id,
            "bot_name": "Startup Script Bot",
            "owner_id": _OWNER,
            "owner_name": _OWNER,
            "entity_id": _OWNER,
            "entity_type": "staff",
            "creator_id": _OWNER,
            "status": "ACTIVE",
            "active_engine": engine,
            "bot_type": "personal",
        }
    )


def _seed_bot(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    _insert_bot(world, bot_id=_BOT_ID)


def _seed_teclaw_bot(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    _insert_bot(world, bot_id=_TECLAW_BOT_ID, engine="teclaw")


def _seed_bot_with_script(world) -> None:
    _seed_bot(world)
    world.get(BotStartupScriptRepositoryProtocol).upsert(
        env=get_current_env(),
        entity_id=_OWNER,
        bot_id=_BOT_ID,
        script=_SCRIPT,
        size_bytes=len(_SCRIPT.encode("utf-8")),
        modifier=_OWNER,
    )


def _seed_no_bot(world) -> None:
    """Only the verifier — the bot deliberately does not exist."""
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


# ── GET ────────────────────────────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/startup-script",
    scenario="reads_the_stored_script",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_bot_with_script,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "bot_id": _BOT_ID,
                "script": _SCRIPT,
                "size_bytes": len(_SCRIPT.encode("utf-8")),
                "updated_by": _OWNER,
                "supported": True,
            },
        },
    ),
)
def get_startup_script_ok():
    """The stored body, its size, and its author survive the round trip."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/startup-script",
    scenario="empty_for_a_bot_that_never_had_one",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"script": "", "size_bytes": 0, "supported": True},
        },
    ),
)
def get_startup_script_absent_is_not_an_error():
    """Absent reads as empty. A 404 here would make "has none" indistinguishable
    from "no such bot"."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/startup-script",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "no-such-bot"}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def get_startup_script_unknown_bot():
    """The ownership guard runs before any read."""


# ── PUT ────────────────────────────────────────────────────────────────────


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/{bot_id}/startup-script",
    scenario="stores_the_script",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"script": _SCRIPT},
    ),
    seed=_seed_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "script": _SCRIPT,
                "size_bytes": len(_SCRIPT.encode("utf-8")),
                # From the principal, never the body.
                "updated_by": _OWNER,
                "supported": True,
            },
        },
    ),
)
def put_startup_script_ok():
    """A write stores the body and stamps the acting user."""


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/{bot_id}/startup-script",
    scenario="rejects_an_oversize_body",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"script": "x" * (MAX_SCRIPT_BYTES + 1)},
    ),
    seed=_seed_bot,
    expect=ExpectError(
        status=413,
        json_contains={
            # The published contract promises the 413 names the limit, so the
            # exact bytes are asserted rather than just the status.
            "message": f"Startup script exceeds the {MAX_SCRIPT_BYTES}-byte limit",
            "data": None,
        },
    ),
)
def put_startup_script_too_large():
    """Refused at write time with the limit in the message, not at run time."""


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/{bot_id}/startup-script",
    scenario="refuses_a_bot_that_cannot_run_one",
    input=CaseInput(
        path_params={"bot_id": _TECLAW_BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"script": _SCRIPT},
    ),
    seed=_seed_teclaw_bot,
    expect=ExpectError(
        status=409,
        json_contains={
            "message": "Startup script is not supported for this bot",
            "data": None,
        },
    ),
)
def put_startup_script_unsupported_bot():
    """A teclaw bot is refused rather than storing a script that never runs."""


# ── DELETE ─────────────────────────────────────────────────────────────────


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/startup-script",
    scenario="clears_the_script",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_bot_with_script,
    expect=ExpectSuccess(status=200, json_contains={"data": {"deleted": True}}),
)
def delete_startup_script_ok():
    """Clearing a stored script succeeds."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/startup-script",
    scenario="is_idempotent",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_bot,
    expect=ExpectSuccess(status=200, json_contains={"data": {"deleted": True}}),
)
def delete_startup_script_absent_is_idempotent():
    """Clearing a script that was never set is not an error."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/startup-script",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "no-such-bot"}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def delete_startup_script_unknown_bot():
    """Idempotence covers an absent script, not an absent bot."""


# ── last-start ─────────────────────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/startup-script/last-start",
    scenario="empty_for_a_bot_that_never_started",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_bot,
    expect=ExpectSuccess(status=200, json_contains={"code": 200000, "data": []}),
)
def last_start_empty():
    """No publish id means nothing to report — an empty list, not an error."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/startup-script/last-start",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "no-such-bot"}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def last_start_unknown_bot():
    """The ownership guard runs before the publish lookup."""
