"""Endpoint-framework coverage for the source-credentials routes (W3, #1471).

Four routes, exercised through the assembled public app (real principal
verification via a locally-minted gateway JWT, real DI graph, real
repository) — the same shape as the startup-script and config-manifest
cases. The coverage gate holds every public route to happy + error
coverage; these are those rows.

This is an application-operated surface: every principal here carries an
**app** identity (the edge requires one); the user riding along is
attributed in the audit actor. Ownership rows cover the 403 the owner
gate answers.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_OWNER = "credential-owner"
_KEY = "source-credentials-framework-signing-key-32-bytes"
_TENANT = "teamclaw"
APP_ID = 42
OTHER_APP_ID = 43


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal(*, app_id: int = APP_ID, with_user: bool = True) -> str:
    now = int(time.time())
    principals = []
    if with_user:
        principals.append(
            {"type": "user", "subject": {"id": _OWNER, "username": "cred@example.test"}}
        )
    principals.append(
        {
            "type": "app",
            "tenant": _TENANT,
            "app": {
                "app_id": app_id,
                "app_name": "partner",
                "owners": "platform-team",
                "tenant": _TENANT,
                "app_type": "integration",
            },
        }
    )
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": principals,
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_APP_ONLY_HEADERS = {PRINCIPAL_HEADER: _principal(with_user=False)}
_OTHER_APP_HEADERS = {PRINCIPAL_HEADER: _principal(app_id=OTHER_APP_ID)}

PUT_BODY = {
    "type": "header",
    "header_name": "PRIVATE-TOKEN",
    "secret": "Bearer fw-secret",
    "allowed_prefixes": ["https://git.example/team/content"],
}
_BAD_PREFIX_BODY = {
    "type": "header",
    "header_name": "PRIVATE-TOKEN",
    "secret": "Bearer fw-secret",
    "allowed_prefixes": ["http://not-https.example/repo"],
}


def _seed_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_credential(world) -> None:
    """A verifier plus one stored credential owned by APP_ID, through the
    REAL service graph (the assembled app's own DI: repository +
    TokenVault with the test profile's empty master key — the singlebox
    passthrough path)."""
    _seed_verifier(world)
    from agentclaw.community.api.source_credential_service import (
        SourceCredentialServiceProtocol,
    )

    world.get(SourceCredentialServiceProtocol).put(
        name="corp-git",
        header_name="PRIVATE-TOKEN",
        secret="Bearer fw-secret",
        allowed_prefixes=PUT_BODY["allowed_prefixes"],
        owner_app_id=APP_ID,
    )


_BASE = "/openapi/v1/bots/source-credentials"


# ── PUT ─────────────────────────────────────────────────────────────────────


@endpoint_test(
    method="PUT",
    path=f"{_BASE}/{{name}}",
    scenario="registers_the_credential",
    input=CaseInput(
        path_params={"name": "corp-git"},
        headers=_HEADERS,
        json_body=PUT_BODY,
    ),
    seed=_seed_verifier,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "name": "corp-git",
                "has_secret": True,
                "type": "header",
                "header_name": "PRIVATE-TOKEN",
                "owner_app_id": APP_ID,
            },
        },
    ),
)
def put_credential_ok():
    """Masked metadata back; the secret never rides the response."""


@endpoint_test(
    method="PUT",
    path=f"{_BASE}/{{name}}",
    scenario="app_only_caller_registers_with_no_on_behalf_of",
    input=CaseInput(
        path_params={"name": "corp-git"},
        headers=_APP_ONLY_HEADERS,
        json_body=PUT_BODY,
    ),
    seed=_seed_verifier,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"name": "corp-git", "has_secret": True},
        },
    ),
)
def put_credential_app_only_ok():
    """The app-only caller the edge admits: ownership without a riding user."""


@endpoint_test(
    method="PUT",
    path=f"{_BASE}/{{name}}",
    scenario="refuses_non_https_prefixes",
    input=CaseInput(
        path_params={"name": "corp-git"},
        headers=_HEADERS,
        json_body=_BAD_PREFIX_BODY,
    ),
    seed=_seed_verifier,
    expect=ExpectError(status=422, json_contains={"data": None}),
)
def put_credential_non_https_prefix_refused():
    """HTTPS-pinned, absolute prefixes — validation refused at write."""


@endpoint_test(
    method="PUT",
    path=f"{_BASE}/{{name}}",
    scenario="rotation_by_non_owner_is_refused",
    input=CaseInput(
        path_params={"name": "corp-git"},
        headers=_OTHER_APP_HEADERS,
        json_body=PUT_BODY,
    ),
    seed=_seed_credential,
    expect=ExpectError(status=403, json_contains={"data": None}),
)
def put_credential_non_owner_refused():
    """A re-PUT replaces the row every citation of the name rides on — the
    owning application's call alone."""


# ── GET ─────────────────────────────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path=f"{_BASE}/{{name}}",
    scenario="reads_masked_metadata",
    input=CaseInput(path_params={"name": "corp-git"}, headers=_HEADERS),
    seed=_seed_credential,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "name": "corp-git",
                "has_secret": True,
                "allowed_prefixes": PUT_BODY["allowed_prefixes"],
                "owner_app_id": APP_ID,
            },
        },
    ),
)
def get_credential_ok():
    """The detail read is masked metadata: name, header, prefixes, no value."""


@endpoint_test(
    method="GET",
    path=f"{_BASE}/{{name}}",
    scenario="unknown_name",
    input=CaseInput(path_params={"name": "no-such-name"}, headers=_HEADERS),
    seed=_seed_verifier,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def get_credential_unknown():
    """A named miss reads as a masked 404, never as a secret-shaped error."""


@endpoint_test(
    method="GET",
    path=_BASE,
    scenario="lists_the_tenant_inventory",
    input=CaseInput(headers=_HEADERS),
    seed=_seed_credential,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": [{"name": "corp-git", "has_secret": True}],
        },
    ),
)
def list_credentials_ok():
    """Masked summaries for every name registered in the caller's tenant."""


# ── DELETE ───────────────────────────────────────────────────────────────────


@endpoint_test(
    method="DELETE",
    path=f"{_BASE}/{{name}}",
    scenario="removes_the_credential",
    input=CaseInput(path_params={"name": "corp-git"}, headers=_HEADERS),
    seed=_seed_credential,
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"deleted": True}},
    ),
)
def delete_credential_ok():
    """Removal is idempotent-success; the next GET reads the empty seat."""


@endpoint_test(
    method="DELETE",
    path=f"{_BASE}/{{name}}",
    scenario="delete_of_an_absent_name_still_succeeds",
    input=CaseInput(path_params={"name": "no-such-name"}, headers=_HEADERS),
    seed=_seed_verifier,
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"deleted": True}},
    ),
)
def delete_credential_idempotent():
    """Idempotent delete mirrors the group contract (deleted=True on re-delete)."""


@endpoint_test(
    method="DELETE",
    path=f"{_BASE}/{{name}}",
    scenario="delete_by_non_owner_is_refused",
    input=CaseInput(path_params={"name": "corp-git"}, headers=_OTHER_APP_HEADERS),
    seed=_seed_credential,
    expect=ExpectError(status=403, json_contains={"data": None}),
)
def delete_credential_non_owner_refused():
    """Delete is the owning application's alone — 403, storage untouched."""


# ── Coverage-gate error shapes for the "cannot-fail" routes ──────────────────
#
# The collection read (empty tenant → 200 []) and the delete (absent name →
# 200 deleted) have no business-shaped failure. Their reachable error shape
# is the surface's own pre-handler refusal: no principal, 401 — these rows
# are the coverage gate's error side for exactly those two (method, path)
# pairs, so they carry that shape honestly instead of borrowing a 404.


@endpoint_test(
    method="GET",
    path=_BASE,
    scenario="unauthenticated_read_is_refused",
    input=CaseInput(headers={}),
    seed=_seed_verifier,
    expect=ExpectError(status=401, json_contains={"data": None}),
)
def list_credentials_unauthenticated_error_shape():
    """No principal on the wire → 401 before the storage is touched."""


@endpoint_test(
    method="DELETE",
    path=f"{_BASE}/{{name}}",
    scenario="unauthenticated_delete_is_refused",
    input=CaseInput(path_params={"name": "corp-git"}, headers={}),
    seed=_seed_verifier,
    expect=ExpectError(status=401, json_contains={"data": None}),
)
def delete_credential_unauthenticated_error_shape():
    """No principal → 401; the idempotent delete contract is untouched."""
