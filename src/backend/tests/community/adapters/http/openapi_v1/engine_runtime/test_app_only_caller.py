"""An application acting alone, on the operations that admit collaborators.

The engine-runtime groups are where delegation actually pays off: they take a
second ``owner_id`` naming the addressed bot's owner and adjudicate through the
collaborator gate, so a bot **shared with** the delegating user is reachable
here and nowhere else on the surface.

The load-bearing test in this file is
:func:`test_losing_the_collaboration_ends_the_applications_access`. Everything
else pins a boundary; that one pins the invariant the whole feature rests on:

    An application's reach is exactly its granting user's reach, and never more.

Not a copy taken at consent time — the live thing. Nothing about the delegator's
authority is stored in the grant, so there is nothing to go stale, and the
application loses the bot the moment the person does.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions import (
    router as sessions_router,
)
from agentclaw.community.api.bot_app_grant_service import BotAppGrantServiceProtocol
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.bot_app_grant.models import BotAppGrantRecord
from agentclaw.community.core.gateway_principal import (
    AppPrincipal,
    GatewayApp,
    VerifiedCaller,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)
from tests.community.adapters.http.openapi_v1.engine_runtime.conftest import BOT, OWNER

APP_ID = 42
#: The delegating user: a member-level collaborator on ``BOT``, which ``OWNER``
#: owns. The case the single-column record could not express at all.
COLLAB = "u-collab"


def app_only_caller(app_id: int = APP_ID) -> VerifiedCaller:
    """A verified credential naming an application and **no human at all**."""
    return VerifiedCaller(
        principals=(
            AppPrincipal(
                tenant="teamclaw",
                app=GatewayApp(
                    app_id=app_id,
                    app_name="partner-platform",
                    owners="platform-team",
                    tenant="teamclaw",
                ),
            ),
        )
    )


class FakeGrants:
    """The delegations in force, as the seam reads them.

    Deliberately dumb: it knows which ``(app, bot, user)`` triples are granted
    and nothing about whether that user may still operate the bot. That
    separation is the point — the grant answers "was this delegated", the relay
    answers "may they still", and the invariant lives in the fact that both are
    asked on every request.
    """

    def __init__(self) -> None:
        #: (app_id, bot_id, user_id) → owner of the bot
        self.granted: dict[tuple[int, str, str], str] = {}

    def grant(self, *, app_id: int, bot_id: str, user_id: str, owner_id: str) -> None:
        self.granted[(app_id, bot_id, user_id)] = owner_id

    def find(self, *, bot_id: str, user_id: str, app_id: int):
        owner = self.granted.get((app_id, bot_id, user_id))
        if owner is None:
            return None
        return BotAppGrantRecord(
            id=1,
            app_id=app_id,
            app_name="partner-platform",
            bot_id=bot_id,
            user_id=user_id,
            owner_id=owner,
            avernet_tenant="teamclaw",
            env="test",
            gmt_create=__import__("datetime").datetime(2026, 8, 10),
        )

    def list_for_app(self, *, app_id: int, user_id: str):
        return [
            self.find(bot_id=bot, user_id=user, app_id=app)
            for (app, bot, user) in self.granted
            if app == app_id and user == user_id
        ]


@pytest.fixture
def grants() -> FakeGrants:
    return FakeGrants()


@pytest.fixture
def app_client(relay, grants):
    """A client whose credential names an application and no end user."""

    def _build(caller: Any = None):
        class _M(Module):
            def configure(self, binder):
                binder.bind(EngineRuntimeRelayProtocol, to=relay)
                binder.bind(BotAppGrantServiceProtocol, to=grants)

        app = FastAPI()
        app.include_router(sessions_router)
        app.dependency_overrides[require_principal] = lambda: (
            caller if caller is not None else app_only_caller()
        )
        attach_injector(app, Injector([_M()]))
        # The refusal is raised in a *dependency*, so ``@envelope_errors`` never
        # sees it — the app-level handler is what turns it into the surface's
        # 404. Mounting the production handlers rather than mirroring them is
        # what keeps this test honest about the status a real caller gets.
        mount_public_error_handlers(app)
        # ``user_id`` still travels on the wire — it names the delegating user
        # rather than the caller, which is the whole shape of the change.
        return user_scoped_client(app, COLLAB)

    return _build


def _sessions(client, **params):
    return client.get(f"/openapi/v1/bots/sessions/{BOT}", params=params)


# ── the invariant ────────────────────────────────────────────────────────────


def test_losing_the_collaboration_ends_the_applications_access(
    app_client, relay, grants
):
    """**The test that must not be cut.**

    ``COLLAB`` collaborates on ``OWNER``'s bot and delegates to the application.
    The application reaches the bot *as them*. Remove the collaboration and the
    application is refused on its very next request — **with the grant row still
    in place**, untouched.

    That is the difference between a bound and a snapshot. Nothing about
    ``COLLAB``'s authority was copied into the grant, so there is nothing to
    revoke, nothing to expire, and nothing to go stale in the direction that
    keeps answering "yes". Re-adding the collaboration restores access with no
    re-granting, which is the same fact read forwards.
    """
    grants.grant(app_id=APP_ID, bot_id=BOT, user_id=COLLAB, owner_id=OWNER)
    relay.add_operator(COLLAB)
    client = app_client()

    assert _sessions(client).status_code == 200, "delegated access works"

    relay.operators.clear()  # COLLAB is removed from the bot

    refused = _sessions(client)
    assert refused.status_code == 404, "access ends immediately"
    assert grants.find(bot_id=BOT, user_id=COLLAB, app_id=APP_ID) is not None, (
        "and it ends without the delegation being revoked"
    )

    relay.add_operator(COLLAB)
    assert _sessions(client).status_code == 200, "restored, with no re-granting"


def test_the_application_is_bounded_by_the_delegator_not_the_owner(
    app_client, relay, grants
):
    """A grant from a collaborator does not confer the owner's reach.

    The application acts *as* ``COLLAB``. A bot ``COLLAB`` cannot operate is a
    bot the application cannot operate, however the grant is worded.
    """
    grants.grant(app_id=APP_ID, bot_id="b-other", user_id=COLLAB, owner_id=OWNER)
    client = app_client()

    # Granted, but COLLAB is not an operator of it — and never was.
    assert client.get("/openapi/v1/bots/sessions/b-other").status_code == 404


# ── the grant as an authorization boundary ───────────────────────────────────


def test_no_grant_is_refused(app_client, relay):
    """An application naming a user who delegated it nothing reaches nothing."""
    relay.add_operator(COLLAB)

    assert _sessions(app_client()).status_code == 404


def test_a_grant_from_another_user_does_not_admit_the_call(
    app_client, relay, grants
):
    """The delegation is keyed on who made it, so someone else's is not yours."""
    grants.grant(app_id=APP_ID, bot_id=BOT, user_id="someone-else", owner_id=OWNER)
    relay.add_operator(COLLAB)

    assert _sessions(app_client()).status_code == 404


def test_a_grant_held_by_another_application_does_not_admit_the_call(
    app_client, relay, grants
):
    """An application cannot borrow a delegation made to a different one."""
    grants.grant(app_id=99, bot_id=BOT, user_id=COLLAB, owner_id=OWNER)
    relay.add_operator(COLLAB)

    assert _sessions(app_client()).status_code == 404


def test_a_grant_for_another_bot_does_not_admit_the_call(app_client, relay, grants):
    """Grants are per bot; holding one is not holding them all."""
    grants.grant(app_id=APP_ID, bot_id="b-other", user_id=COLLAB, owner_id=OWNER)
    relay.add_operator(COLLAB)

    assert _sessions(app_client()).status_code == 404


def test_the_refusal_reaches_no_device(app_client, relay, grants):
    """Refused before any forward, not filtered after one.

    A 404 alone would not prove this: the point is that an ungranted
    application never causes a device to be touched at all.
    """
    relay.add_operator(COLLAB)

    _sessions(app_client())

    assert relay.attempts == []


# ── the addressed owner comes from the record, never the request ─────────────


def test_the_addressed_owner_is_taken_from_the_grant(app_client, relay, grants):
    """With ``owner_id`` omitted, it resolves to the grant's owner.

    A human caller omitting it means "my own bot". An application means "the bot
    my grant names" — which is someone else's, and is exactly the case
    delegation exists for.
    """
    grants.grant(app_id=APP_ID, bot_id=BOT, user_id=COLLAB, owner_id=OWNER)
    relay.add_operator(COLLAB)

    assert _sessions(app_client()).status_code == 200
    assert relay.calls[0]["owner_id"] == OWNER


def test_naming_a_different_owner_is_refused(app_client, relay, grants):
    """An application may not re-aim a grant it holds at a bot it does not.

    Refused *before* the resolve. Left to the downstream lookup this would 404
    anyway — but only because two independent refusals happen to line up, and a
    boundary that holds by coincidence is not a boundary.
    """
    grants.grant(app_id=APP_ID, bot_id=BOT, user_id=COLLAB, owner_id=OWNER)
    relay.add_operator(COLLAB)

    refused = _sessions(app_client(), owner_id="someone-else")

    assert refused.status_code == 404
    assert relay.attempts == [], "refused before any forward"


def test_naming_the_grants_own_owner_is_accepted(app_client, relay, grants):
    """Stating the owner explicitly is allowed when it agrees with the record."""
    grants.grant(app_id=APP_ID, bot_id=BOT, user_id=COLLAB, owner_id=OWNER)
    relay.add_operator(COLLAB)

    assert _sessions(app_client(), owner_id=OWNER).status_code == 200


# ── the human path is untouched ──────────────────────────────────────────────


def test_a_human_caller_still_addresses_owners_freely(make_client, relay):
    """No grant exists, and none is consulted: this is a human request.

    The regression this guards against is subtle and large — treating any
    request that *carries* an app as an application request would put every
    ordinary collaborator call through a grant lookup and refuse it.
    """
    relay.add_operator(COLLAB)
    client = make_client(sessions_router, caller=COLLAB)

    response = client.get(
        f"/openapi/v1/bots/sessions/{BOT}", params={"owner_id": OWNER}
    )

    assert response.status_code == 200
