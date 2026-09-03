"""The public surface names its end user explicitly (``?user_id=``).

Every user-scoped operation used to infer the user it acts for from the verified
principal, which only worked because the gateway resolves one end user per
request and signs it in. An App calling **on behalf of** a user presents its own
credential, and an operation whose published contract never mentions a user has
nowhere to put one — so the contract now says out loud what it always meant.

Two halves. The **seam**: what ``require_user_id`` returns, what it refuses, and
with which status. And the **document**: which operations carry the parameter,
where it sits, and which four are exempt — asserted against the generated
description rather than a hand-kept list, so an operation added later is covered
without editing this file.

The refusal is the load-bearing part. Making the user explicit must not make it
*forgeable*: a human caller may name only themselves, while an application may
name only the end user carried by its verified delegation context. A mismatch is
a 403, so making the parameter explicit does not widen who may act for whom.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.openapi_v1 import (
    PUBLIC_API_PREFIX,
)
from tests.community.adapters.http.openapi_v1.conftest import public_document
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    ERROR_RESPONSES,
    USER_SCOPED_ERROR_RESPONSES,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.errors import (
    MissingPrincipalError,
    UserIdMismatchError,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    USER_ID_QUERY,
    UserIdDep,
    require_user_id,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)

_CALLER = "u-42"
_PROBE = f"{PUBLIC_API_PREFIX}/bots/_probe"


@pytest.fixture
def probe_app() -> FastAPI:
    """One route shaped exactly like a real user-scoped handler, and nothing else.

    A probe rather than a mounted group, following ``test_principal_seam.py``:
    the real handlers pull services from the injector, so driving one would test
    the service bindings on the way to testing the identity chain. This declares
    the same ``UserIdDep`` a real handler declares and returns what it resolved,
    which is the whole of what these assertions are about.

    The app-level handlers are imported from ``app.py`` rather than
    re-implemented, so deleting that wiring fails here instead of leaving these
    green against a local copy of it.
    """
    from agentclaw.community.adapters.http.app import (
        _principal_error_handler,
        _user_id_mismatch_handler,
        _validation_error_handler,
    )
    from fastapi.exceptions import RequestValidationError

    app = FastAPI()

    @app.get(_PROBE)
    @envelope_errors
    async def probe(request: Request, user_id: UserIdDep):
        return envelope({"user_id": user_id}, request)

    app.add_exception_handler(MissingPrincipalError, _principal_error_handler)
    app.add_exception_handler(UserIdMismatchError, _user_id_mismatch_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.dependency_overrides[require_principal] = lambda: {"user_id": _CALLER}
    return app


@pytest.fixture
def client(probe_app: FastAPI) -> TestClient:
    # The response is what these tests are about, so observe it rather than
    # letting an unhandled exception be re-raised into the test.
    return TestClient(probe_app, raise_server_exceptions=False)


# ── what the caller gets ────────────────────────────────────────────────────


def test_the_caller_reaches_its_own_scope(client):
    """The parameter repeating the caller is accepted — the only value that is.

    And what the handler receives is the parameter's value, not a second read of
    the principal: that is what makes the handlers already correct on the day
    the two are allowed to differ.
    """
    response = client.get(_PROBE, params={USER_ID_QUERY: _CALLER})

    assert response.status_code == 200
    assert response.json()["data"] == {"user_id": _CALLER}


def test_naming_another_user_is_refused(client):
    """The parameter does not widen who a caller can reach — that is delegation."""
    response = client.get(_PROBE, params={USER_ID_QUERY: "someone-else"})

    assert response.status_code == 403
    body = response.json()
    assert body["code"] == 403000
    assert body["message"] == "Forbidden"
    assert body["data"] is None


def test_two_rejected_ids_give_identical_answers(client):
    """The refusal says nothing about the user it was asked for."""
    first = client.get(_PROBE, params={USER_ID_QUERY: "someone-else"})
    second = client.get(_PROBE, params={USER_ID_QUERY: "another-one"})

    assert first.status_code == second.status_code == 403
    assert _without_request_id(first) == _without_request_id(second)


# ── precedence: 401 outranks everything ─────────────────────────────────────


def test_a_missing_parameter_is_a_validation_failure(client):
    """422, not 401: the caller is authenticated, the request is incomplete."""
    assert client.get(_PROBE).status_code == 422


def test_an_empty_parameter_is_a_validation_failure_too(client):
    """A blank value must not read as "the caller" — it names nobody."""
    assert client.get(_PROBE, params={USER_ID_QUERY: ""}).status_code == 422


def test_no_verified_caller_is_still_a_401_whatever_the_parameter_says(client):
    """The 401 comes first, so the parameter can never stand in for a credential.

    This is the "nothing else changes" property: a request with no verified
    principal is refused exactly as it was before the parameter existed.
    """
    client.app.dependency_overrides[require_principal] = lambda: None

    response = client.get(_PROBE, params={USER_ID_QUERY: _CALLER})

    assert response.status_code == 401


def test_a_long_subject_id_is_not_locked_out(probe_app):
    """No upper bound on the parameter, because the identity boundary has none.

    ``GatewayUser.id`` is an unconstrained ``str`` and the verifier refuses only
    a *blank* subject id, so any cap here would reject a caller whose credential
    the gateway accepts — a 422 on all 56 operations for a value that matches
    the signed principal exactly. ``min_length=1`` is safe for the opposite
    reason: it has a counterpart at that boundary.
    """
    long_id = "u-" + "x" * 4000
    probe_app.dependency_overrides[require_principal] = lambda: {"user_id": long_id}
    client = TestClient(probe_app, raise_server_exceptions=False)

    response = client.get(_PROBE, params={USER_ID_QUERY: long_id})

    assert response.status_code == 200
    assert response.json()["data"] == {"user_id": long_id}


def test_a_rejected_id_cannot_choose_how_much_it_logs(caplog):
    """The bound moved from the request to the log, so it still has one.

    Removing the cap means a refused caller controls an unbounded string. It is
    escaped (no forged lines) and truncated (no choosing how many bytes each
    refusal costs), and the truncation says how much it dropped so a reader is
    not misled about what was sent.
    """
    flood = "z" * 10_000

    with caplog.at_level(logging.WARNING):
        with pytest.raises(UserIdMismatchError):
            asyncio.run(require_user_id(principal={"user_id": _CALLER}, user_id=flood))

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert len(logged) < 500, "a caller must not choose the size of the log line"
    assert "(+9872)" in logged, "and the reader is told how much was dropped"


# ── the seam itself ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_dependency_returns_the_request_user_id():
    resolved = await require_user_id(principal={"user_id": _CALLER}, user_id=_CALLER)

    assert resolved == _CALLER


@pytest.mark.asyncio
async def test_the_dependency_refuses_a_mismatch():
    with pytest.raises(UserIdMismatchError):
        await require_user_id(principal={"user_id": _CALLER}, user_id="someone-else")


@pytest.mark.asyncio
async def test_the_dependency_refuses_an_unverifiable_caller():
    """Fail-closed on the principal before the parameter is even compared."""
    with pytest.raises(MissingPrincipalError):
        await require_user_id(principal=None, user_id=_CALLER)


@pytest.mark.asyncio
async def test_the_mismatch_logs_both_ids(caplog):
    """The response carries a fixed word, so the log is the only record.

    An operator debugging a partner integration needs to know which user was
    asked for and which caller asked. Both values are the caller's own
    identifiers on this path — a parameter disagreeing with a *verified*
    principal is the only way to get here — so neither discloses a third party.
    """
    with caplog.at_level(logging.WARNING):
        with pytest.raises(UserIdMismatchError):
            await require_user_id(
                principal={"user_id": _CALLER}, user_id="someone-else"
            )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "someone-else" in logged
    assert _CALLER in logged


# ── how the 403 is published ────────────────────────────────────────────────


def test_the_403_is_not_declared_surface_wide():
    """``ERROR_RESPONSES`` stays the set *every* operation can return.

    ``test_openapi_error_schema`` asserts every operation documents every status
    in it, so a 403 added there would make Bot Logs — and the four operations
    with no user dimension — advertise a failure they cannot produce.
    """
    assert 403 not in ERROR_RESPONSES
    assert 403 in USER_SCOPED_ERROR_RESPONSES


def _without_request_id(response) -> dict:
    body = dict(response.json())
    body.pop("request_id", None)
    return body


# ── the published document ──────────────────────────────────────────────────

#: The operations that take no ``user_id``, and why each one cannot.
#:
#: Pinned by address rather than counted, so adding another is a deliberate edit
#: to this list with a reason attached — which is the only thing standing
#: between "this operation has no user dimension" and "somebody found the
#: parameter inconvenient".
_NO_USER_DIMENSION = {
    # Name uniqueness is checked across the tenant, not within one user's bots.
    ("get", f"{PUBLIC_API_PREFIX}/bots/check-name"),
    # Source credentials (W3, #1471): tenant-level named objects on an
    # application-operated surface — no user axis at all. The caller is
    # the tenant's application (the edge requires an app credential);
    # a riding user is audit attribution in ``modifier``, never a scope.
    ("get", f"{PUBLIC_API_PREFIX}/bots/source-credentials"),
    ("get", f"{PUBLIC_API_PREFIX}/bots/source-credentials/{{name}}"),
    ("put", f"{PUBLIC_API_PREFIX}/bots/source-credentials/{{name}}"),
    ("delete", f"{PUBLIC_API_PREFIX}/bots/source-credentials/{{name}}"),
    # The marketplace catalogue is identical for every caller in the tenant.
    ("get", f"{PUBLIC_API_PREFIX}/bots/mcp/servers"),
    ("get", f"{PUBLIC_API_PREFIX}/bots/mcp/servers/{{server_code}}"),
    ("get", f"{PUBLIC_API_PREFIX}/bots/mcp/tenants"),
    # The department directory is a tenant-wide catalogue — not the caller's.
    ("get", f"{PUBLIC_API_PREFIX}/org/dept"),
    ("get", f"{PUBLIC_API_PREFIX}/bots/catalog/search"),
    ("get", f"{PUBLIC_API_PREFIX}/bots/catalog/discover"),
    # Tenant-identical marketplace searches expose no user-scoped state.
    ("post", f"{PUBLIC_API_PREFIX}/bots/market/skills"),
    ("post", f"{PUBLIC_API_PREFIX}/bots/market/mcp-servers"),
    ("post", f"{PUBLIC_API_PREFIX}/bots/market/skill-center/skills"),
    # Public Skill Center status and tag catalogues are tenant-wide reads.
    ("get", f"{PUBLIC_API_PREFIX}/bots/skills/{{skill_code}}/publish/status"),
    ("get", f"{PUBLIC_API_PREFIX}/bots/market/skill-center/tags"),
    # The load-test endpoint answers a constant. It reads nothing and writes
    # nothing, so there is no scope for a user id to name — and a synthetic
    # endpoint measuring the shared path must not be the one exception that
    # measures a dependency the path does not have.
    ("get", f"{PUBLIC_API_PREFIX}/bots/loadtest/hello"),
    # Task public surface: execute submits (owner in body), dashboard reads by
    # task_id — neither scopes to a caller-supplied user_id. grant/revoke
    # identify the operator from the verified principal (Cookie/Referer relayed
    # to secbaas, the api-key held server-side), not a caller-supplied user_id.
    # list does take a caller-supplied user_id filter, so it is NOT here.
    ("post", f"{PUBLIC_API_PREFIX}/collaboration/tasks/execute"),
    ("get", f"{PUBLIC_API_PREFIX}/collaboration/tasks/dashboard"),
    ("post", f"{PUBLIC_API_PREFIX}/collaboration/tasks/grant"),
    ("post", f"{PUBLIC_API_PREFIX}/collaboration/tasks/revoke"),
}

# Read-only operations that accept a user_id as a caller-selected filter rather
# than as the authenticated user's acting scope. They still require the query
# parameter, but must not advertise the owner-mismatch 403.
_USER_ID_FILTER_ONLY = {
    ("get", f"{PUBLIC_API_PREFIX}/collaboration/tasks/list"),
}

#: Operations whose ``user_id`` is a directory filter, not the self-confirm
#: seam. Same spelling, opposite contract: elsewhere ``user_id`` is *who this
#: call acts for* and must be the caller (403 otherwise); here it is *whose
#: identity to return*, and any authenticated human caller may name any user.
#: So it is REQUIRED, carries no 403, and stays out of both _NO_USER_DIMENSION
#: (it takes the param) and the user-scoped-required rule (the param is not
#: self-confirm). Mirrors /bots/logs/traces' carve-out (the first opposite-
#: contract spelling). There is no whoami fall-back: the param names whose
#: identity to return, and its absence is a 422.
_DIRECTORY_USER_ID = {("get", f"{PUBLIC_API_PREFIX}/org/user")}

# Operations that are user-scoped but deliberately non-delegable. They derive
# the actor from the verified human principal and therefore publish no
# caller-selectable ``user_id``. They may still document a domain-specific 403
# (the Join Request operation is mounted with the Space authorization errors).
_AUTHENTICATED_SELF = {
    ("post", f"{PUBLIC_API_PREFIX}/bots/spaces/{{space_id}}/join-requests"),
    # README resolves the acting user from the verified principal; callers do
    # not get a second, steerable user_id query parameter.
    ("get", f"{PUBLIC_API_PREFIX}/bots/skills/{{skill_id}}/readme"),
}

#: Bot Logs is excluded for a different reason and must stay that way — see the
#: "Naming the end user" note in ``openapi_v1/__init__.py``. Its own ``user_id``
#: is a filter over other people's traces, not the caller's scope.
_LOGS_PREFIX = f"{PUBLIC_API_PREFIX}/bots/logs"

#: What ``bot_id`` looks like, and must keep looking like: an address where it
#: addresses a bot, a query parameter where it is one, and never a body field.
#: This change deliberately moved none of them. ``none`` counts operations with
#: no ``bot_id`` at all, so it grows by one for each such operation added — the
#: load-test endpoint is the twentieth.
#:
#: ``query`` moved 18 → 20 when the resources file endpoints were re-addressed by
#: workspace path (#1000): ``{resource_id}/download`` and ``{resource_id}/preview``
#: went away and ``DELETE ""``, ``/download``, ``/preview`` and ``/mkdir`` replaced
#: them, all four taking ``bot_id`` as a query parameter — which is what it is
#: there, since the address is the file's path and not the bot's.
#:
#: ``query`` then moved 20 → 16 when the resources group became files-only: the
#: five record-addressed operations went with the links they served (``POST ""``,
#: ``GET``/``PUT``/``DELETE /{resource_id}``, ``check-name``) and ``GET /stat``
#: replaced them, all query-addressed like the rest of the group. ``path`` is
#: untouched by that: those five carried ``{resource_id}``, never a ``{bot_id}``.
#:
#: ``path`` moved 31 → 34 with the startup-script operations (GET/PUT/DELETE on
#: ``/bots/{bot_id}/startup-script``) — all three address a bot, none moved.
#:
#: Then bot-first addressing moved 34/16/21 → 54/1/16, and this counter stopped
#: meaning what its name says: ``bot_id`` is no longer untouched, it was the
#: point. Every operation that acts on one bot now names it in the path, so the
#: only ``query`` left is ``GET /bots/logs/traces``, where ``bot_id`` is a
#: filter over a tenant-level trace query rather than an address — the one
#: placement the rule deliberately keeps. ``none`` fell 21 → 16 because the five
#: operations that named no bot at all (the resources and routines collection
#: roots, skills list and upload) now do.
#:
#: ``none`` then moved 16 → 35 when the 19 collaboration operations for Spaces,
#: work orders and recipient notifications were added. None of those operations
#: addresses a bot, so the path and query counts remain unchanged.
#:
#: ``path`` then moved 54 → 56 when the two Bot Chats operations were added.
#:
#: ``none`` then moved 35 → 36 with the user-identity read: it answers who
#: the caller is and addresses no bot.
#:
#: The combined Bot Workshop surface then adds a net 27 bot-addressed operations
#: and five account-level operations. The trace filter remains the sole query
#: placement. Together with the user-identity read, the combined contract is
#: 83/1/45. Channels adds six Bot-addressed operations, and the Bot Space
#: reassignment endpoint adds one more, yielding 90/1/45. Session Favorites then
#: adds three Bot-addressed operations. IAM-token retrieval and optional Caller
#: preparation share one Bot-addressed operation. The Space
#: Skill list adds one more account-level operation. Editors and render screens
#: add another nine Bot-addressed operations, while the Bot catalog contributes
#: two account-level reads, and the read-only Node inventory adds one more
#: Bot-addressed operation, yielding 104/1/49. Skill Installation then adds
#: three Bot-addressed operations. Repo Catalog then adds three more
#: Bot-addressed plus eight account-level operations, yielding 110/1/57.
#: The merged contract contains 132 path-addressed Bots, one legacy
#: query-addressed operation, and 53 non-Bot operations — the six Harness
#: operations are Bot-addressed under ``/bots/{bot_id}/harness``.
#:
#: ``none`` then moved 53 → 54 with the department directory search
#: (``/openapi/v1/org/dept``), an account-level catalogue read that addresses no bot.
#:
#: ``path`` then moved 132 → 133 with the BCS publish-to-users operation
#: (``POST /openapi/v1/bots/{bot_id}/public-bcs``): it addresses a bot and acts
#: for the operator, so it is bot-path-addressed like the rest of the surface.
#: The two IAM operations then merged into one Bot-addressed operation: the
#: existing Caller path was renamed while the account-level IAM read went away,
#: so ``path`` stays unchanged and ``none`` decreases by one. Bot editor
#: requests then add one path-addressed Bot operation. The metadata query
#: added alongside it is user-scoped but has no ``bot_id`` parameter. The BCS
#: publish-to-users route moved from /bots/{bot_id}/public-bcs to the external
#: /collaboration/bots/{bot_uuid}/public, so one path-addressed {bot_id}
#: operation became a {bot_uuid}-named one: path -1, none +1.
#:
#: ``none`` then moved 62 → 63 with the owner-level routines aggregate
#: (``GET /openapi/v1/bots/routines/all``): it lists the named user's whole
#: fleet, so it addresses no single bot.
#:
#: The task grant/revoke operations also carry the target in ``bcs_bot_id``
#: request-body fields rather than a ``bot_id`` parameter, adding two more
#: operations without changing the path/query counts.
#:
#: ``path`` then moved 146 → 150 with the four config-manifest operations
#: (``GET``/``PUT``/``DELETE …/{bot_id}/config-manifest`` and
#: ``GET …/config-manifest/capabilities``). Each addresses one bot and
#: resolves it as the named user's, so all four are bot-path-addressed like
#: the rest of the surface. ``none`` is 97 because task template execution
#: is internal to ``execute`` and has no separate route; it then moved
#: 97 → 101 with the four source-credentials operations (W3, #1471) —
#: tenant-level rows, no bot dimension at all. The W3 rework moved the
#: group under ``/bots/source-credentials`` and made all four operations
#: user-parameter-free (application-operated), which changes no count here.
#:
#: ``path`` then moved 150 → 153 with the three apply operations (W4, #1472:
#: ``POST …/config-manifest/apply``, ``GET …/config-manifest/last-apply``,
#: ``GET …/config-manifest/applies/{apply_id}``). The ``apply_id`` is a handle
#: for polling, never what authorizes the read — the bot is still addressed in
#: the path and still resolved as the named user's, which is what makes an id
#: from another bot resolve to nothing.
#:
#: CLI caller configuration then took ``path`` 153 → 154 — one more
#: owner-addressed operation.
#:
#: W13 (#1696) adds the create-with-manifest pair, one to each column.
#: ``GET …/bots/{bot_id}/with-manifest/status`` is bot-path-addressed like the
#: rest (154 → 155); ``POST …/bots/with-manifest`` names no bot because it is
#: the request that allocates one, so it joins ``none`` (101 → 102) beside
#: ``POST /openapi/v1/bots`` itself.
#: Directory download adds one more bot-path-addressed resource operation.
#: Space Skill Version Copy adds one account-level operation; it is addressed by
#: Space and Skill version rather than by Bot. Human Chat adds eleven Bot-path
#: operations while retaining separate caller and Bot-owner identities.
#:
#: W9 (#1477) adds the three ``cli-tools`` operations — install, list and
#: delete. All three are bot-path-addressed like the config-manifest group they
#: sit beside, so ``path`` 167 → 170 and nothing else moves.
_BOT_ID_PLACEMENT = {"path": 170, "query": 1, "none": 103}


def _schema() -> dict:
    return public_document()


def _current_operations(schema: dict):
    """``(path, method, operation)`` for the operations of the current contract.

    The retiring addresses answer beside them and take the same ``user_id``,
    but every count in this file is pinned — and a pin that silently doubled
    when the deprecated package landed, then halved again when it went, would
    be measuring the migration rather than the rule. The rule is about the
    surface this API has.
    """
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            if not operation.get("deprecated", False):
                yield path, method, operation


def _operations(schema: dict):
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            yield path, method, operation


def _param(operation: dict, name: str) -> dict | None:
    for parameter in operation.get("parameters", []):
        if parameter["name"] == name:
            return parameter
    return None


def _user_scoped(path: str, method: str) -> bool:
    return (
        not path.startswith(_LOGS_PREFIX)
        and (method, path) not in _NO_USER_DIMENSION
        and (method, path) not in _AUTHENTICATED_SELF
        and (method, path) not in _USER_ID_FILTER_ONLY
        and (method, path) not in _DIRECTORY_USER_ID
    )


def test_every_user_scoped_operation_requires_user_id_in_the_query():
    """A route added later cannot quietly go back to inferring the user."""
    offenders = []
    for path, method, operation in _operations(_schema()):
        if not _user_scoped(path, method):
            continue
        parameter = _param(operation, USER_ID_QUERY)
        if parameter is None or parameter["in"] != "query" or not parameter["required"]:
            offenders.append(f"{method.upper()} {path} -> {parameter}")
    assert not offenders, f"operations not naming their user in the query: {offenders}"


def test_filter_only_operations_require_user_id_without_owner_scope_403():
    """Caller-selected filters remain required query parameters, not owner scopes."""
    schema = _schema()
    for method, path in _USER_ID_FILTER_ONLY:
        operation = schema["paths"][path][method]
        parameter = _param(operation, USER_ID_QUERY)
        assert parameter is not None, f"{method.upper()} {path}"
        assert parameter["in"] == "query", f"{method.upper()} {path}"
        assert parameter["required"] is True, f"{method.upper()} {path}"
        assert "403" not in operation["responses"], f"{method.upper()} {path}"


def test_the_pinned_number_of_operations_take_it():
    """The count is pinned so a silent drop shows up as a number, not a shrug.

    60 → 62 with the resources file endpoints re-addressed by workspace path
    (#1000): four new operations (``DELETE ""``, ``/download``, ``/preview``,
    ``/mkdir``) replaced two id-addressed ones, and every one of them takes the
    caller's ``user_id`` like the rest of the user-scoped surface.

    65 → 61 when the resources group became files-only: five record-addressed
    operations were removed and ``GET /stat`` added. A *drop* is the direction
    this pin exists to catch, so it is worth saying plainly that this one is
    intended — the five went away with the link resources they served, not by
    losing the dependency.

    61 → 77 with the service-Bot lifecycle surface: conversion, approval config,
    version reads/actions and edit-lock operations all act for an explicit user.
    The five bot-first Editors operations bring the total to 119, and the four
    render-screen operations bring the total to 123, and the read-only Node
    inventory brings the current total to 124. Caller identity context plus MCP
    and CLI call-type configuration add three Bot-addressed, user-scoped operations.
    """
    taking = [
        1
        for path, method, operation in _current_operations(_schema())
        if _user_scoped(path, method) and _param(operation, USER_ID_QUERY)
    ]
    # 60 on the merge base, +3 for the startup-script operations, +2 for the
    # resources file endpoints re-addressed by workspace path (#1000), then -4
    # for the files-only resources group, then +19 for Spaces, work orders and
    # recipient-notification operations added by the collaboration API, then +2
    # for the Bot Chats operations. The combined Bot Workshop surface adds a
    # further net 32 user-scoped operations (27 bot-addressed and five
    # account-level operations), then +6 for Bot-scoped Channels CRUD/status,
    # then +1 for Bot Space reassignment, +3 for Session Favorites, +2 for
    # the merged IAM-token/Caller preparation operation, +1 for Space Skill list, then
    # +5 for Editors and +4 for render screens. The read-only Node inventory adds
    # the final operation. Skill Installation adds three further Bot-addressed
    # operations, Repo Catalog adds seven operations, SkillSet adds eleven, and
    # MCP adds eight operations, the Harness surface adds six Bot-addressed
    # operations, Session File adds six more, Bot metadata queries add one, and
    # Bot editor requests add one. The BCS publish-to-users route moved from the
    # internal /bots/{bot_id}/public-bcs path to the external contract path
    # /collaboration/bots/{bot_uuid}/public (same op count, {bot_uuid} not {bot_id}).
    # Service publication version upgrade adds one Bot-addressed operation.
    # GET .../collaboration/tasks/list briefly counted here; it now takes
    # ``user_id`` as a caller-selected filter with no owner-mismatch 403, so it
    # moved to _USER_ID_FILTER_ONLY (asserted by its own test) and left this
    # count: 182 → 181. execute/dashboard take no user_id at all — see
    # _NO_USER_DIMENSION.
    # Caller identity context plus MCP/CLI call-type updates add three operations.
    # Space Skill Grant management adds four Space-addressed operations,
    # editor-request command adds one, and permanent Draft Edit Lease adds four.
    # The owner-level routines aggregate (GET /bots/routines/all) adds one
    # account-level operation. Phase 2 Group 1 adds fourteen Space-addressed
    # creation/detail/Draft/Published-Version operations; none addresses a Bot,
    # and each carries the explicit user dimension. The merged surface contains
    # 207 operations. Phase 2 Group 3 Publication adds five Space-addressed
    # operations, Group 4 adds three Bot-addressed Reference operations plus
    # one account-level manual SC Public Sync operation, and Group 5 adds the
    # Offline impact and command operations. All twelve are user-scoped,
    # bringing the combined surface to 218. The public task execute operation is the single task submission surface;
    # static-template execution is selected inside execute rather than exposed as a route. The config
    # manifest adds four Bot-addressed operations — read, replace, clear, and
    # the capability read — all user-scoped, 223 after the obsolete
    # run-template route was removed. W3 (#1471) added a user-taking
    # PUT /source-credentials/{name} (223), and its rework took it back off
    # the user parameter — the surface is application-operated, the audit
    # actor composes off the principal alone — landing on 222 again.
    # Applying the manifest (W4, #1472) adds three more — apply, the
    # last-apply read, and the poll by ``apply_id``: 222 → 225. CLI caller
    # configuration adds an owner-addressed user-scoped operation: 225 → 226.
    # Creating a bot with its manifest (W13, #1696) adds two — the submission
    # and its status poll: 226 → 228. Both name the end user for the same
    # reason the ordinary create does: they spend that user's quota and read
    # that user's rows, and neither is admissible to an application caller.
    # Directory download adds one more user-scoped resource operation.
    # Space Skill Version Copy adds one more user-scoped operation. The isolated
    # Human Chat surface adds eleven caller-owned operations: 230 → 241.
    # W9's three cli-tools operations (#1477) are user-scoped for the reason the
    # config-manifest group beside them is: they may address a *shared* bot, so
    # the owner arrives on the wire while the caller stays the acting user —
    # which is what ``installed_by`` records: 241 → 244.
    assert len(taking) == 244


def test_the_exempt_operations_take_none():
    """The exempt operations ask for nothing they cannot use."""
    schema = _schema()
    for method, path in _NO_USER_DIMENSION:
        operation = schema["paths"][path][method]
        assert _param(operation, USER_ID_QUERY) is None, f"{method.upper()} {path}"
        assert "403" not in operation["responses"], f"{method.upper()} {path}"


def test_authenticated_self_operations_derive_the_user_from_the_principal():
    """Non-delegable self-service operations expose no steerable user id."""
    schema = _schema()
    for method, path in _AUTHENTICATED_SELF:
        operation = schema["paths"][path][method]
        assert _param(operation, USER_ID_QUERY) is None, f"{method.upper()} {path}"


def test_bot_logs_keeps_its_own_meaning_of_user_id():
    """Same spelling, opposite contract — and it must not acquire a 403.

    ``GET …/logs/traces`` takes a ``user_id`` that says *whose traces to read*;
    a user+App caller may point it at someone else. Giving that operation this
    rule's 403 would remove the capability the route exists to provide.
    """
    schema = _schema()
    logs = [
        (path, method, operation)
        for path, method, operation in _operations(schema)
        if path.startswith(_LOGS_PREFIX)
    ]
    assert len(logs) == 5
    assert not any("403" in operation["responses"] for _, _, operation in logs)

    traces = schema["paths"][f"{_LOGS_PREFIX}/traces"]["get"]
    assert _param(traces, USER_ID_QUERY) is not None, (
        "the filter is part of that operation's contract, not this rule's"
    )


def test_org_user_directory_filter_is_required_without_403():
    """``GET /org/user`` takes a REQUIRED ``user_id`` — opposite contract.

    Same spelling, opposite meaning: elsewhere ``user_id`` is *who this call
    acts for* and must be the caller (403 otherwise); here it is *whose
    identity to return*, a directory filter any authenticated human caller may
    name any user for — so it is REQUIRED (no whoami fall-back; absence is a
    422) and carries no 403. Mirrors the /bots/logs/traces carve-out, the
    first opposite-contract spelling.
    """
    schema = _schema()
    operation = schema["paths"][f"{PUBLIC_API_PREFIX}/org/user"]["get"]
    parameter = _param(operation, USER_ID_QUERY)
    assert parameter is not None, "GET /org/user carries the required user_id"
    assert parameter["in"] == "query"
    assert parameter["required"] is True
    assert "403" not in operation["responses"]


def test_user_id_is_never_a_body_field_or_a_path_segment():
    """The two placements this design rejected, asserted rather than remembered."""
    schema = _schema()
    for path, method, operation in _operations(schema):
        assert "{user_id}" not in path, f"{method.upper()} {path}"
        parameter = _param(operation, USER_ID_QUERY)
        assert parameter is None or parameter["in"] == "query"

    # Request bodies only. A *response* may well carry a user id — Bot Logs'
    # ``ConversationDetail`` describes whose conversation it was — and that has
    # nothing to do with where a request names the user it acts for.
    for name in _request_body_models(schema):
        model = schema["components"]["schemas"][name]
        assert USER_ID_QUERY not in (model.get("properties") or {}), (
            f"{name} declares user_id as a request-body field"
        )


def test_bot_id_is_in_the_path_wherever_it_addresses_a_bot():
    """Counted over the current contract only.

    The retiring addresses carry ``bot_id`` where they always did — in the
    query, in a body, or nowhere — which is the whole point of keeping them.
    Counting them here would measure how far through the migration we are
    rather than whether the rule holds.
    """
    counts = {"path": 0, "query": 0, "none": 0}
    for _, _, operation in _current_operations(_schema()):
        parameter = _param(operation, "bot_id")
        counts[parameter["in"] if parameter else "none"] += 1
    assert counts == _BOT_ID_PLACEMENT


def test_the_403_is_documented_on_exactly_the_user_scoped_operations():
    for path, method, operation in _operations(_schema()):
        documented = "403" in operation["responses"]
        expected = _user_scoped(path, method) or (method, path) in _AUTHENTICATED_SELF
        assert documented is expected, f"{method.upper()} {path}"


def _request_body_models(schema: dict) -> set[str]:
    """Every named component reachable as an operation's request body."""
    found = set()
    for _, _, operation in _operations(schema):
        body = operation.get("requestBody") or {}
        for media in (body.get("content") or {}).values():
            ref = (media.get("schema") or {}).get("$ref")
            if ref:
                found.add(ref.rsplit("/", 1)[-1])
    return found
