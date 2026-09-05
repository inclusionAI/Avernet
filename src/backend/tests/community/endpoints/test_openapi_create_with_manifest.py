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


#: A document teclaw can carry: identity, not script (``unsupported_script``).
_TECLAW_DOCUMENT = (
    "schema_version: 1\nmanifest:\n  identity:\n"
    "    - type: RULES.md\n      content: '# be kind'\n"
)


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/with-manifest",
    scenario="teclaw_is_accepted",
    input=CaseInput(
        json_body=_body(_TECLAW_DOCUMENT, engine="teclaw", cluster="ANDC"),
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=lambda world: (_seed_verifier(world), _stand_in_for_provisioning(world)),
    expect=ExpectSuccess(status=202, json_contains={"code": 202000}),
)
def teclaw_creation_is_accepted_at_submission():
    """W8 (#1476): every engine family creates here.

    The refusal that named W8 is gone: a teclaw bot is created from its
    manifest like any other, and which order the creation runs in is the
    delivery strategy's — not the endpoint's — to decide."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/with-manifest",
    scenario="script_on_teclaw_is_still_refused",
    input=CaseInput(
        json_body=_body(engine="teclaw", cluster="ANDC"),
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_refusing_passport,
    expect=ExpectError(status=422),
)
def script_on_teclaw_is_the_validators_refusal():
    """What a family cannot deliver is refused per construct, by the validator,
    before Passport — the document above declares a startup script."""


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


# ── the whole flow ─────────────────────────────────────────────────────────
#
# Written as ordinary tests against the same per-test app and world the
# declarative cases use, because a creation is not one request: it is a
# submission, a job that runs between polls, and a poll whose answer changes
# each time. The declarative runner drives exactly one request, so a flow
# expressed there could only ever assert the first answer.
#
# The job is driven by hand rather than by a worker. The endpoint app never runs
# lifecycle ``bootstrap()``, so no task-queue handler is registered in it — the
# same reason ``test_openapi_config_manifest_apply.py`` drains apply tasks
# instead of starting one. Driving the real handler with the real payload is
# exactly what a worker that *had* registered it would do, and it is
# deterministic: no poll interval, no lease, nothing racing the fixture's engine
# disposal.

import pytest
from fastapi.testclient import TestClient

from agentclaw.community.api.bot_config_manifest_apply_service import (
    BotConfigManifestApplyServiceProtocol,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.api.bot_startup_script_service import (
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.apply.apply_task import (
    APPLY_TASK_TYPE,
)
from agentclaw.community.core.bot_config_manifest.create_job import (
    BotCreateWithManifestHandler,
)
from agentclaw.community.core.bot_config_manifest.creation import (
    BotCreationManifestSeam,
)
from agentclaw.community.core.bot_management.manifest_seam import (
    ManifestCreationSeam,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotConfigManifestRepositoryProtocol,
)
from agentclaw.community.core.task_queue.types import Complete, Fail, Reschedule

_SCRIPT = "echo provisioned"
_FLOW_DOCUMENT = f'schema_version: 1\nscript:\n  body: "{_SCRIPT}"\n'
#: Declares nothing at all. Valid, and the closest this endpoint has to "no
#: manifest" — the field is required, so an empty document is how a caller says
#: they want the bot and no configuration.
_EMPTY_DOCUMENT = "schema_version: 1\n"
#: A category nothing in this build can apply. It was ``resources`` until W6
#: landed that materialiser; ``engine_config`` is the honest example now, and
#: the reason it can be one is that the vocabulary outruns the code by exactly
#: two entries (``engine_config`` and ``cli_tools``). Such a category is
#: refused *here* rather than stored inert, because accepting it would mean
#: authorizing, creating the bot, and only then failing to configure it.
_UNBACKED_DOCUMENT = (
    "schema_version: 1\nmanifest:\n  engine_config:\n"
    '    permissions:\n      allow: ["Bash"]\n'
)


def _stand_in_for_provisioning(world, *, status: str = "ACTIVE") -> list[dict]:
    """Persist the row ``create_bot`` would, without allocating a device.

    The one collaborator a request cannot drive: allocation posts to BaaS and
    the local ``HttpClient`` seam refuses an unstubbed call. Everything above it
    stays real — the engine registry check, the engine/cluster pairing, the
    quota preflight, the Passport application, the manifest preflight, the
    storage write and the owner-relationship write all run.
    """
    created: list[dict] = []
    bot_repo = world.get(BotRepository)

    def create_bot(_self, **kwargs):
        # Idempotent on a supplied bot_id, exactly as the real one is — it
        # returns the existing bot rather than inserting a second. The job
        # relies on that: a re-claimed task, and the owner-relationship repair,
        # both call completion again on a bot that already exists.
        existing = bot_repo.get_by_id_and_entity(
            kwargs["bot_id"], kwargs.get("entity_id") or kwargs["user_id"]
        )
        if existing is not None:
            return existing
        record = bot_repo.insert(
            {
                "bot_id": kwargs["bot_id"],
                "bot_name": kwargs.get("bot_name") or kwargs["bot_id"],
                "bot_desc": kwargs.get("bot_desc"),
                "owner_id": kwargs["user_id"],
                "owner_name": kwargs["user_id"],
                "entity_id": kwargs.get("entity_id") or kwargs["user_id"],
                "entity_type": kwargs.get("entity_type") or "staff",
                "creator_id": kwargs["user_id"],
                "bot_type": kwargs.get("bot_type") or "personal",
                "status": status,
                "active_engine": kwargs.get("engine_type") or "claude_code",
            }
        )
        created.append(record)
        return record

    # Bound under both keys: the routers reach this service as its Protocol and
    # the creation job reaches it as its concrete class, so substituting only
    # one leaves the other on the real device-allocating path.
    bind_overrides(
        world,
        BotServiceProtocol,
        {"create_bot": create_bot},
        also_bind=(BotService,),
    )
    return created


class _Worker:
    """A worker, reduced to one deployment and driven a turn at a time.

    It claims, dispatches and **applies the outcome to the row**, because that
    last part is what the poll reads: a job that returned ``Fail`` but whose row
    was never transitioned still looks live, and the poll would go on reporting
    `AWAITING_AUTHORIZATION` for a creation that had already been declined.
    Running the handlers without the transitions would have made these cases
    assert against a state the real system never produces.

    Two liberties, both deliberate. Rescheduled tasks come back immediately
    rather than after the job's five-second poll delay — the delay is latency,
    not sequencing, and waiting it out would only make the suite slow. And a
    task type this file does not drive is put back far enough not to return,
    rather than completed, so nothing else in the app is quietly retired.
    """

    _WORKER = "test-worker"

    def __init__(self, world) -> None:
        self._world = world
        self._repo = world.get(TaskQueueRepositoryProtocol)

    def _dispatch(self, task):
        if task.task_type == CREATE_JOB_TASK_TYPE:
            return self._world.get(BotCreateWithManifestHandler).handle(task.payload)
        if task.task_type == APPLY_TASK_TYPE:
            self._world.get(BotConfigManifestApplyServiceProtocol).run_apply_task(
                task.payload
            )
            return Complete()
        return None

    def _settle(self, task, outcome) -> None:
        if outcome is None or isinstance(outcome, Reschedule):
            self._repo.reschedule(
                task_id=task.id,
                worker_id=self._WORKER,
                delay_seconds=0 if outcome is not None else 3600,
            )
        elif isinstance(outcome, Fail):
            self._repo.fail(
                task_id=task.id, worker_id=self._WORKER, error=outcome.error
            )
        else:
            self._repo.complete(task_id=task.id, worker_id=self._WORKER)

    def turn(self) -> list:
        claimed = self._repo.claim_batch(
            worker_id=self._WORKER,
            env=get_current_env(),
            app=DEFAULT_APP,
            limit=10,
            lease_seconds=300,
        )
        settled = []
        for task in claimed:
            outcome = self._dispatch(task)
            self._settle(task, outcome)
            if task.task_type == CREATE_JOB_TASK_TYPE:
                settled.append(outcome)
        return settled

    def run_to_the_end(self, *, turns: int = 8):
        for _ in range(turns):
            for outcome in self.turn():
                if isinstance(outcome, (Complete, Fail)):
                    # Drain whatever this last turn enqueued — the
                    # post-container apply is started by the job's final turn.
                    self.turn()
                    return outcome
        raise AssertionError("the creation never reached a terminal outcome")


@pytest.fixture
def client(app_with_testing_modules):
    return TestClient(app_with_testing_modules)


def _submit(client, document: str = _FLOW_DOCUMENT, engine: str = "claude_code"):
    return client.post(
        "/openapi/v1/bots/with-manifest",
        params=_QUERY,
        headers=_HEADERS,
        json=_body(document, engine=engine),
    )


def _poll(client, bot_id: str):
    return client.get(
        f"/openapi/v1/bots/{bot_id}/with-manifest/status",
        params=_QUERY,
        headers=_HEADERS,
    )


def test_a_creation_runs_from_submission_to_ready(client, world):
    """The whole path, and the report at the end carries **both** phases.

    That last part is the one a reader should not skip. The script is delivered
    before the container exists and everything else after it, so a report built
    only from the second phase would name whatever landed post-container and
    silently omit the script — which is exactly what a caller would look for
    first.
    """
    _seed_verifier(world)
    _stand_in_for_provisioning(world)

    submitted = _submit(client)
    assert submitted.status_code == 202, submitted.text
    bot_id = submitted.json()["data"]["bot_id"]
    assert bot_id
    # No state on submission: the vocabulary belongs to the poll, so a terminal
    # value cannot be returned by a request where nothing has happened yet.
    assert "state" not in submitted.json()["data"]

    awaiting = _poll(client, bot_id).json()["data"]
    assert awaiting["state"] == "AWAITING_AUTHORIZATION"
    assert awaiting["bot"] is None

    outcome = _Worker(world).run_to_the_end()
    assert isinstance(outcome, Complete), outcome

    ready = _poll(client, bot_id).json()["data"]
    assert ready["state"] == "READY", ready
    assert ready["bot"]["bot_id"] == bot_id
    assert ready["apply"]["result"] == "SUCCEEDED"
    assert any(
        entry["category"] == "script" for entry in ready["apply"]["entries"]
    ), (
        "the pre-container phase is missing from the report: a caller would "
        "think the script never ran"
    )
    # And it really was delivered before the bot existed, which is the whole
    # point of the two phases.
    assert (
        world.get(BotStartupScriptServiceProtocol).get_body(
            entity_id=_OWNER, bot_id=bot_id
        )
        == _SCRIPT
    )


def test_a_creation_declaring_nothing_is_still_ready(client, world):
    """An empty manifest is not an error, and not a special case either.

    Both phases run and apply nothing, which is what makes the endpoint usable
    as a plain create: a caller should not have to choose a different address
    because they have no configuration yet.
    """
    _seed_verifier(world)
    _stand_in_for_provisioning(world)

    bot_id = _submit(client, _EMPTY_DOCUMENT).json()["data"]["bot_id"]
    _Worker(world).run_to_the_end()

    ready = _poll(client, bot_id).json()["data"]
    assert ready["state"] == "READY", ready


def test_a_construct_with_no_materialiser_is_refused_at_submission(client, world):
    _seed_verifier(world)
    _refuse_passport(world)

    refused = _submit(client, _UNBACKED_DOCUMENT)

    assert refused.status_code == 422, refused.text
    assert "engine_config" in refused.text, (
        "the refusal must name the category, or a caller cannot tell which "
        "part of their document to remove"
    )


def test_a_declined_authorization_is_terminal_and_leaves_nothing(client, world):
    """No bot, and no rows either.

    The manifest and any startup-script row are keyed by a ``bot_id`` that will
    never become a bot, so nothing else can ever reach them: ordinary deletion
    needs a bot record. Cleaning up here is what replaced the feature switch
    this item was originally going to ship behind.
    """
    _seed_verifier(world)
    _stand_in_for_provisioning(world)

    bot_id = _submit(client).json()["data"]["bot_id"]

    def _declined(_self, **_kwargs):
        return {"status": "REJECTED"}

    bind_overrides(world, PassportPlugin, {"query_auth_status": _declined})

    outcome = _Worker(world).run_to_the_end()
    assert isinstance(outcome, Fail), outcome

    declined = _poll(client, bot_id).json()["data"]
    assert declined["state"] == "AUTHORIZATION_REJECTED", declined
    assert declined["bot"] is None

    assert (
        world.get(BotConfigManifestRepositoryProtocol).get(
            env=get_current_env(), entity_id=_OWNER, bot_id=bot_id
        )
        is None
    ), "the manifest of a bot that will never exist was left behind"
    assert not world.get(BotStartupScriptServiceProtocol).get_body(
        entity_id=_OWNER, bot_id=bot_id
    ), "a startup-script row was left behind"
    assert world.get(BotRepository).get_by_id_and_entity(bot_id, _OWNER) is None


def test_an_abandoned_creation_expires_rather_than_reading_as_declined(
    client, world
):
    """A user who never clicked did not decide anything.

    The window is the handler's own rather than only the queue's, because a task
    retired in the claim scan never runs again — so nothing would delete the
    rows submission wrote.

    The window is shortened through **the value a deployment configures**, not by
    patching a clock. It reaches the system in exactly one place — the seam reads
    ``bot_create_with_manifest.authorization_window_seconds`` once and hands it to
    the enqueue, which freezes it into the payload — so a one-second window set
    there is the same path a deployment's setting takes. It costs one real
    second, because the window is wall-clock and nothing about it is fake.
    """
    _seed_verifier(world)
    _stand_in_for_provisioning(world)

    def _never_answers(_self, **_kwargs):
        return {"status": "PENDING"}

    bind_overrides(world, PassportPlugin, {"query_auth_status": _never_answers})

    def _one_second_window(self, **kwargs):
        # The configured value, applied where configuration enters: the seam
        # hands its window to the enqueue, which freezes it into the payload.
        self._authorization_window_seconds = 1
        return BotCreationManifestSeam.start_job(self, **kwargs)

    # Keyed by the Protocol, because that is what the container binds — the
    # concrete class is not a binding at all, and asking for it would build a
    # second, unwired seam. ``bind_overrides`` subclasses whatever the binding
    # actually serves, so the stand-in still carries the real implementation.
    bind_overrides(world, ManifestCreationSeam, {"start_job": _one_second_window})

    bot_id = _submit(client).json()["data"]["bot_id"]
    time.sleep(1.2)
    outcome = _Worker(world).run_to_the_end()

    assert isinstance(outcome, Fail), outcome

    expired = _poll(client, bot_id).json()["data"]
    assert expired["state"] == "AUTHORIZATION_EXPIRED", expired
    assert expired["state"] != "AUTHORIZATION_REJECTED"
    assert expired["bot"] is None
    assert (
        world.get(BotConfigManifestRepositoryProtocol).get(
            env=get_current_env(), entity_id=_OWNER, bot_id=bot_id
        )
        is None
    )


def test_the_old_two_call_path_still_works_unchanged(client, world):
    """The path this endpoint replaces must keep working exactly as it did.

    Create through `POST /openapi/v1/bots`, `PUT` a manifest, apply it. This is
    the regression that matters most for W13: applying moved off a daemon thread
    onto the task queue, and *nothing about that is supposed to be visible* from
    outside — same `202`, same `apply_id`, same terminal report, and still no
    restart.

    The restart clause is the one worth spelling out. A manifest converges a
    running bot in place; a configuration change that bounced the container
    would interrupt whatever the bot was doing, and a caller who applied a
    one-line script change would have no way to know that was going to happen.
    """
    _seed_verifier(world)
    _stand_in_for_provisioning(world)

    restarts: list[tuple] = []

    def restart_bot(_self, *args, **kwargs):
        restarts.append((args, kwargs))
        return {}

    bind_overrides(
        world,
        BotServiceProtocol,
        {"restart_bot": restart_bot},
        also_bind=(BotService,),
    )

    created = client.post(
        "/openapi/v1/bots",
        params=_QUERY,
        headers=_HEADERS,
        json={
            "bot_name": "Ordinary Bot",
            "bot_desc": "created the old way",
            "engine": "claude_code",
            "cluster_name": "ACRA",
            "bot_type": "personal",
        },
    )
    assert created.status_code == 201, created.text
    bot_id = created.json()["data"]["bot_id"]

    stored = client.put(
        f"/openapi/v1/bots/{bot_id}/config-manifest",
        params=_QUERY,
        headers=_HEADERS,
        json={"document": _FLOW_DOCUMENT},
    )
    assert stored.status_code in (200, 201), stored.text
    # W8 (§2.6): the PUT itself started an apply of what it stored. It runs on
    # the same queue and reports under its own trigger; the explicit apply
    # below still works exactly as it did once that one has run.
    assert stored.json()["data"]["apply"]["result"] == "RUNNING"
    _Worker(world).turn()
    put_report = client.get(
        f"/openapi/v1/bots/{bot_id}/config-manifest/last-apply",
        params=_QUERY,
        headers=_HEADERS,
    ).json()["data"]
    assert put_report["trigger"] == "put" and put_report["result"] == "SUCCEEDED", put_report

    accepted = client.post(
        f"/openapi/v1/bots/{bot_id}/config-manifest/apply",
        params=_QUERY,
        headers=_HEADERS,
    )
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["data"]["apply_id"], "the 202 carried no handle"
    assert accepted.json()["data"]["result"] == "RUNNING"

    _Worker(world).turn()

    report = client.get(
        f"/openapi/v1/bots/{bot_id}/config-manifest/last-apply",
        params=_QUERY,
        headers=_HEADERS,
    ).json()["data"]
    assert report["result"] == "SUCCEEDED", report
    assert report["trigger"] == "explicit", (
        "an apply on a running bot must not be labelled as part of a creation"
    )

    assert not restarts, "applying a manifest restarted the bot"
    assert (
        world.get(BotRepository).get_by_id_and_entity(bot_id, _OWNER)["status"]
        == "ACTIVE"
    )
    # And the poll for *creations* does not claim this one as its own.
    assert _poll(client, bot_id).status_code == 404


def _seed_ready_to_create(world) -> None:
    _seed_verifier(world)
    _stand_in_for_provisioning(world)


def _the_submission_allocated_a_bot_and_reported_no_state(response, _world) -> None:
    data = response.json()["data"]
    assert data["bot_id"], "the 202 carried no bot_id to poll with"
    # The property the models make structural, asserted on the wire too: a
    # caller cannot read a state off a submission, because there is none to read.
    assert "state" not in data
    assert set(data) == {"bot_id", "iframe_url", "redirect_url"}


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/with-manifest",
    scenario="accepts_and_returns_a_bot_id_to_poll",
    input=CaseInput(json_body=_body(), query_params=_QUERY, headers=_HEADERS),
    seed=_seed_ready_to_create,
    expect=ExpectSuccess(status=202, json_contains={"code": 202000}),
    extra_assertions=(_the_submission_allocated_a_bot_and_reported_no_state,),
)
def submitting_a_creation_answers_202_with_an_id():
    """Submission never waits and never creates: it validates the manifest,
    stores it against the allocated id, applies for the authorization and stops.
    The bot is created later, by the job, once the user has authorized."""


# ── teclaw, platform-managed: the record, one phase, then the container (W8) ──


def _stand_in_for_teclaw_platform_managed(world) -> list[str]:
    """Deferred creation and provisioning stood in, the strategy switched on.

    ``create_bot(provision=False)`` writes the record with no binding, and
    ``provision_bot`` binds it and reports the container up. The apply service
    itself stays real, with its strategy factory rebound so teclaw runs the
    platform-managed sequence over the store-backed ports the DI graph already
    binds — the mock object store and the in-memory index.
    """
    from agentclaw.community.core.bot_config_manifest.apply.delivery import (
        DeliveryStrategyFactory,
        TeclawPlatformBindings,
    )
    from agentclaw.community.core.bot_config_manifest.services.config_manifest_apply_service import (
        BotConfigManifestApplyService,
    )

    order: list[str] = []
    bot_repo = world.get(BotRepository)

    def create_bot(_self, **kwargs):
        existing = bot_repo.get_by_id_and_entity(
            kwargs["bot_id"], kwargs.get("entity_id") or kwargs["user_id"]
        )
        if existing is not None:
            return existing
        order.append("record" if kwargs.get("provision") is False else "create")
        return bot_repo.insert(
            {
                "bot_id": kwargs["bot_id"],
                "bot_name": kwargs.get("bot_name") or kwargs["bot_id"],
                "owner_id": kwargs["user_id"],
                "owner_name": kwargs["user_id"],
                "entity_id": kwargs.get("entity_id") or kwargs["user_id"],
                "entity_type": kwargs.get("entity_type") or "staff",
                "creator_id": kwargs["user_id"],
                "bot_type": kwargs.get("bot_type") or "personal",
                "status": "PENDING",
                "binding_id": None,
                "active_engine": kwargs.get("engine_type") or "teclaw",
            }
        )

    def provision_bot(_self, bot_id, user_id, nick_name, **_kw):
        order.append("provision")
        bot_repo.update_by_owner(bot_id, user_id, {"binding_id": 9, "device_id": "BOT-9", "status": "ACTIVE"})
        return bot_repo.get_by_id_and_owner(bot_id, user_id)

    bind_overrides(
        world,
        BotServiceProtocol,
        {"create_bot": create_bot, "provision_bot": provision_bot},
        also_bind=(BotService,),
    )

    applies = bind_overrides(
        world, BotConfigManifestApplyServiceProtocol, {}, also_bind=(BotConfigManifestApplyService,)
    )
    # The managed-files store keeps no index: the object store's listing is
    # the record, so the mock plugin has to remember what was put into it.
    from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin

    objects: dict[str, bytes] = {}
    oss = world.get(ObjectStoragePlugin)
    oss.put_object.side_effect = lambda key, content: (
        objects.__setitem__(key, content if isinstance(content, bytes) else content.encode()) or True
    )
    oss.get_object.side_effect = objects.get
    oss.delete_object.side_effect = lambda key: objects.pop(key, None) is not None or True
    oss.list_objects.side_effect = lambda prefix, max_keys=1000: sorted(
        k for k in objects if k.startswith(prefix)
    )[:max_keys]
    bindings = world.get(TeclawPlatformBindings)
    applies._strategies = DeliveryStrategyFactory(
        is_teclaw=lambda engine: engine == "teclaw",
        teclaw_platform_managed=True,
        arca_ports=applies._arca_ports,
        teclaw_platform_ports=bindings.platform_ports,
        redeliver=bindings.redeliver,
    )
    return order


def test_a_teclaw_creation_walks_record_phase_container_ready(client, world):
    """`AWAITING_AUTHORIZATION → CREATING → APPLYING → CREATING → READY`, with
    the report from the single phase, and the phase's files in the platform's
    store before the container was provisioned."""
    from agentclaw.community.core.bot_config_manifest.managed_files import (
        CATEGORY_IDENTITY,
        ManagedFileScope,
        ManagedFilesStore,
    )

    _seed_verifier(world)
    order = _stand_in_for_teclaw_platform_managed(world)
    worker = _Worker(world)

    submitted = client.post(
        "/openapi/v1/bots/with-manifest",
        params=_QUERY,
        headers=_HEADERS,
        json=_body(_TECLAW_DOCUMENT, engine="teclaw", cluster="ANDC"),
    )
    assert submitted.status_code == 202, submitted.text
    bot_id = submitted.json()["data"]["bot_id"]
    assert _poll(client, bot_id).json()["data"]["state"] == "AWAITING_AUTHORIZATION"

    states: list[str] = []
    seen_at_provision: dict[str, list[str]] = {}
    store = world.get(ManagedFilesStore)
    scope = ManagedFileScope(entity_type="staff", entity_id=_OWNER, bot_id=bot_id)

    outcome = None
    for _ in range(8):
        turned = worker.turn()
        if "provision" in order and "identity" not in seen_at_provision:
            seen_at_provision["identity"] = [r.rel_path for r in store.list(scope, category=CATEGORY_IDENTITY)]
        state = _poll(client, bot_id).json()["data"]["state"]
        if not states or states[-1] != state:
            states.append(state)
        outcome = next((o for o in turned if isinstance(o, (Complete, Fail))), None)
        if outcome is not None:
            break
    assert isinstance(outcome, Complete), (outcome, states, order)

    assert order == ["record", "provision"], order
    assert states == ["CREATING", "APPLYING", "CREATING", "READY"], states
    assert seen_at_provision["identity"] == ["identity/RULES.md"], (
        "the platform's copy must exist when provisioning composes the first artifact"
    )
    ready = _poll(client, bot_id).json()["data"]
    assert ready["bot"]["bot_id"] == bot_id and ready["bot"]["status"] == "ACTIVE"
    assert ready["apply"]["result"] == "SUCCEEDED"
    assert [e["category"] for e in ready["apply"]["entries"]] == ["identity"]
