"""Endpoint-framework coverage for the public config-manifest operations (#1469).

Four routes, exercised through the assembled public app rather than a mocked
router: the real gateway-principal verification, the ownership guard, the
capability resolver, schema validation, and the repository round trip.

"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.repository.protocols.bot import (
    BotConfigManifestRepositoryProtocol,
    BotRepository,
)
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_collaborator
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


def _principal(user_id: str = _OWNER) -> str:
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
                    "subject": {"id": user_id, "username": f"{user_id}@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER}

#: A collaborator holding MEMBER on the owner's bot. These operations are
#: collaborator-scoped — MEMBER to read, ADMIN to write — so this caller is the
#: one that proves the bars are real rather than decorative.
#: Collaboration is a service-bot notion in this codebase, so the shared bot is
#: a service bot; the owner-scoped cases above keep using the personal one.
_SHARED_BOT_ID = "config-manifest-shared-bot"
_MEMBER = "config-manifest-member"
_MEMBER_HEADERS = {PRINCIPAL_HEADER: _principal(_MEMBER)}
#: ``user_id`` is the caller; ``owner_id`` names whose bot is addressed.
_MEMBER_QUERY = {"user_id": _MEMBER, "owner_id": _OWNER}


def _insert_bot(
    world, *, bot_id: str, engine: str = "openclaw", bot_type: str = "personal",
    status: str = "ACTIVE",
) -> None:
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
            "status": status,
            "active_engine": engine,
            "bot_type": bot_type,
        }
    )


def _seed_bot(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    _insert_bot(world, bot_id=_BOT_ID)


def _seed_teclaw_bot(world) -> None:
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


def _seed_member(world) -> None:
    """A shared service bot carrying a manifest, plus a MEMBER collaborator."""
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    _insert_bot(world, bot_id=_SHARED_BOT_ID, bot_type="service")
    world.get(BotConfigManifestRepositoryProtocol).upsert(
        env=get_current_env(),
        entity_id=_OWNER,
        bot_id=_SHARED_BOT_ID,
        document=_DOCUMENT,
        size_bytes=len(_DOCUMENT.encode("utf-8")),
        schema_version=1,
        modifier=_OWNER,
    )
    make_staff_user(world, user_id=_MEMBER)
    make_collaborator(
        world,
        bot_id=_SHARED_BOT_ID,
        owner_id=_OWNER,
        user_id=_MEMBER,
        role="member",
        operator_id=_OWNER,
    )


def _seed_no_bot(world) -> None:
    """Only the verifier — the bot deliberately does not exist."""
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


# ── collaborator bars ──────────────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/config-manifest",
    scenario="a_member_collaborator_can_read_it",
    input=CaseInput(
        path_params={"bot_id": _SHARED_BOT_ID},
        query_params=_MEMBER_QUERY,
        headers=_MEMBER_HEADERS,
    ),
    seed=_seed_member,
    expect=ExpectSuccess(
        status=200, json_contains={"code": 200000, "data": {"document": _DOCUMENT}}
    ),
)
def get_config_manifest_as_member():
    """Reading how a bot is configured is part of working on it, so the read bar
    is MEMBER — the same bar the channels reads take."""


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/{bot_id}/config-manifest",
    scenario="a_member_collaborator_cannot_write_it",
    input=CaseInput(
        path_params={"bot_id": _SHARED_BOT_ID},
        query_params=_MEMBER_QUERY,
        headers=_MEMBER_HEADERS,
        json_body={"document": "schema_version: 1\n"},
    ),
    seed=_seed_member,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def put_config_manifest_as_member_is_refused():
    """Replacing the manifest decides what the bot *is*, so the write bar is
    ADMIN. The refusal is a masked 404, byte-identical to a bot that does not
    exist — anything finer would confirm the bot to a caller who may not reach
    it."""


# ── PUT starts an apply (W8, §2.6) ─────────────────────────────────────────

from agentclaw.community.adapters.http.openapi_v1.bots.config_manifest_support import (  # noqa: E402
    SCRIPT_DELIVERY_NOTE,
)
from agentclaw.community.api.bot_config_manifest_apply_service import (  # noqa: E402
    BotConfigManifestApplyServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import (  # noqa: E402
    BotConfigManifestApplyLockRepositoryProtocol,
    BotConfigManifestApplyRepositoryProtocol,
)
from tests.community.framework import bind_overrides  # noqa: E402

#: A document teclaw can carry: identity only, no script.
_IDENTITY_DOCUMENT = (
    "schema_version: 1\nmanifest:\n  identity:\n"
    "    - type: SOUL.md\n      content: '# Who I am'\n"
)


def _the_apply_is_running_and_readable(response, world) -> None:
    data = response.json()["data"]
    assert data["apply"]["result"] == "RUNNING" and data["apply"]["reason"] is None
    apply_id = data["apply"]["apply_id"]
    assert apply_id
    # The id names a real RUNNING record, under the ``put`` trigger.
    latest = world.get(BotConfigManifestApplyRepositoryProtocol).latest(
        env=get_current_env(), entity_id=_OWNER, bot_id=_BOT_ID
    )
    assert latest is not None and latest.apply_id == apply_id
    assert latest.status == "RUNNING" and latest.trigger == "put"


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/{bot_id}/config-manifest",
    scenario="starts_an_apply_and_notes_the_script",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS,
        json_body={"document": _DOCUMENT},
    ),
    seed=_seed_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"warnings": [SCRIPT_DELIVERY_NOTE]}},
    ),
    extra_assertions=(_the_apply_is_running_and_readable,),
)
def put_config_manifest_starts_an_apply():
    """Storing starts an apply of the stored document, trigger ``put``, and the
    response carries its id. The document declares a script, so the response
    also says the script takes effect on the next start."""


def _seed_bot_with_the_lock_held(world) -> None:
    _seed_bot(world)
    assert world.get(BotConfigManifestApplyLockRepositoryProtocol).acquire(
        env=get_current_env(), entity_id=_OWNER, bot_id=_BOT_ID, holder_user_id="someone-else"
    ) is not None


def _stored_anyway(response, world) -> None:
    record = world.get(BotConfigManifestRepositoryProtocol).get(
        env=get_current_env(), entity_id=_OWNER, bot_id=_BOT_ID
    )
    assert record is not None and record.document == _DOCUMENT


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/{bot_id}/config-manifest",
    scenario="a_held_lock_is_not_started_and_the_document_is_still_stored",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS,
        json_body={"document": _DOCUMENT},
    ),
    seed=_seed_bot_with_the_lock_held,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"apply": {"apply_id": "", "result": "NOT_STARTED", "reason": "apply_in_progress"}},
        },
    ),
    extra_assertions=(_stored_anyway,),
)
def put_config_manifest_while_another_apply_runs():
    """D-8: a PUT that could not start an apply still stores, and says why."""


def _seed_pending_arca_bot(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    _insert_bot(world, bot_id=_BOT_ID, status="PENDING")


def _warns_about_the_container(response, _world) -> None:
    warnings = response.json()["data"]["warnings"]
    assert any(w.startswith("the bot is PENDING") for w in warnings), warnings
    assert any(f"/openapi/v1/bots/{_BOT_ID}/config-manifest/apply" in w for w in warnings)


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/{bot_id}/config-manifest",
    scenario="a_pending_arca_bot_is_warned_about_container_bound_categories",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS,
        json_body={"document": _IDENTITY_DOCUMENT},
    ),
    seed=_seed_pending_arca_bot,
    expect=ExpectSuccess(status=200, json_contains={"code": 200000, "data": {"apply": {"result": "RUNNING"}}}),
    extra_assertions=(_warns_about_the_container,),
)
def put_config_manifest_on_a_pending_arca_bot():
    """D-2: the apply starts anyway; the caller is told what will fail and
    which call to make once the bot is up."""


def _seed_pending_teclaw_platform_managed(world) -> None:
    """A PENDING teclaw bot, with the apply service's strategy switched on."""
    from agentclaw.community.core.bot_config_manifest.apply.delivery import (
        DeliveryStrategyFactory,
        TeclawPlatformBindings,
    )
    from agentclaw.community.core.bot_config_manifest.services.config_manifest_apply_service import (
        BotConfigManifestApplyService,
    )

    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    _insert_bot(world, bot_id=_TECLAW_BOT_ID, engine="teclaw", status="PENDING")
    applies = bind_overrides(
        world, BotConfigManifestApplyServiceProtocol, {}, also_bind=(BotConfigManifestApplyService,)
    )
    bindings = world.get(TeclawPlatformBindings)
    applies._strategies = DeliveryStrategyFactory(
        is_teclaw=lambda engine: engine == "teclaw",
        teclaw_platform_managed=True,
        arca_ports=applies._arca_ports,
        teclaw_platform_ports=bindings.platform_ports,
        redeliver=bindings.redeliver,
    )


def _no_container_note(response, _world) -> None:
    assert response.json()["data"]["warnings"] == []


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/{bot_id}/config-manifest",
    scenario="a_pending_teclaw_bot_on_the_platform_path_is_not_warned",
    input=CaseInput(
        path_params={"bot_id": _TECLAW_BOT_ID}, query_params=_QUERY, headers=_HEADERS,
        json_body={"document": _IDENTITY_DOCUMENT},
    ),
    seed=_seed_pending_teclaw_platform_managed,
    expect=ExpectSuccess(status=200, json_contains={"code": 200000, "data": {"apply": {"result": "RUNNING"}}}),
    extra_assertions=(_no_container_note,),
)
def put_config_manifest_on_a_pending_teclaw_bot():
    """Nothing needs the container on the platform-managed path, so there is
    nothing to warn about: the apply writes platform state and provisioning
    composes from it."""
