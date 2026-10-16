"""The public surface's configured ``user_id`` bypass (``openapi.user_id_bypass``).

A caller authenticated as a person may name only themselves — ``require_user_id``
answers 403 otherwise (``test_explicit_user_id.py``). This block lists ids that
are accepted **as-is** for any such caller: the comparison is skipped for them.
It ships empty, and the provider refuses a non-empty list in ``pre``/``prod``
(``tests/community/di/test_openapi_user_id_bypass_config_provider.py``).

What is pinned here is the seam's side of it: a listed id is accepted, nothing
else loosens, the 401 still comes first, an app with no binding or no injector
resolves to *no* bypass, and a provider that refuses its block is loud rather
than read as empty.

The probe app mirrors the one ``test_explicit_user_id.py`` builds — one route
shaped like a real user-scoped handler, with the app-level handlers from
``app.py`` — and shares its route address and caller id with it.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from injector import Binder, Injector, Module, singleton

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
from agentclaw.community.di.openapi_user_id_bypass_config import (
    OpenApiUserIdBypassConfig,
)
from tests.community.adapters.http.openapi_v1.test_explicit_user_id import (
    _CALLER,
    _PROBE,
)


@pytest.fixture
def probe_app() -> FastAPI:
    """The same probe ``test_explicit_user_id.py`` builds: one user-scoped route.

    The app-level handlers come from ``app.py`` rather than a local copy, for
    the reason given there. No injector is attached: that is the state every
    test below starts from, and one of the states it pins.
    """
    from agentclaw.community.adapters.http.app import (
        _principal_error_handler,
        _user_id_mismatch_handler,
        _validation_error_handler,
    )

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


# The probe app has no injector, so these tests attach one carrying only the
# bypass binding — the same lookup ``resolve_user_id_bypass`` performs on a
# real app.

_SHARED = "shared-account"


class _BypassModule(Module):
    def __init__(self, *user_ids: str) -> None:
        self._config = OpenApiUserIdBypassConfig(user_ids=frozenset(user_ids))

    def configure(self, binder: Binder) -> None:
        binder.bind(OpenApiUserIdBypassConfig, to=self._config, scope=singleton)


class _BrokenBypassModule(Module):
    """What a provider that refuses its block looks like to the seam."""

    def configure(self, binder: Binder) -> None:
        def _refuse() -> OpenApiUserIdBypassConfig:
            raise ValueError("openapi.user_id_bypass must be empty in 'prod'")

        binder.bind(OpenApiUserIdBypassConfig, to=_refuse, scope=singleton)


def _with_injector(app: FastAPI, *modules: Module) -> TestClient:
    app.state.injector = Injector(list(modules))
    return TestClient(app, raise_server_exceptions=False)


def test_a_listed_id_is_accepted_for_a_caller_who_is_not_it(probe_app):
    """The one configured exception: the comparison is skipped for a listed id."""
    client = _with_injector(probe_app, _BypassModule(_SHARED))

    response = client.get(_PROBE, params={USER_ID_QUERY: _SHARED})

    assert response.status_code == 200
    assert response.json()["data"] == {"user_id": _SHARED}


def test_the_bypass_covers_only_the_listed_ids(probe_app):
    """Listing one id does not loosen the check for any other."""
    client = _with_injector(probe_app, _BypassModule(_SHARED))

    assert client.get(_PROBE, params={USER_ID_QUERY: "someone-else"}).status_code == 403
    assert client.get(_PROBE, params={USER_ID_QUERY: _CALLER}).status_code == 200


def test_the_bypass_does_not_stand_in_for_a_credential(probe_app):
    """401 still comes first: a listed id is not a way in without a caller."""
    probe_app.dependency_overrides[require_principal] = lambda: None
    client = _with_injector(probe_app, _BypassModule(_SHARED))

    assert client.get(_PROBE, params={USER_ID_QUERY: _SHARED}).status_code == 401


def test_an_empty_bypass_keeps_the_comparison(probe_app):
    """The shipped default — an empty list — is exactly the behaviour before it existed."""
    client = _with_injector(probe_app, _BypassModule())

    assert client.get(_PROBE, params={USER_ID_QUERY: _SHARED}).status_code == 403


def test_an_app_without_the_binding_keeps_the_comparison(probe_app):
    """Not wired resolves to no bypass — fail closed, not fail to build."""
    client = _with_injector(probe_app)  # an injector with no modules at all

    assert client.get(_PROBE, params={USER_ID_QUERY: _SHARED}).status_code == 403


def test_an_app_without_an_injector_keeps_the_comparison(probe_app):
    """The probe app itself: no ``app.state.injector``, no bypass."""
    client = TestClient(probe_app, raise_server_exceptions=False)

    assert client.get(_PROBE, params={USER_ID_QUERY: _SHARED}).status_code == 403


def test_a_refused_bypass_config_is_loud_not_empty(probe_app):
    """A provider that raises must not be read as "no bypass".

    Silently degrading would hide exactly the misconfiguration the provider
    refuses (a non-empty list in pre/prod), so the error propagates.
    """
    client = _with_injector(probe_app, _BrokenBypassModule())

    assert client.get(_PROBE, params={USER_ID_QUERY: _CALLER}).status_code == 500


@pytest.mark.asyncio
async def test_the_dependency_accepts_a_listed_id_directly():
    resolved = await require_user_id(
        principal={"user_id": _CALLER}, user_id=_SHARED, bypass=frozenset({_SHARED})
    )

    assert resolved == _SHARED


@pytest.mark.asyncio
async def test_a_bypassed_request_is_logged_with_both_ids(caplog):
    """The response carries nothing, so the log is the audit record of the bypass."""
    with caplog.at_level(logging.WARNING):
        await require_user_id(
            principal={"user_id": _CALLER}, user_id=_SHARED, bypass=frozenset({_SHARED})
        )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "bypass" in logged
    assert _SHARED in logged
    assert _CALLER in logged


@pytest.mark.asyncio
async def test_a_bypass_does_not_relax_the_credential_check():
    with pytest.raises(MissingPrincipalError):
        await require_user_id(principal=None, user_id=_SHARED, bypass=frozenset({_SHARED}))
