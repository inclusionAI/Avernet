"""Create-with-manifest through the assembled app (W13, #1696).

The two routes on the wire: the real principal verification, the declared bars,
the DI graph and the repository round trip. What the handler unit tests in
``adapters/http/openapi_v1/`` structurally cannot reach — they pass every
dependency by keyword, so FastAPI's wiring and the injector never run.

Two rules are load-bearing enough to be cases of their own rather than
assertions inside a longer flow:

**An invalid manifest costs nothing.** It is a `422` naming every violation, and
**Passport is never applied to** — the stand-in below raises if it is. A caller
must never complete an authorization only to be told their document was wrong;
that wastes their time and burns a Passport application. The ordering that makes
it true (preflight beside quota and name, before Passport) is invisible in a
diff, so it is pinned here.

**The three failure modes answer differently.** An invalid manifest is a `422`
with no bot and no state; a bot that could not be provisioned is `CREATE_FAILED`;
a bot that is up with an incomplete manifest is `APPLY_FAILED`, carrying the bot
so a caller can see it exists. A caller must never have to read prose to tell
"you have no bot" from "you have a bot missing some configuration".
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.bot_config_manifest.create_job import (
    CREATE_JOB_TASK_TYPE,
    create_job_idempotency_key,
)
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_ON_CONTAINER_TRIGGER,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotConfigManifestApplyRepositoryProtocol,
    BotRepository,
)
from agentclaw.community.core.repository.protocols.platform import (
    TaskQueueRepositoryProtocol,
)
from agentclaw.community.core.task_queue.types import DEFAULT_APP
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)

_OWNER = "create-manifest-owner"
_BOT_ID = "create-manifest-bot"
_KEY = "create-with-manifest-framework-signing-key-at-least-32-bytes"
_APPLY_ID = "fedcba9876543210fedcba9876543210"

#: Declares only ``script`` — the one construct whose materialiser needs neither
#: a container nor a registry, so these cases exercise the surface rather than a
#: device fixture.
_DOCUMENT = 'schema_version: 1\nscript:\n  body: "echo provisioned"\n'

#: Wrong in two ways at once, on purpose: the all-or-nothing rule means a caller
#: fixing a document does it in one pass rather than a queue of resubmissions.
_INVALID_DOCUMENT = "schema_version: 99\nscript:\n  body: 17\n"


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


def _body(document: str = _DOCUMENT, engine: str = "openclaw", cluster: str = "ACRA"):
    return {
        "bot_name": "Manifest Creation Bot",
        "bot_desc": "created with its configuration",
        "engine": engine,
        "cluster_name": cluster,
        "bot_type": "personal",
        "config_manifest": document,
    }


def _refuse_passport(world) -> None:
    """Passport, wired to fail loudly if this path reaches it.

    A counter would prove the same thing and read as bookkeeping; raising makes
    the failure land on the case that broke the ordering, with a message saying
    what it broke.
    """

    def _never(_self, *_args, **_kwargs):
        raise AssertionError(
            "Passport was applied for on a submission that should have been "
            "refused before any application was made"
        )

    bind_overrides(
        world,
        PassportPlugin,
        {"apply_agent_passport": _never, "apply_first_agent_passport": _never},
    )


def _seed_refusing_passport(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    _refuse_passport(world)


def _seed_verifier(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


# ── submission ─────────────────────────────────────────────────────────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/with-manifest",
    scenario="invalid_manifest_is_refused_before_passport",
    input=CaseInput(
        json_body=_body(_INVALID_DOCUMENT), query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_refusing_passport,
    expect=ExpectError(status=422),
)
def invalid_manifest_is_422_and_costs_no_passport_application():
    """The whole reason validation happens before the Passport application.

    Every violation is named at once, no bot id is minted, nothing is stored,
    and no authorization is applied for — the stand-in raises if it is."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/with-manifest",
    scenario="teclaw_is_refused",
    input=CaseInput(
        json_body=_body(engine="teclaw", cluster="ANDC"),
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_refusing_passport,
    expect=ExpectError(status=422),
)
def teclaw_creation_is_refused_at_submission():
    """ARCA only, and the refusal names W8 (#1476).

    Not a missing materialiser: a teclaw bot is configured by the artifact
    composed when its container is provisioned, which is a different mechanism
    from this endpoint's pre/post-container delivery. A bot created here would
    get semantics that change under it when W8 lands."""


# ── the poll ───────────────────────────────────────────────────────────────


def _insert_bot(world, *, status: str) -> None:
    world.get(BotRepository).insert(
        {
            "bot_id": _BOT_ID,
            "bot_name": "Manifest Creation Bot",
            "owner_id": _OWNER,
            "owner_name": _OWNER,
            "entity_id": _OWNER,
            "entity_type": "staff",
            "creator_id": _OWNER,
            "status": status,
            "active_engine": "openclaw",
            "bot_type": "personal",
        }
    )


def _enqueue_the_job(world) -> None:
    """The creation's task row, under the key submission would have used.

    Enqueued through the repository rather than the endpoint because these cases
    are about what the poll *reads*: driving a real submission would also drive
    Passport, quota and provisioning, none of which is what is under test here.
    """
    world.get(TaskQueueRepositoryProtocol).enqueue(
        task_type=CREATE_JOB_TASK_TYPE,
        payload={"bot_id": _BOT_ID, "entity_id": _OWNER, "user_id": _OWNER},
        delay_seconds=0,
        deadline_seconds=600,
        env=get_current_env(),
        app=DEFAULT_APP,
        idempotency_key=create_job_idempotency_key(
            tenant=get_current_avernet_tenant(),
            entity_id=_OWNER,
            bot_id=_BOT_ID,
        ),
    )


def _record_post_container_apply(world, *, status: str) -> None:
    applies = world.get(BotConfigManifestApplyRepositoryProtocol)
    applies.start(
        env=get_current_env(),
        entity_id=_OWNER,
        bot_id=_BOT_ID,
        apply_id=_APPLY_ID,
        trigger=CREATE_ON_CONTAINER_TRIGGER,
        actor=_OWNER,
        report="{}",
    )
    applies.finish(
        env=get_current_env(),
        entity_id=_OWNER,
        bot_id=_BOT_ID,
        apply_id=_APPLY_ID,
        status=status,
        report=(
            '{"apply_id": "%s", "bot_id": "%s", "trigger": "%s", '
            '"result": "%s", "started_at": null, "finished_at": null, '
            '"sources": [], '
            '"categories": [{"category": "script", "aborted": true, '
            '"partially_written": false, "removed": []}], '
            '"entries": [{"category": "script", "name": "script", '
            '"action": "failed", "error": "the device refused the write", '
            '"note": null}]}'
        )
        % (_APPLY_ID, _BOT_ID, CREATE_ON_CONTAINER_TRIGGER, status),
    )


def _seed_a_bot_that_never_came_up(world) -> None:
    _seed_verifier(world)
    _insert_bot(world, status="FAILED")
    _enqueue_the_job(world)


def _seed_a_running_bot_with_a_failed_apply(world) -> None:
    _seed_verifier(world)
    _insert_bot(world, status="ACTIVE")
    _enqueue_the_job(world)
    _record_post_container_apply(world, status="PARTIAL")


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/with-manifest/status",
    scenario="provisioning_failed_is_create_failed",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_a_bot_that_never_came_up,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"state": "CREATE_FAILED", "bot_id": _BOT_ID, "bot": None},
        },
    ),
)
def a_bot_that_never_came_up_is_create_failed():
    """There is no usable bot, and the manifest is beside the point. The
    response carries no bot, which is the difference a caller acts on."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/with-manifest/status",
    scenario="partial_apply_is_apply_failed_with_the_bot",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_a_running_bot_with_a_failed_apply,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "state": "APPLY_FAILED",
                "bot_id": _BOT_ID,
                # The bot is there and running: the manifest failing never
                # touches the bot record, and the response says so rather than
                # leaving it to be inferred from a word.
                "bot": {"bot_id": _BOT_ID, "status": "ACTIVE"},
                "apply": {"apply_id": _APPLY_ID, "result": "PARTIAL"},
            },
        },
    ),
)
def a_partial_apply_is_apply_failed_and_carries_the_bot():
    """`PARTIAL` reports `APPLY_FAILED`, not `READY`: under the manifest's
    category overwrite a partial category can have removed entries it then
    failed to replace, so it is a state to act on. The bot is up either way,
    and re-applying converges it — nothing needs recreating."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/with-manifest/status",
    scenario="a_bot_that_was_not_created_here",
    input=CaseInput(
        path_params={"bot_id": "no-such-creation"},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_verifier,
    expect=ExpectError(status=404),
)
def a_bot_id_with_no_creation_is_a_404():
    """Including a bot made by the ordinary endpoint: it has a record and no
    post-container apply, which is the shape of `CREATING`. Answering `404` is
    the difference between "no idea what you are asking about" and inventing a
    state for a bot this endpoint never created."""
