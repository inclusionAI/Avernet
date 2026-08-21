"""What the authorization seam does, on the path no shipped operation takes yet.

Every row on the surface is scaffolding today (``spec.md`` *Decisions* 4), so
``Check`` has no production caller until the first group migrates. That is a
deliberate choice, and it makes this file the *only* thing exercising the code
that will shortly govern the whole surface — so each test below pins one
property and fails for its own reason, rather than a happy path standing in for
a contract.

The fixture router is the point: a real ``PublicAPIRoute`` reading a real
``Check`` row, assembled into a real app with a real injector, so what is tested
is the wiring and not a hand-called function.
"""

from __future__ import annotations

import json

import pytest
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi_injector import attach_injector
from injector import Injector, InstanceProvider, Module

from agentclaw.community.adapters.http.openapi_v1 import authorization as authz
from agentclaw.community.adapters.http.openapi_v1.access_log import (
    PublicApiAccessLogMiddleware,
)
from agentclaw.community.adapters.http.openapi_v1.authorization import (
    Check,
    PublicAPIRoute,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.errors import (
    BotNotFoundError as CollaboratorBotNotFoundError,
)
from agentclaw.community.adapters.http.openapi_v1.responses import envelope_errors
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLogRepositoryProtocol,
    BotRepository,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)

OWNER = "u-owner"
CALLER = "u-caller"
BOT = "b-1"
PATH = "/openapi/v1/bots/{bot_id}/probe"
URL = f"/openapi/v1/bots/{BOT}/probe"


class _Bots:
    """A bot repository that answers only for the one bot under test."""

    def __init__(
        self, *, exists: bool = True, raises: bool = False, owner: str = OWNER
    ) -> None:
        self.exists, self.raises, self.owner = exists, raises, owner

    def get_by_id_and_owner(self, bot_id, owner_id, **_):
        if self.raises:
            raise RuntimeError("database is unavailable")
        if not self.exists or (bot_id, owner_id) != (BOT, self.owner):
            return None
        return {"id": 7, "bot_id": BOT, "owner_id": self.owner, "env": "test"}


class _Collaborators:
    """Answers one level for the caller, or fails, as the test dictates."""

    def __init__(self, level=PermissionLevel.NONE, *, raises: bool = False) -> None:
        self.level, self.raises = level, raises

    def get_operable_permission_level(self, *, bot, user_id, env=None):
        if self.raises:
            raise RuntimeError("collaborator table is unavailable")
        return self.level

    def get_permission_level(self, *a, **k):  # pragma: no cover - fallback path
        return self.get_operable_permission_level(bot={}, user_id=CALLER)

    def list_collaborators(self, *a, **k):  # pragma: no cover - protocol shape
        return []

    def check_collaborator_permission(self, *a, **k):  # pragma: no cover
        return {"has_permission": False}


class _Audit:
    """Collects rows, or refuses to accept them."""

    def __init__(self, *, raises: bool = False) -> None:
        self.rows, self.raises = [], raises

    def insert(self, data):
        if self.raises:
            raise RuntimeError("audit table is unavailable")
        self.rows.append(data)
        return data


def _surface(*, level, bots=None, collaborators=None, audit=None, bar=None, write_raises=None):
    """A one-operation app whose route really carries the seam.

    ``write_raises`` makes the mutation fail *before* a response exists, which
    is a different question from a mutation that fails with one.
    """
    bots = bots or _Bots()
    collaborators = collaborators or _Collaborators(level)
    audit = audit or _Audit()

    row = Check(bar or PermissionLevel.MEMBER)
    authz.AUTHORIZATION[("GET", PATH)] = row
    authz.AUTHORIZATION[("POST", PATH)] = row
    try:
        router = APIRouter(route_class=PublicAPIRoute)

        @router.get(PATH)
        async def read(bot_id: str) -> dict:
            return {"ok": "read"}

        @router.post(PATH)
        async def write(bot_id: str) -> dict:
            if write_raises is not None:
                raise write_raises
            return {"ok": "write"}

        class _M(Module):
            def configure(self, binder):
                # ``InstanceProvider`` rather than ``to=obj``: injector
                # isinstance-checks a bare instance, and these protocols are
                # not all ``@runtime_checkable``.
                binder.bind(BotRepository, to=InstanceProvider(bots))
                binder.bind(
                    CollaboratorServiceProtocol, to=InstanceProvider(collaborators)
                )
                binder.bind(
                    BotCollabLogRepositoryProtocol, to=InstanceProvider(audit)
                )

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[require_principal] = lambda: {"user_id": CALLER}
        mount_public_error_handlers(app)
        attach_injector(app, Injector([_M()]))
        # The real middleware, not a stand-in: it is what publishes the wire
        # status the audit decision reads, so a test app without it would
        # exercise a different path from production.
        app.add_middleware(PublicApiAccessLogMiddleware)
        return user_scoped_client(app, CALLER), audit
    finally:
        authz.AUTHORIZATION.pop(("GET", PATH), None)
        authz.AUTHORIZATION.pop(("POST", PATH), None)


def _get(client, *, caller=CALLER, owner=OWNER):
    return client.get(URL, params={"user_id": caller, "owner_id": owner})


def _post(client, *, caller=CALLER, owner=OWNER):
    return client.post(URL, params={"user_id": caller, "owner_id": owner})


# ── the level check ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("bar", list(PermissionLevel))
def test_owner_passes_every_level(bar):
    """OWNER is the top of the lattice, so no bar can exclude the owner."""
    if bar is PermissionLevel.NONE:
        pytest.skip("NONE is not a bar an operation can declare")
    client, _ = _surface(level=PermissionLevel.OWNER, bar=bar)

    assert _get(client).status_code == 200


def test_caller_at_the_bar_passes():
    client, _ = _surface(level=PermissionLevel.MEMBER, bar=PermissionLevel.MEMBER)

    assert _get(client).status_code == 200


def test_caller_below_the_bar_is_404_not_403():
    """Masked, because 403 would confirm the bot exists.

    That confirmation is the enumeration oracle the whole surface masks
    against — a caller who may not reach a bot must not be able to tell it
    from one that is not there.
    """
    client, _ = _surface(level=PermissionLevel.MEMBER, bar=PermissionLevel.ADMIN)

    response = _get(client)

    assert response.status_code == 404
    # The envelope shape, not the raw ``{"detail": ...}`` fallback: that is
    # what a genuinely absent bot returns on this surface, and matching it is
    # the point (see the byte-identity test below).
    assert response.json()["message"] == "Not found"
    assert response.json()["data"] is None


def test_absent_bot_answers_exactly_as_a_refused_caller():
    """Both paths through the seam produce one answer.

    Weak on its own — both branches raise ``BotAccessRefusedError``, so they
    are identical almost by construction. The check that carries the real
    claim is the next one.
    """
    permitted, _ = _surface(level=PermissionLevel.MEMBER, bar=PermissionLevel.ADMIN)
    missing, _ = _surface(level=PermissionLevel.OWNER, bots=_Bots(exists=False))

    refused, absent = _get(permitted), _get(missing)

    assert (refused.status_code, refused.json()) == (absent.status_code, absent.json())


def test_refusal_is_byte_identical_to_the_rest_of_the_surface():
    """The masking only works if it matches what *other* code returns.

    A bot that genuinely does not exist is answered elsewhere on this surface
    by ``BotNotFoundError`` / ``GrantNotResolvableError``, which are mapped
    through ``ENVELOPE_ERRORS`` into the envelope shape. If the seam's own
    refusal were not mapped there too it would fall through to the raw
    ``{"detail": ...}`` body — same status, different shape — and a caller
    could tell "not permitted" from "no such bot" by the response body alone.
    That is the enumeration oracle the masking exists to close, and it would
    open the instant any row adopted ``Check``.

    Compared at the mapping layer rather than through a fixture app, because
    the claim is about the shared table, not about one route's wiring.
    """
    from agentclaw.community.adapters.http.openapi_v1.errors import (
        BotAccessRefusedError,
        GrantNotResolvableError,
    )
    from agentclaw.community.adapters.http.openapi_v1.responses import (
        mapped_error_response,
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": URL,
            "headers": [],
            "query_string": b"",
            "app": None,
        }
    )

    refused = mapped_error_response(BotAccessRefusedError("nope"), request)
    absent = mapped_error_response(GrantNotResolvableError("nope"), request)

    assert refused is not None, "BotAccessRefusedError is not in ENVELOPE_ERRORS"
    assert absent is not None
    assert bytes(refused.body) == bytes(absent.body)
    assert refused.status_code == absent.status_code == 404


# ── fail closed, on every failure ────────────────────────────────────────────


def test_unresolvable_bot_refuses():
    """A repository that raises must not be read as "no restriction"."""
    client, _ = _surface(level=PermissionLevel.OWNER, bots=_Bots(raises=True))

    assert _get(client).status_code == 404


def test_collaborator_lookup_failure_refuses():
    """The interceptor's ``permission_skipped`` fail-open, deliberately not ported.

    Reading an unavailable collaborator table as "no collaborators" admits a
    stranger at exactly the moment the check meant to stop them could not run.
    """
    client, _ = _surface(
        level=PermissionLevel.OWNER, collaborators=_Collaborators(raises=True)
    )

    assert _get(client).status_code == 404


def test_unwired_services_refuse():
    """No injector at all is a refusal, not a bypass."""
    row = Check(PermissionLevel.MEMBER)
    authz.AUTHORIZATION[("GET", PATH)] = row
    try:
        router = APIRouter(route_class=PublicAPIRoute)

        @router.get(PATH)
        async def read(bot_id: str) -> dict:  # pragma: no cover - must not run
            return {"ok": True}

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[require_principal] = lambda: {"user_id": CALLER}
        mount_public_error_handlers(app)
        client = user_scoped_client(app, CALLER)

        assert _get(client).status_code == 404
    finally:
        authz.AUTHORIZATION.pop(("GET", PATH), None)


# ── the audit record ─────────────────────────────────────────────────────────


def test_non_owner_write_writes_one_audit_row():
    client, audit = _surface(level=PermissionLevel.ADMIN, bar=PermissionLevel.MEMBER)

    assert _post(client).status_code == 200

    assert len(audit.rows) == 1
    row = audit.rows[0]
    assert (row["bot_id"], row["owner_id"], row["operator_id"]) == (BOT, OWNER, CALLER)
    assert json.loads(row["detail"]) == {"route": PATH, "method": "POST"}


def test_owner_write_writes_none():
    """Matching today's behaviour on both surfaces: an owner's action is not audited."""
    client, audit = _surface(level=PermissionLevel.OWNER)

    assert _post(client).status_code == 200

    assert audit.rows == []


def test_read_writes_none():
    """Reads are not audited — measured against the internal surface, 36 of 36."""
    client, audit = _surface(level=PermissionLevel.ADMIN, bar=PermissionLevel.MEMBER)

    assert _get(client).status_code == 200

    assert audit.rows == []


def test_refused_request_writes_none():
    """The record follows the action, and a refused request performed none."""
    client, audit = _surface(level=PermissionLevel.MEMBER, bar=PermissionLevel.ADMIN)

    assert _post(client).status_code == 404

    assert audit.rows == []


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(RuntimeError("the handler blew up"), id="unhandled"),
        pytest.param(HTTPException(status_code=409, detail="conflict"), id="http"),
    ],
)
def test_a_mutation_that_never_produced_a_response_writes_no_audit_row(failure):
    """The two shapes of failure *before* a response, not after one.

    ``_succeeded`` reads a status the middleware published, so it is fair to
    ask what happens when the request dies before there is one. Two different
    mechanisms answer, and neither is the status check:

    - An exception that escapes the handler is thrown back into the gate at
      its ``yield``. The ``yield`` is bare, so it propagates straight out and
      the audit code below it never runs at all.
    - An exception a handler *converts* into a response — this is what an
      ``HTTPException`` does — is a response, so the middleware publishes its
      status and the ordinary ``>= 400`` check suppresses the row.

    Pinned because both are load-bearing and neither is visible in the code
    that appears to make the decision.
    """
    client, audit = _surface(
        level=PermissionLevel.ADMIN, bar=PermissionLevel.MEMBER, write_raises=failure
    )

    try:
        _post(client)
    except RuntimeError:
        pass  # escaped every handler, which is the case under test

    assert audit.rows == []


def test_audit_failure_does_not_fail_the_request():
    """The action already happened; reporting an error for it would be a lie.

    A client retrying on that error would apply the mutation twice, which is a
    worse outcome than a missing row (``spec.md`` *Decisions* 2).
    """
    client, _ = _surface(
        level=PermissionLevel.ADMIN, bar=PermissionLevel.MEMBER, audit=_Audit(raises=True)
    )

    response = _post(client)

    assert response.status_code == 200
    assert response.json() == {"ok": "write"}


def test_audit_failure_log_bounds_caller_supplied_ids(caplog):
    """The record of a *dropped* record must not itself be forgeable.

    This branch is reached only after the check passed, so ``owner_id`` had to
    match the addressed bot's stored owner — better bounded than the refusal
    branch, where any value at all reaches the log. It goes through the same
    helper anyway: this line is the only evidence that an audit row was lost,
    so it is the last line that should be paddable or splittable by the party
    whose action went unrecorded.
    """
    poisoned = "u-owner\nFAKE AUDIT LINE"
    client, _ = _surface(
        level=PermissionLevel.ADMIN,
        bar=PermissionLevel.MEMBER,
        bots=_Bots(owner=poisoned),
        audit=_Audit(raises=True),
    )

    with caplog.at_level("ERROR"):
        assert _post(client, owner=poisoned).status_code == 200

    dropped = [r for r in caplog.records if "audit write failed" in r.getMessage()]

    assert dropped, "the dropped audit row was not logged at all"
    line = dropped[0].getMessage()
    assert "\n" not in line, "a caller-supplied newline reached the log verbatim"
    assert "FAKE AUDIT LINE" in line, "the value should be escaped, not dropped"


def test_the_refusal_message_cannot_forge_the_app_level_404_line(caplog):
    """The refusal reaches a second log line by a route the seam does not own.

    ``app.py``'s 404 handler renders ``str(exc)`` through ``%s``, so the
    exception message is a caller-supplied value on a log line written outside
    this module. Bounding it where the exception is raised is what keeps that
    handler identical to its three siblings instead of a special case.
    """
    client, _ = _surface(level=PermissionLevel.NONE, bar=PermissionLevel.ADMIN)

    with caplog.at_level("WARNING"):
        response = client.get(
            "/openapi/v1/bots/b-1%0AFAKE 404 LINE/probe",
            params={"user_id": CALLER, "owner_id": OWNER},
        )

    assert response.status_code == 404
    handled = [r for r in caplog.records if r.getMessage().startswith("[Public 404]")]

    assert handled, "the refusal never reached the app-level handler"
    assert "\n" not in handled[0].getMessage(), (
        "the exception message carried a caller-supplied newline into app.py's log"
    )


def test_unadjudicable_log_bounds_the_addressed_bot(caplog):
    """The least bounded of the lot: no lookup has succeeded yet.

    When the injector is unwired, ``_level`` refuses before the bot is
    resolved, so ``bot_id`` here is whatever the caller put in the path and is
    constrained by nothing at all.
    """
    client, _ = _surface(level=PermissionLevel.OWNER)
    client.app.state.injector = None

    with caplog.at_level("ERROR"):
        assert client.get(
            "/openapi/v1/bots/b-1%0AFAKE UNWIRED LINE/probe",
            params={"user_id": CALLER, "owner_id": OWNER},
        ).status_code in (404, 500)

    unwired = [r for r in caplog.records if "cannot adjudicate" in r.getMessage()]

    assert unwired, "the unadjudicable request was not logged at all"
    line = unwired[0].getMessage()
    assert "\n" not in line, "a caller-supplied newline reached the log verbatim"


# ── what the seam is not ─────────────────────────────────────────────────────


def test_the_seam_never_touches_the_lock_service():
    """No edit lock in this iteration (``spec.md`` *Decisions* 1).

    Asserted against the module's own imports rather than by observing a
    request, because the claim is that the lock is *absent*, and absence is not
    something one request can demonstrate.
    """
    import inspect

    from agentclaw.community.adapters.http.openapi_v1 import bot_access

    source = inspect.getsource(bot_access)
    code = "\n".join(
        line for line in source.splitlines() if line.startswith(("import ", "from "))
    )

    assert "lock" not in code.lower()


def test_failed_mutation_writes_no_audit_row():
    """A mutation that did not happen must not leave a record saying it did.

    Reaching the teardown does not mean the operation worked:
    ``@envelope_errors`` catches a mapped domain error and *returns* an error
    response instead of raising, so the handler completes normally and this
    dependency resumes exactly as it would after a success. Before the status
    check, that wrote an audit row for a 404 — an incident review could not
    then tell a real action from a failed one.
    """
    audit = _Audit()
    row = Check(PermissionLevel.MEMBER)
    path = "/openapi/v1/bots/{bot_id}/failing"
    authz.AUTHORIZATION[("POST", path)] = row
    try:
        router = APIRouter(route_class=PublicAPIRoute)

        @router.post(path)
        @envelope_errors
        async def failing(bot_id: str, request: Request) -> dict:
            raise CollaboratorBotNotFoundError("the mutation did not happen")

        class _M(Module):
            def configure(self, binder):
                binder.bind(BotRepository, to=InstanceProvider(_Bots()))
                binder.bind(
                    CollaboratorServiceProtocol,
                    to=InstanceProvider(_Collaborators(PermissionLevel.ADMIN)),
                )
                binder.bind(BotCollabLogRepositoryProtocol, to=InstanceProvider(audit))

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[require_principal] = lambda: {"user_id": CALLER}
        mount_public_error_handlers(app)
        attach_injector(app, Injector([_M()]))
        app.add_middleware(PublicApiAccessLogMiddleware)
        client = user_scoped_client(app, CALLER)

        response = client.post(f"/openapi/v1/bots/{BOT}/failing", params={"owner_id": OWNER})

        assert response.status_code == 404
        assert audit.rows == [], "audited a mutation that never happened"
    finally:
        authz.AUTHORIZATION.pop(("POST", path), None)


def test_refusal_log_bounds_caller_supplied_ids(caplog):
    """A refused caller must not be able to forge lines in the refusal trail.

    ``owner_id`` is a query parameter with no upper bound and ``bot_id`` a
    percent-decoded path segment, so both can carry newlines. This branch runs
    only for values the server refused — i.e. values the caller chose — so
    formatting either raw would let the party being refused append convincing
    extra entries to the one record of that refusal.
    """
    client, _ = _surface(level=PermissionLevel.NONE, bar=PermissionLevel.ADMIN)

    with caplog.at_level("WARNING"):
        client.get(URL, params={"user_id": CALLER, "owner_id": "u-9\nFAKE LOG LINE"})

    refusals = [r for r in caplog.records if "is below" in r.getMessage()]

    assert refusals, "the refusal was not logged at all"
    line = refusals[0].getMessage()
    assert "\n" not in line, "a caller-supplied newline reached the log verbatim"
    assert "FAKE LOG LINE" in line, "the value should be escaped, not dropped"
