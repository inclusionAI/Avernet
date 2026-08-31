"""Endpoint-framework coverage for the public bot config-manifest routes (W1).

Four routes, exercised through the assembled public app rather than a mocked
router: real gateway-principal verification, the ownership guard, the #935
support narrowing, the schema validation, and the repository round-trip —
the same shape and rationale as ``test_openapi_startup_script.py``.

The surface ships dark behind ``BCM_API_ENABLED``; these cases set it because
they assert the *open* contract. The gate holds the rows from here on.

A **user** principal is enough: ``require_granted_own_bot`` is a no-op for a
human caller (the operation's own owner-scoped resolve already refuses a bot
that is not theirs).
"""

from __future__ import annotations

import os
import time

import jwt
import pytest

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
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

# The dark-launch flag is read from the environment per request; set it for
# the whole case module (the assembled app is shared across it).
os.environ["BCM_API_ENABLED"] = "1"


_OWNER = "manifest-owner"
_BOT_ID = "manifest-bot"
_TECLAW_BOT_ID = "manifest-teclaw-bot"
_KEY = "config-manifest-framework-signing-key-at-32-bytes"
_DIGEST = "sha256:" + "ab" * 32

_GIT_DOC = {
    "schema_version": 1,
    "sources": {
        "content": {
            "git": "https://git.example/team/content.git",
            "ref": "v1.2.0",
            "auth": "corp-git",
        }
    },
    "manifest": {
        "identity": [
            {
                "type": "SOUL.md",
                "from": "content",
                "subpath": "bots/${OCB_BOT_ID}/soul.md",
            }
        ],
        "skills": [
            {"name": "q", "from": "content", "subpath": "skills/q/"},
            {"name": "zipped", "source": "https://a.example/z.zip", "digest": _DIGEST},
        ],
    },
    "script": {"body": "#!/bin/bash\necho '$(id)' {token}\n"},
}


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
                    "subject": {"id": _OWNER, "username": "manifest@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER}


def _insert_bot(world, *, bot_id: str, engine: str = "openclaw") -> None:
    """A PENDING, binding-less personal bot — the supported, ordinary case."""
    world.get(BotRepository).insert(
        {
            "bot_id": bot_id,
            "bot_name": "Manifest Bot",
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


def _seed(world, *, teclaw: bool = False) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    _insert_bot(world, bot_id=_TECLAW_BOT_ID if teclaw else _BOT_ID, engine="teclaw" if teclaw else "openclaw")


def _seed_with_document(world) -> None:
    import json

    _seed(world)
    document = json.dumps(_GIT_DOC)
    world.get(BotConfigManifestRepositoryProtocol).upsert(
        env=get_current_env(),
        entity_id=_OWNER,
        bot_id=_BOT_ID,
        schema_version=1,
        document=document,
        size_bytes=len(document.encode("utf-8")),
        modifier=_OWNER,
    )


def _seed_nothing(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


_BASE = "/openapi/v1/bots/{bot_id}/config-manifest"


# ── GET ────────────────────────────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path=_BASE,
    scenario="reads_the_stored_document",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_with_document,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "bot_id": _BOT_ID,
                "schema_version": 1,
                "sources": {"content": {"ref": "v1.2.0"}},
                "manifest": {"skills": [{"name": "q"}]},
                # #1469: 校正过的 script 正文逐字节往返。
                "script": {"body": "#!/bin/bash\necho '$(id)' {token}\n"},
                "updated_by": _OWNER,
            },
        },
    ),
)
def get_manifest_ok():
    """The stored document, its audit stamps and an exact script body."""


@endpoint_test(
    method="GET",
    path=_BASE,
    scenario="empty_for_a_bot_that_never_declared_one",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "bot_id": _BOT_ID,
                "schema_version": 1,
                "sources": {},
                "script": None,
                "updated_by": None,
            },
        },
    ),
)
def get_manifest_absent_is_not_an_error():
    """Absence is "no declaration", not "no bot" — a 404 would conflate them."""


@endpoint_test(
    method="GET",
    path=_BASE,
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "no-such-bot"}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_nothing,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def get_manifest_unknown_bot():
    """The ownership guard runs before any read."""


# ── PUT ────────────────────────────────────────────────────────────────────


@endpoint_test(
    method="PUT",
    path=_BASE,
    scenario="stores_the_document",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body=_GIT_DOC,
    ),
    seed=_seed,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "bot_id": _BOT_ID,
                "schema_version": 1,
                # From the principal, never the body.
                "updated_by": _OWNER,
            },
        },
    ),
)
def put_manifest_ok():
    """All-or-nothing on the happy side: the whole document, stored."""


@endpoint_test(
    method="PUT",
    path=_BASE,
    scenario="rejects_an_invalid_document_with_per_entry_reasons",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={
            "sources": {"x": {}},
            "manifest": {"skills": [{"name": "ghost", "from": "nobody"}]},
        },
    ),
    seed=_seed,
    expect=ExpectError(
        status=422,
        json_contains={
            "code": 422000,
            "data": {
                "violations": [
                    {"entry": "skills[0]", "rule": "from-undeclared"}
                ]
            },
        },
    ),
)
def put_manifest_invalid():
    """One invalid part rejects the whole document, naming the entries."""


@endpoint_test(
    method="PUT",
    path=_BASE,
    scenario="refuses_a_script_for_a_teclaw_bot",
    input=CaseInput(
        path_params={"bot_id": _TECLAW_BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"script": {"body": "#!/bin/bash\n"}},
    ),
    seed=lambda world: _seed(world, teclaw=True),
    expect=ExpectError(
        status=422,
        json_contains={
            "code": 422000,
            "data": {"violations": [{"rule": "script-unsupported"}]},
        },
    ),
)
def put_manifest_teclaw_script_refused():
    """Fail closed at write time — storing it would be a silent no-op."""


# ── DELETE ─────────────────────────────────────────────────────────────────


@endpoint_test(
    method="DELETE",
    path=_BASE,
    scenario="removes_the_declaration",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_with_document,
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"deleted": True}},
    ),
)
def delete_manifest_ok():
    """Deletes the declaration — idempotent group contract, like the script
    routes: the response stays ``deleted: true``; emptiness is GET's story."""


# ── capabilities ───────────────────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path=f"{_BASE}/capabilities",
    scenario="advertises_what_put_accepts",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "categories": {
                    "mcp": True,
                    "resources": True,
                    "skills": True,
                    "identity": True,
                    "script": True,
                    # Phase-1 delivery notes, on the wire.
                    "engine_config": False,
                    "cli_tools": False,
                },
                "reasons": {},
            },
        },
    ),
)
def capabilities_match_the_put_gate():
    """The same resolver the write path consults — GET cannot advertise a
    category PUT would refuse."""


# ── Error shapes the coverage gate requires on every route ──────────────────
# (DELETE and capabilities only had happy cases; the gate holds every route
# to happy+error coverage.)


@endpoint_test(
    method="DELETE",
    path=_BASE,
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "no-such-bot"}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_nothing,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def delete_manifest_unknown_bot():
    """Idempotent does not mean unguarded — a bot that is not the caller's
    answers 404 before anything is removed."""


@endpoint_test(
    method="GET",
    path=f"{_BASE}/capabilities",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "no-such-bot"}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_nothing,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def capabilities_unknown_bot():
    """Capability answers are own-bot scoped like everything in the group."""
