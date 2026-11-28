"""The public surface names its end user explicitly (``?user_id=``).

Every user-scoped operation used to infer the user it acts for from the verified
principal, which only worked because the gateway resolves one end user per
request and signs it in. An App calling **on behalf of** a user presents its own
credential, and an operation whose published contract never mentions a user has
nowhere to put one — so the contract now says out loud what it always meant.

This file checks the identity dependency seam: what ``require_user_id``
returns, what it refuses, and with which status. The generated-document
inventory is asserted separately in ``test_explicit_user_id_schema.py``.

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
#: What the seam sees when no bypass is configured — the shipped default.
_NO_BYPASS: frozenset[str] = frozenset()
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
            asyncio.run(
                require_user_id(
                    principal={"user_id": _CALLER}, user_id=flood, bypass=_NO_BYPASS
                )
            )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert len(logged) < 500, "a caller must not choose the size of the log line"
    assert "(+9872)" in logged, "and the reader is told how much was dropped"


# ── the seam itself ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_dependency_returns_the_request_user_id():
    resolved = await require_user_id(
        principal={"user_id": _CALLER}, user_id=_CALLER, bypass=_NO_BYPASS
    )

    assert resolved == _CALLER


@pytest.mark.asyncio
async def test_the_dependency_refuses_a_mismatch():
    with pytest.raises(UserIdMismatchError):
        await require_user_id(
            principal={"user_id": _CALLER}, user_id="someone-else", bypass=_NO_BYPASS
        )


@pytest.mark.asyncio
async def test_the_dependency_refuses_an_unverifiable_caller():
    """Fail-closed on the principal before the parameter is even compared."""
    with pytest.raises(MissingPrincipalError):
        await require_user_id(principal=None, user_id=_CALLER, bypass=_NO_BYPASS)


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
                principal={"user_id": _CALLER},
                user_id="someone-else",
                bypass=_NO_BYPASS,
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
