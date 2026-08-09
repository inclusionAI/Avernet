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
*forgeable*: until delegation lands the only user a caller may name is itself,
so a parameter naming anyone else is a 403 and nothing about who may call what
has changed.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.openapi_v1 import (
    PUBLIC_API_PREFIX,
    build_public_router,
)
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
def client() -> TestClient:
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
    # The response is what these tests are about, so observe it rather than
    # letting an unhandled exception be re-raised into the test.
    return TestClient(app, raise_server_exceptions=False)


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
#: Pinned by address rather than counted, so adding a fifth is a deliberate edit
#: to this list with a reason attached — which is the only thing standing
#: between "this operation has no user dimension" and "somebody found the
#: parameter inconvenient".
_NO_USER_DIMENSION = {
    # Name uniqueness is checked across the tenant, not within one user's bots.
    ("get", f"{PUBLIC_API_PREFIX}/bots/check-name"),
    # The marketplace catalogue is identical for every caller in the tenant.
    ("get", f"{PUBLIC_API_PREFIX}/bots/mcp/servers"),
    ("get", f"{PUBLIC_API_PREFIX}/bots/mcp/servers/{{server_code}}"),
    ("get", f"{PUBLIC_API_PREFIX}/bots/mcp/tenants"),
}

#: Bot Logs is excluded for a different reason and must stay that way — see the
#: "Naming the end user" note in ``openapi_v1/__init__.py``. Its own ``user_id``
#: is a filter over other people's traces, not the caller's scope.
_LOGS_PREFIX = f"{PUBLIC_API_PREFIX}/bots/logs"

#: What ``bot_id`` looks like, and must keep looking like: an address where it
#: addresses a bot, a query parameter where it is one, and never a body field.
#: This change deliberately moved none of them.
_BOT_ID_PLACEMENT = {"path": 28, "query": 18, "none": 19}


def _schema() -> dict:
    app = FastAPI()
    app.include_router(build_public_router())
    return app.openapi()


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
    return not path.startswith(_LOGS_PREFIX) and (method, path) not in _NO_USER_DIMENSION


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


def test_fifty_six_operations_take_it():
    """The count is pinned so a silent drop shows up as a number, not a shrug."""
    taking = [
        1
        for path, method, operation in _operations(_schema())
        if _user_scoped(path, method) and _param(operation, USER_ID_QUERY)
    ]
    assert len(taking) == 56


def test_the_exempt_operations_take_none():
    """The four with no user dimension ask for nothing they cannot use."""
    schema = _schema()
    for method, path in _NO_USER_DIMENSION:
        operation = schema["paths"][path][method]
        assert _param(operation, USER_ID_QUERY) is None, f"{method.upper()} {path}"
        assert "403" not in operation["responses"], f"{method.upper()} {path}"


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


def test_bot_id_placement_is_untouched():
    """This change moved no ``bot_id``. If it ever does, it is on purpose."""
    counts = {"path": 0, "query": 0, "none": 0}
    for _, _, operation in _operations(_schema()):
        parameter = _param(operation, "bot_id")
        counts[parameter["in"] if parameter else "none"] += 1
    assert counts == _BOT_ID_PLACEMENT


def test_the_403_is_documented_on_exactly_the_user_scoped_operations():
    for path, method, operation in _operations(_schema()):
        documented = "403" in operation["responses"]
        assert documented is _user_scoped(path, method), f"{method.upper()} {path}"


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
