"""Endpoint-framework coverage for the config-manifest apply operations (#1472).

Three routes through the assembled public app rather than a mocked router: the
real gateway-principal verification, the ownership guard, the declared bars, and
the repository round trip.

The engine's own behaviour — convergence, all-or-nothing, overwrite — is proven
in ``tests/community/core/bot_config_manifest/apply/``. What these cover is the
*surface*: the shapes a caller sees, and the two "absent is not an error" rules.
"""

from __future__ import annotations

import threading
import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.repository.protocols.bot import (
    BotConfigManifestApplyLockRepositoryProtocol,
    BotConfigManifestApplyRepositoryProtocol,
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


_OWNER = "manifest-apply-owner"
_BOT_ID = "manifest-apply-bot"
_KEY = "manifest-apply-framework-signing-key-at-least-32-bytes"
_APPLY_ID = "0123456789abcdef0123456789abcdef"

#: A document declaring only ``script``: it is the one construct whose
#: materialiser needs neither a container nor a registry, so these cases test
#: the surface rather than a device fixture.
_DOCUMENT = 'schema_version: 1\nscript:\n  body: "echo hello"\n'


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal(user_id: str = _OWNER) -> str:
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
                    "subject": {"id": user_id, "username": f"{user_id}@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER}


def _insert_bot(world, *, bot_id: str = _BOT_ID) -> None:
    world.get(BotRepository).insert(
        {
            "bot_id": bot_id,
            "bot_name": "Manifest Apply Bot",
            "owner_id": _OWNER,
            "owner_name": _OWNER,
            "entity_id": _OWNER,
            "entity_type": "staff",
            "creator_id": _OWNER,
            "status": "ACTIVE",
            "active_engine": "openclaw",
            "bot_type": "personal",
        }
    )


def _seed_bot(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    _insert_bot(world)


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


def _seed_finished_apply(world) -> None:
    """A bot with one completed apply on record."""
    _seed_bot_with_manifest(world)
    applies = world.get(BotConfigManifestApplyRepositoryProtocol)
    applies.start(
        env=get_current_env(),
        entity_id=_OWNER,
        bot_id=_BOT_ID,
        apply_id=_APPLY_ID,
        trigger="explicit",
        actor=_OWNER,
        report="{}",
    )
    applies.finish(
        env=get_current_env(),
        entity_id=_OWNER,
        bot_id=_BOT_ID,
        apply_id=_APPLY_ID,
        status="SUCCEEDED",
        report=(
            '{"apply_id": "%s", "bot_id": "%s", "trigger": "explicit", '
            '"result": "SUCCEEDED", "started_at": null, "finished_at": null, '
            '"sources": [], '
            '"categories": [{"category": "script", "aborted": false, "removed": []}], '
            '"entries": [{"category": "script", "name": "script", '
            '"action": "created", "error": null, "note": "delivered now"}]}'
        )
        % (_APPLY_ID, _BOT_ID),
    )


def _seed_no_bot(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _await_the_background_apply(_response, world) -> None:
    """Join the worker before the case ends, then assert it finished.

    Two jobs, and the first is not optional. ``POST …/apply`` answers 202 and
    keeps working on a daemon thread; the per-test fixture disposes the engine
    the moment the case returns. Disposing a SQLite engine while another thread
    is mid-statement on one of its connections does not raise — it segfaults the
    interpreter, taking the whole pytest process with it. Joining the thread is
    what makes the case deterministic rather than a coin flip that usually lands
    the right way.

    Nothing here works around a production defect: a real deployment does not
    dispose its engine under a live apply, and an apply killed mid-flight is
    already answered by design — its ``RUNNING`` row has no live lock behind it,
    so the read derives ``FAILED``.

    Having waited, assert what the wait makes observable: a 202 is only worth
    anything if the work behind the handle actually reaches a terminal status.
    """
    for thread in threading.enumerate():
        # The name the service gives its workers. Coupling a test to it is the
        # price of being able to wait for one deterministically.
        if thread.name.startswith("manifest-apply-"):
            thread.join(timeout=30)
            assert not thread.is_alive(), "the apply thread never finished"

    record = world.get(BotConfigManifestApplyRepositoryProtocol).latest(
        env=get_current_env(), entity_id=_OWNER, bot_id=_BOT_ID
    )
    assert record is not None, "the accepted apply left no record"
    assert record.status == "SUCCEEDED", record.status


def _seed_bot_with_a_held_lock(world) -> None:
    """A bot whose apply lock is already held by someone else."""
    _seed_bot_with_manifest(world)
    world.get(BotConfigManifestApplyLockRepositoryProtocol).acquire(
        env=get_current_env(),
        entity_id=_OWNER,
        bot_id=_BOT_ID,
        holder_user_id="someone-else",
    )


# ── POST .../apply ─────────────────────────────────────────────────────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/config-manifest/apply",
    scenario="accepts_and_returns_an_id_to_poll",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_bot_with_manifest,
    expect=ExpectSuccess(status=202, json_contains={"code": 200000}),
    extra_assertions=(_await_the_background_apply,),
)
def apply_returns_202_with_a_handle():
    """Apply does not block: it answers 202 with an ``apply_id`` and continues
    in the background. A caller holding an HTTP connection across device I/O —
    and, from W5, network fetching — is a caller timing out."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/config-manifest/apply",
    scenario="no_stored_manifest_applies_nothing",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_bot,
    expect=ExpectSuccess(status=202, json_contains={"code": 200000}),
    extra_assertions=(_await_the_background_apply,),
)
def apply_with_no_manifest_is_not_an_error():
    """A bot with no manifest applies nothing and reports nothing applied — the
    same rule that makes an absent manifest read as an empty document."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/config-manifest/apply",
    scenario="dry_run_returns_the_plan",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={**_QUERY, "dry_run": "true"},
        headers=_HEADERS,
    ),
    seed=_seed_bot_with_manifest,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "bot_id": _BOT_ID,
                # The plan itself, not a handle — a dry run mints no id.
                "apply_id": "",
                "entries": [
                    {
                        "category": "script",
                        "name": "script",
                        "action": "created",
                        "error": None,
                        "note": None,
                    }
                ],
            },
        },
    ),
)
def apply_dry_run_answers_in_the_response():
    """A dry run is synchronous and carries the plan in the body: a preview whose
    answer arrives later by polling is not a preview. It mints no ``apply_id``
    and writes no record."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/config-manifest/apply",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "no-such-bot"},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404),
)
def apply_unknown_bot_is_a_404():
    """The ownership guard runs before anything else, as on every route here."""


# ── GET .../last-apply ─────────────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/config-manifest/last-apply",
    scenario="empty_for_a_bot_that_never_applied",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"apply_id": "", "bot_id": _BOT_ID, "entries": []},
        },
    ),
)
def last_apply_absent_is_not_an_error():
    """Never applied reads as an empty report, not a 404 — the same rule the
    manifest's own GET follows: a 404 would make "has never applied"
    indistinguishable from "no such bot"."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/config-manifest/last-apply",
    scenario="returns_the_most_recent_report",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_finished_apply,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "apply_id": _APPLY_ID,
                "bot_id": _BOT_ID,
                "result": "SUCCEEDED",
            },
        },
    ),
)
def last_apply_returns_the_report():
    """The authoritative answer to "did my manifest take effect?"."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/config-manifest/last-apply",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "no-such-bot"},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404),
)
def last_apply_unknown_bot_is_a_404():
    """"Never applied" reading as an empty report does not extend to "no such
    bot". The ownership guard runs first, so the two stay distinguishable — which
    is the whole reason the empty-report rule is safe."""


# ── GET .../applies/{apply_id} ─────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/config-manifest/applies/{apply_id}",
    scenario="polls_one_apply_by_id",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "apply_id": _APPLY_ID},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_finished_apply,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"apply_id": _APPLY_ID, "result": "SUCCEEDED"},
        },
    ),
)
def poll_by_id_returns_that_apply():
    """The handle the 202 hands back."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/config-manifest/applies/{apply_id}",
    scenario="an_id_from_another_bot_is_not_found",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "apply_id": "ffffffffffffffffffffffffffffffff"},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_finished_apply,
    expect=ExpectSuccess(
        status=200, json_contains={"code": 200000, "data": {"apply_id": ""}}
    ),
)
def poll_by_unknown_id_reads_empty():
    """An ``apply_id`` this bot does not own resolves to nothing.

    The lookup is scoped to the bot key as well as the id, so an id guessed or
    leaked from another bot cannot be read here: the id is a handle for polling,
    never what authorizes the read.
    """

@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/config-manifest/applies/{apply_id}",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "no-such-bot", "apply_id": _APPLY_ID},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404),
)
def poll_by_id_unknown_bot_is_a_404():
    """The guard runs before the id is looked at.

    Worth pinning separately from the unknown-id case above: that one proves an
    id this bot does not own reads empty, and this one proves a *bot* the caller
    does not own is refused outright, rather than leaking the difference between
    "no such bot" and "that bot has no such apply".
    """


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/config-manifest/apply",
    scenario="a_second_apply_while_one_runs_is_a_409",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_bot_with_a_held_lock,
    expect=ExpectError(status=409),
)
def apply_while_locked_is_a_409():
    """Applies are serialized per bot, and contention is retryable — not a 500.

    The route's own documentation promises this 409, but
    ``ManifestApplyInProgressError`` was not registered in the envelope's error
    map, so ``@envelope_errors`` re-raised it and the caller got a 500. Pinned
    through the assembled app rather than at the service, because the defect was
    entirely in the mapping: the service raised the right exception all along.
    """
