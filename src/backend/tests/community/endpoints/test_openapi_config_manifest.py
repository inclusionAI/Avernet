"""Endpoint-framework coverage for the public config-manifest operations (#1469).

Four routes, exercised through the assembled public app rather than a mocked
router: the real gateway-principal verification, the ownership guard, the
capability resolver, schema validation, and the repository round trip.

Every seed turns the feature switch on. The switch decides whether this surface
is served at all, and off is its shipping default — a case that did not set it
would be asserting the 404 rather than the operation. The one case that *is*
about the switch lives in
``tests/community/adapters/http/openapi_v1/test_config_manifest_surface.py``,
where it can be asserted without leaving process state behind for whichever
case the runner picks next.
"""

from __future__ import annotations

import os
import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.bot_config_manifest.feature_flag import (
    CONFIG_MANIFEST_ENABLED_ENV,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotConfigManifestRepositoryProtocol,
    BotRepository,
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


_OWNER = "config-manifest-owner"
_BOT_ID = "config-manifest-bot"
_TECLAW_BOT_ID = "config-manifest-teclaw-bot"
_KEY = "config-manifest-framework-signing-key-at-least-32-bytes"

#: A document that exercises the byte-exact round trip: a block scalar whose
#: body carries a quote, a ``$(...)`` and a ``{token}``.
_DOCUMENT = (
    "schema_version: 1\n"
    "manifest:\n"
    "  identity:\n"
    "    - type: SOUL.md\n"
    "      content: |\n"
    "        # Who I am\n"
    "script:\n"
    "  body: |\n"
    "    #!/bin/bash\n"
    "    echo '$(id)' \"EOF\" {token}\n"
)


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    """A gateway-signed principal naming a **user** and no application.

    No ``app`` entry on purpose: that is what makes the caller a human, and a
    human is waved through ``require_granted_own_bot`` because the operation's
    own owner-scoped resolve already refuses a bot that is not theirs.
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
                    "subject": {"id": _OWNER, "username": "manifest@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER}


def _enable_surface() -> None:
    os.environ[CONFIG_MANIFEST_ENABLED_ENV] = "true"


def _insert_bot(world, *, bot_id: str, engine: str = "openclaw") -> None:
    """A bot with **no** binding — PENDING between create and first start.

    Deliberately the supported case: it is exactly when an owner wants to attach
    a manifest, and it keeps these cases free of a device-binding fixture that
    would test the binding lookup rather than this surface.
    """
    world.get(BotRepository).insert(
        {
            "bot_id": bot_id,
            "bot_name": "Config Manifest Bot",
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
    _enable_surface()
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    _insert_bot(world, bot_id=_BOT_ID)


def _seed_teclaw_bot(world) -> None:
    _enable_surface()
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    _insert_bot(world, bot_id=_TECLAW_BOT_ID, engine="teclaw")


def _seed_bot_with_manifest(world) -> None:
    _seed_bot(world)
    world.get(BotConfigManifestRepositoryProtocol).upsert(
        env=get_current_env(),
        entity_id=_OWNER,
        bot_id=_BOT_ID,
        document=_DOCUMENT,
        size_bytes=len(_DOCUMENT.encode("utf-8")),
        schema_version=1,
        modifier=_OWNER,
    )


def _seed_no_bot(world) -> None:
    """Only the verifier and the switch — the bot deliberately does not exist."""
    _enable_surface()
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


# ── GET ────────────────────────────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/config-manifest",
    scenario="reads_the_stored_document",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_bot_with_manifest,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "bot_id": _BOT_ID,
                # Byte for byte, quoting and all.
                "document": _DOCUMENT,
                "size_bytes": len(_DOCUMENT.encode("utf-8")),
                "schema_version": 1,
                "updated_by": _OWNER,
            },
        },
    ),
)
def get_config_manifest_ok():
    """The stored document, its size and its author survive the round trip."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/config-manifest",
    scenario="empty_for_a_bot_that_never_had_one",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"document": "", "size_bytes": 0, "schema_version": None},
        },
    ),
)
def get_config_manifest_absent_is_not_an_error():
    """Absent reads as an empty document. A 404 here would make "has none"
    indistinguishable from "no such bot"."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/config-manifest",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "no-such-bot"}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def get_config_manifest_unknown_bot():
    """The ownership guard runs before any read."""


# ── PUT ────────────────────────────────────────────────────────────────────


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/{bot_id}/config-manifest",
    scenario="stores_the_document",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"document": _DOCUMENT},
    ),
    seed=_seed_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "document": _DOCUMENT,
                "size_bytes": len(_DOCUMENT.encode("utf-8")),
                "schema_version": 1,
                # From the principal, never the body.
                "updated_by": _OWNER,
                "warnings": [],
            },
        },
    ),
)
def put_config_manifest_ok():
    """A write validates, stores the caller's bytes, and stamps the actor."""


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/{bot_id}/config-manifest",
    scenario="refuses_the_whole_document_and_names_each_reason",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={
            "document": (
                "schema_version: 1\n"
                "manifest:\n"
                "  cli_tools: []\n"
                "  identity:\n"
                "    - type: MEMORY.md\n"
                "      content: \"hi\"\n"
            )
        },
    ),
    seed=_seed_bot,
    expect=ExpectError(
        status=422,
        json_contains={
            "code": 422109,
            "message": "Config manifest is invalid",
            "data": {
                "violations": [
                    {
                        "location": "manifest.cli_tools",
                        "code": "unsupported_category",
                    },
                    {
                        "location": "manifest.identity[0].type",
                        "code": "reserved_identity_type",
                    },
                ]
            },
        },
    ),
)
def put_config_manifest_all_or_nothing():
    """Every reason at once, each naming the entry it applies to — and nothing
    stored."""


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/{bot_id}/config-manifest",
    scenario="refuses_a_script_on_a_bot_that_cannot_run_one",
    input=CaseInput(
        path_params={"bot_id": _TECLAW_BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"document": "schema_version: 1\nscript:\n  body: |\n    echo hi\n"},
    ),
    seed=_seed_teclaw_bot,
    expect=ExpectError(
        status=422,
        json_contains={
            "message": "Config manifest is invalid",
            "data": {"violations": [{"location": "script", "code": "unsupported_script"}]},
        },
    ),
)
def put_config_manifest_unsupported_for_this_bot():
    """A teclaw bot is refused rather than storing a script that never runs."""


# ── DELETE ─────────────────────────────────────────────────────────────────


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/config-manifest",
    scenario="clears_the_document",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_bot_with_manifest,
    expect=ExpectSuccess(status=200, json_contains={"data": {"deleted": True}}),
)
def delete_config_manifest_ok():
    """Clearing a stored manifest succeeds."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/config-manifest",
    scenario="is_idempotent",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_bot,
    expect=ExpectSuccess(status=200, json_contains={"data": {"deleted": True}}),
)
def delete_config_manifest_absent_is_idempotent():
    """Clearing a manifest that was never set is not an error."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/config-manifest",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "no-such-bot"}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def delete_config_manifest_unknown_bot():
    """Idempotence covers an absent manifest, not an absent bot."""


# ── capabilities ───────────────────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/config-manifest/capabilities",
    scenario="answers_per_construct",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "bot_id": _BOT_ID,
                "engine_type": "openclaw",
                "bot_type": "personal",
                "schema_versions": [1],
            },
        },
    ),
)
def get_config_manifest_capabilities_ok():
    """The read path's half of "one function, two entry points"."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/config-manifest/capabilities",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "no-such-bot"}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def get_config_manifest_capabilities_unknown_bot():
    """Capabilities are a property of a bot the caller can reach."""
