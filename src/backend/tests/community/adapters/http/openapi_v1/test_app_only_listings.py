"""Listings and account-level reads, for an application acting alone.

These are the operations that name **no bot**, so there is no grant to check
against one. Each answers a different question about what that means:

- **Mode B** — a listing of bots. Admitted, and narrowed to what was delegated.
- **Mode C** — an answer about the named user's account. Admitted only while the
  application holds some delegation from them.
- **OPEN** — an answer identical for every caller in the tenant. Admitted on
  authentication alone, because there is no user here to gate against.

The narrowing is where a listing goes wrong quietly: too wide and it hands over
bots nobody delegated, and it does so with a ``200``.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.authorized_apps import (
    app_view_router,
)
from agentclaw.community.adapters.http.openapi_v1.bots import router as bots_router
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.bot_app_grant_service import BotAppGrantServiceProtocol
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_app_grant.models import BotAppGrantRecord
from agentclaw.community.core.gateway_principal import (
    AppPrincipal,
    GatewayApp,
    GatewayUser,
    UserPrincipal,
    VerifiedCaller,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)

APP_ID = 42
USER = "u-1"
GRANTED = "b-granted"
UNGRANTED = "b-ungranted"
#: A bot the user does **not** own but collaborates on, delegated to the app.
#: It can never appear in the bots listing — that is owner-scoped — which is why
#: the application's own view has to exist.
SHARED = "b-shared"


def _caller(*, with_user: bool) -> VerifiedCaller:
    principals: list = []
    if with_user:
        principals.append(UserPrincipal(subject=GatewayUser(id=USER, username=USER)))
    principals.append(
        AppPrincipal(
            tenant="teamclaw",
            app=GatewayApp(
                app_id=APP_ID,
                app_name="partner",
                owners="platform-team",
                tenant="teamclaw",
            ),
        )
    )
    return VerifiedCaller(principals=tuple(principals))


def _record(bot_id: str, owner_id: str = USER) -> BotAppGrantRecord:
    return BotAppGrantRecord(
        id=1,
        app_id=APP_ID,
        app_name="partner",
        bot_id=bot_id,
        user_id=USER,
        owner_id=owner_id,
        avernet_tenant="teamclaw",
        env="test",
        gmt_create=datetime(2026, 8, 10),
    )


class _Grants:
    def __init__(self, *bot_ids: str) -> None:
        self.bot_ids = list(bot_ids)

    def find(self, *, bot_id: str, user_id: str, app_id: int):
        if bot_id in self.bot_ids and user_id == USER and app_id == APP_ID:
            return _record(bot_id)
        return None

    def list_for_app(self, *, app_id: int, user_id: str):
        if app_id != APP_ID or user_id != USER:
            return []
        return [
            _record(b, owner_id="someone-else" if b == SHARED else USER)
            for b in self.bot_ids
        ]


class _Bots:
    """Two bots owned by ``USER``. ``SHARED`` is not among them, by design."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def list_bots_by_conditions(self, **kwargs):
        self.calls.append(kwargs)
        owned = [
            {"bot_id": GRANTED, "bot_name": "granted", "owner_id": USER},
            {"bot_id": UNGRANTED, "bot_name": "ungranted", "owner_id": USER},
        ]
        allowed = kwargs.get("bot_ids")
        if allowed is not None:
            owned = [b for b in owned if b["bot_id"] in allowed]
        return {"total": len(owned), "items": owned}

    def get_bots_ceiling_for_owner(self, owner_id: str) -> int:
        return 5

    def check_bot_name_exists(self, name: str) -> bool:
        return False


@pytest.fixture
def bots():
    return _Bots()


@pytest.fixture
def make_client(bots):
    def _build(*grant_ids: str, with_user: bool = False):
        class _M(Module):
            def configure(self, binder):
                binder.bind(BotServiceProtocol, to=bots)
                binder.bind(BotAppGrantServiceProtocol, to=_Grants(*grant_ids))

        app = FastAPI()
        # The literal before the wildcard, exactly as ``build_public_router``
        # mounts them: ``/openapi/v1/bots/{bot_id}`` would otherwise claim
        # ``/openapi/v1/bots/authorized`` as "the bot named authorized".
        app.include_router(app_view_router)
        app.include_router(bots_router)
        app.dependency_overrides[require_principal] = lambda: _caller(
            with_user=with_user
        )
        attach_injector(app, Injector([_M()]))
        mount_public_error_handlers(app)
        return user_scoped_client(app, USER)

    return _build


def _data(response):
    assert response.status_code == 200, response.json()
    return response.json()["data"]


# ── Mode B: the bots listing ─────────────────────────────────────────────────


def test_the_listing_is_narrowed_to_the_delegated_bots(make_client):
    """Two bots owned, one delegated: one returned."""
    client = make_client(GRANTED)

    listed = _data(client.get("/openapi/v1/bots"))

    assert [item["bot_id"] for item in listed["items"]] == [GRANTED]


def test_the_count_describes_the_narrowed_set(make_client):
    """Otherwise the gap between count and items leaks what was withheld.

    A caller could subtract and learn exactly how many of the user's bots it was
    not granted — a number nobody agreed to share.
    """
    client = make_client(GRANTED)

    assert _data(client.get("/openapi/v1/bots"))["total"] == 1


def test_the_narrowing_happens_before_pagination(make_client, bots):
    """Filtering the page after the fact would return short pages.

    Asserted through the call the service actually receives, because the symptom
    — a page of 20 that returns 3 — is the kind of thing that looks like an
    off-by-one rather than a scoping failure.
    """
    client = make_client(GRANTED)

    client.get("/openapi/v1/bots")

    assert bots.calls[0]["bot_ids"] == [GRANTED]


def test_an_application_granted_nothing_gets_an_empty_page(make_client, bots):
    """``200`` with no items, not an error: naming no bot, there is nothing to mask."""
    client = make_client()

    listed = _data(client.get("/openapi/v1/bots"))

    assert listed["items"] == [] and listed["total"] == 0
    assert bots.calls == [], "and the service was not asked for a page to discard"


def test_a_human_caller_sees_everything_they_own(make_client, bots):
    """The narrowing applies to applications, not to people."""
    client = make_client(GRANTED, with_user=True)

    listed = _data(client.get("/openapi/v1/bots"))

    assert [item["bot_id"] for item in listed["items"]] == [GRANTED, UNGRANTED]
    assert bots.calls[0]["bot_ids"] is None, "unrestricted, not restricted-to-all"


def test_the_application_view_shows_a_bot_the_user_does_not_own(make_client):
    """The reason this operation has to admit a machine caller at all.

    A bot delegated by a collaborator belongs to someone else, so it appears in
    no listing of the delegating user's bots — the one above is owner-scoped
    underneath. Without this view it would be reachable but undiscoverable.
    """
    client = make_client(GRANTED, SHARED)

    listed = _data(client.get("/openapi/v1/bots/authorized"))

    assert {item["bot_id"] for item in listed["items"]} == {GRANTED, SHARED}


# ── Mode C: an answer about the user's account ───────────────────────────────


def test_the_ceiling_is_readable_with_a_delegation(make_client):
    client = make_client(GRANTED)

    assert _data(client.get("/openapi/v1/bots/ceiling"))["ceiling"] == 5


def test_the_ceiling_is_refused_without_one(make_client):
    """A stranger application must not read a person's quota by naming them."""
    client = make_client()

    assert client.get("/openapi/v1/bots/ceiling").status_code == 404


# ── OPEN: identical for every caller in the tenant ───────────────────────────


def test_the_name_check_needs_no_delegation(make_client):
    """No user on the wire to gate against, and no new exposure.

    Every authenticated caller in the tenant already gets this exact answer, so
    requiring a delegation would refuse a machine caller a fact it could obtain
    by presenting any credential at all.
    """
    client = make_client()

    response = client.get("/openapi/v1/bots/check-name", params={"name": "anything"})

    assert response.status_code == 200, response.json()
    assert response.json()["data"]["exists"] is False
