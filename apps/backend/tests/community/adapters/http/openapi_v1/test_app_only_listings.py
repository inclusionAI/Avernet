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
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.admission import (
    ADMISSION,
    AdmissionMode,
)
from agentclaw.community.adapters.http.openapi_v1.authorized_apps import (
    app_view_router,
)
from agentclaw.community.adapters.http.openapi_v1.bots import router as bots_router
from agentclaw.community.adapters.http.openapi_v1.local import router as local_router
from agentclaw.community.adapters.http.openapi_v1.routines.owner_router import (
    router as routines_owner_router,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.spaces import router as spaces_router
from agentclaw.community.adapters.http.openapi_v1.spaces.skill_routes import (
    router as space_skill_router,
)
from agentclaw.community.adapters.http.openapi_v1.work_orders import (
    router as work_orders_router,
)
from agentclaw.community.adapters.http.openapi_v1.deprecated import LEGACY_ROUTES
from agentclaw.community.api.bot_app_grant_service import BotAppGrantServiceProtocol
from agentclaw.community.api.bot_inventory_service import BotInventoryServiceProtocol
from agentclaw.community.api.cron_relay_service import (
    CronRelayServiceProtocol,
)
from agentclaw.community.api.local_bot_workflow_service import (
    LocalBotWorkflowServiceProtocol,
)
from agentclaw.community.api.bot_quota_service import BotQuotaServiceProtocol
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.market_favorite_service import (
    MarketFavoriteServiceProtocol,
)
from agentclaw.community.api.space_service import (
    SpaceMemberServiceProtocol,
    SpaceServiceProtocol,
)
from agentclaw.community.api.space_skill_query_service import (
    SpaceSkillQueryServiceProtocol,
)
from agentclaw.community.api.space_skill_application_service import (
    SpaceSkillApplicationServiceProtocol,
)
from agentclaw.community.api.space_skill_version_query_service import (
    SpaceSkillVersionQueryServiceProtocol,
)
from agentclaw.community.api.space_skill_grant_service import (
    SpaceSkillGrantServiceProtocol,
)
from agentclaw.community.api.work_order_service import (
    WorkOrderNotificationServiceProtocol,
    WorkOrderServiceProtocol,
)
from agentclaw.community.core.bot_app_grant.models import BotAppGrantRecord
from agentclaw.community.core.bot_inventory.adapters.noop_business_space import (
    NoopBusinessSpaceContext,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
)
from agentclaw.community.core.bot_inventory.types import (
    BotInventoryItem,
    BotInventoryKind,
    DeployMode,
    DisplayState,
    ServiceEditLockState,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.bot_management.bot_quota import (
    BotQuotaScope,
    BotQuotaSnapshot,
)
from agentclaw.community.core.spaces.models import SpaceType
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
#: A legacy ``default``-style id: the delegating user owns one, and so does
#: someone else. ``bot_id`` alone cannot tell the two apart.
LEGACY_ID = "default"


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

    def find(self, *, bot_id: str, owner_id: str, user_id: str, app_id: int):
        if bot_id not in self.bot_ids or user_id != USER or app_id != APP_ID:
            return None
        # The grant on SHARED names another owner; every other bot is the
        # delegating user's own. A request must address the right one.
        granted_owner = "someone-else" if bot_id == SHARED else USER
        if owner_id != granted_owner:
            return None
        return _record(bot_id, owner_id=granted_owner)

    def list_for_app(self, *, app_id: int, user_id: str):
        if app_id != APP_ID or user_id != USER:
            return []
        return [
            _record(b, owner_id="someone-else" if b == SHARED else USER)
            for b in self.bot_ids
        ]


class _UnexpectedService:
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        raise AssertionError(f"business service must not be called: {name}")


class _Bots:
    """Two bots owned by ``USER``. ``SHARED`` is not among them, by design."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def list_bots_by_conditions(self, **kwargs):
        self.calls.append(kwargs)
        owned = [
            {"bot_id": GRANTED, "bot_name": "granted", "owner_id": USER},
            {"bot_id": UNGRANTED, "bot_name": "ungranted", "owner_id": USER},
            # The user's own legacy bot, never granted to anyone.
            {"bot_id": LEGACY_ID, "bot_name": "the user's own", "owner_id": USER},
        ]
        allowed = kwargs.get("bot_ids")
        if allowed is not None:
            owned = [b for b in owned if b["bot_id"] in allowed]
        return {"total": len(owned), "items": owned}

    def get_bot(self, bot_id: str, user_id: str):
        """Owner-scoped, as production is — and it resolves the *user's* bot."""
        if user_id != USER:
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        return {
            "id": 1,
            "bot_id": bot_id,
            "owner_id": USER,
            "bot_name": "the user's own",
        }

    def get_bots_ceiling_for_owner(self, owner_id: str) -> int:
        return 5

    def check_bot_name_exists(self, name: str) -> bool:
        return False


class _Quota:
    def inspect(self, *, owner_id: str, space_id: int | None) -> BotQuotaSnapshot:
        return BotQuotaSnapshot(
            scope=BotQuotaScope(
                owner_id=owner_id,
                space_id=space_id,
                space_name="Personal",
                space_type=SpaceType.PERSONAL,
            ),
            ceiling=5,
            used=0,
        )


@pytest.fixture
def bots():
    return _Bots()


@pytest.fixture
def make_client(bots):
    def _build(
        *grant_ids: str,
        with_user: bool = False,
        inventory_service=None,
        local_service=None,
        space_context=None,
    ):
        class _M(Module):
            def configure(self, binder):
                binder.bind(BotServiceProtocol, to=bots)
                binder.bind(BotQuotaServiceProtocol, to=_Quota())
                binder.bind(BotAppGrantServiceProtocol, to=_Grants(*grant_ids))
                unexpected = _UnexpectedService()
                binder.bind(CronRelayServiceProtocol, to=unexpected)
                binder.bind(SpaceServiceProtocol, to=unexpected)
                binder.bind(SpaceMemberServiceProtocol, to=unexpected)
                binder.bind(SpaceSkillQueryServiceProtocol, to=unexpected)
                binder.bind(SpaceSkillApplicationServiceProtocol, to=unexpected)
                binder.bind(SpaceSkillVersionQueryServiceProtocol, to=unexpected)
                binder.bind(SpaceSkillGrantServiceProtocol, to=unexpected)
                binder.bind(MarketFavoriteServiceProtocol, to=unexpected)
                binder.bind(WorkOrderServiceProtocol, to=unexpected)
                binder.bind(WorkOrderNotificationServiceProtocol, to=unexpected)
                binder.bind(
                    BotInventoryServiceProtocol,
                    to=inventory_service or unexpected,
                )
                binder.bind(
                    LocalBotWorkflowServiceProtocol,
                    to=local_service or unexpected,
                )
                binder.bind(
                    BusinessSpaceContextProtocol,
                    to=space_context or unexpected,
                )

        app = FastAPI()
        # The literal before the wildcard, exactly as ``build_public_router``
        # mounts them: ``/openapi/v1/bots/{bot_id}`` would otherwise claim
        # ``/openapi/v1/bots/authorized`` as "the bot named authorized".
        app.include_router(app_view_router)
        # Same rule for the owner-routines literal: ``routines`` is not a bot id.
        app.include_router(routines_owner_router)
        app.include_router(local_router)
        app.include_router(bots_router)
        app.include_router(spaces_router)
        app.include_router(space_skill_router)
        app.include_router(work_orders_router)
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
    # The bots listing now resolves each row's owner-view space (Noop models
    # the personal space with zero service calls), so it needs a serving
    # space context rather than the unexpected-service guard.
    client = make_client(GRANTED, space_context=NoopBusinessSpaceContext())

    listed = _data(client.get("/openapi/v1/bots"))

    assert [item["bot_id"] for item in listed["items"]] == [GRANTED]


def test_the_count_describes_the_narrowed_set(make_client):
    """Otherwise the gap between count and items leaks what was withheld.

    A caller could subtract and learn exactly how many of the user's bots it was
    not granted — a number nobody agreed to share.
    """
    client = make_client(GRANTED, space_context=NoopBusinessSpaceContext())

    assert _data(client.get("/openapi/v1/bots"))["total"] == 1


def test_the_narrowing_happens_before_pagination(make_client, bots):
    """Filtering the page after the fact would return short pages.

    Asserted through the call the service actually receives, because the symptom
    — a page of 20 that returns 3 — is the kind of thing that looks like an
    off-by-one rather than a scoping failure.
    """
    client = make_client(GRANTED, space_context=NoopBusinessSpaceContext())

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
    client = make_client(
        GRANTED, with_user=True, space_context=NoopBusinessSpaceContext()
    )

    listed = _data(client.get("/openapi/v1/bots"))

    assert [item["bot_id"] for item in listed["items"]] == [
        GRANTED,
        UNGRANTED,
        LEGACY_ID,
    ], "every bot they own, including the legacy one no application was granted"
    assert bots.calls[0]["bot_ids"] is None, "unrestricted, not restricted-to-all"


def test_workshop_inventory_passes_owned_grants_before_pagination(make_client):
    inventory = MagicMock()
    inventory.list_items.return_value = (
        [
            BotInventoryItem(
                bot_id=GRANTED,
                bot_name="granted",
                bot_desc="",
                engine="openclaw",
                bot_type="personal",
                kind=BotInventoryKind.PERSONAL_CLOUD,
                deploy_mode=DeployMode.CLOUD,
                display_state=DisplayState.RUNNING,
                status="ACTIVE",
                owner_entity_id=USER,
                space=None,
                card_id=GRANTED,
            )
        ],
        1,
    )
    client = make_client(
        GRANTED,
        inventory_service=inventory,
        space_context=NoopBusinessSpaceContext(),
    )

    listed = _data(
        client.get("/openapi/v1/bots/all", params={"page": 2, "page_size": 7})
    )

    assert [item["bot_id"] for item in listed["items"]] == [GRANTED]
    assert listed["total"] == 1
    assert inventory.list_items.call_args.kwargs["bot_ids"] == [GRANTED]
    assert inventory.list_items.call_args.kwargs["page"] == 2
    assert inventory.list_items.call_args.kwargs["page_size"] == 7


def test_workshop_inventory_does_not_widen_from_a_shared_bot_grant(make_client):
    inventory = MagicMock()
    client = make_client(
        SHARED,
        inventory_service=inventory,
        space_context=NoopBusinessSpaceContext(),
    )

    listed = _data(client.get("/openapi/v1/bots/all"))

    assert listed["items"] == [] and listed["total"] == 0
    inventory.list_items.assert_not_called()


def test_workshop_inventory_serializes_batched_edit_lock(make_client):
    inventory = MagicMock()
    inventory.list_items.return_value = (
        [
            BotInventoryItem(
                bot_id=GRANTED,
                bot_name="service",
                bot_desc="",
                engine="openclaw",
                bot_type="service",
                kind=BotInventoryKind.SERVICE,
                deploy_mode=DeployMode.CLOUD,
                display_state=DisplayState.SERVICE_DRAFT,
                status="draft",
                owner_entity_id=USER,
                space=None,
                card_id=f"service:{GRANTED}:1",
                edit_lock=ServiceEditLockState(
                    locked=True,
                    holder_user_id="editor-1",
                    holder_name="Editor One",
                    has_collaborators=True,
                    is_owner_holder=False,
                    need_lock=True,
                ),
            )
        ],
        1,
    )
    client = make_client(
        GRANTED,
        inventory_service=inventory,
        space_context=NoopBusinessSpaceContext(),
    )

    item = _data(client.get("/openapi/v1/bots/all"))["items"][0]

    assert item["edit_lock"] == {
        "locked": True,
        "acquired": None,
        "holder_user_id": "editor-1",
        "holder_name": "Editor One",
        "has_collaborators": True,
        "is_owner_holder": False,
        "need_lock": True,
    }


def test_local_listing_passes_owned_grants_before_pagination(make_client):
    local = MagicMock()
    local.list_bots.return_value = (
        1,
        [
            {
                "bot_id": GRANTED,
                "bot_name": "granted",
                "bot_desc": "",
                "active_engine": "openclaw",
                "status": "ACTIVE",
                "owner_id": USER,
            }
        ],
    )
    client = make_client(GRANTED, local_service=local)

    listed = _data(
        client.get("/openapi/v1/bots/local", params={"page": 3, "page_size": 4})
    )

    assert [item["bot_id"] for item in listed["items"]] == [GRANTED]
    assert listed["total"] == 1
    assert local.list_bots.call_args.kwargs["bot_ids"] == [GRANTED]
    assert local.list_bots.call_args.kwargs["page"] == 3
    assert local.list_bots.call_args.kwargs["page_size"] == 4


def test_local_device_reads_are_user_gated_by_any_live_delegation(make_client):
    local = MagicMock()
    local.list_devices.return_value = (
        1,
        [{"machine_id": "m1", "machine_name": "Mac", "status": "ACTIVE"}],
    )
    local.list_device_files.return_value = {"name": "Desktop", "children": []}
    client = make_client(SHARED, local_service=local)

    devices = _data(client.get("/openapi/v1/bots/local/devices"))
    files = _data(client.get("/openapi/v1/bots/local/devices/m1/files"))

    assert devices["total"] == 1
    assert devices["items"][0]["machine_id"] == "m1"
    assert files == {"name": "Desktop", "children": []}


def test_the_application_view_shows_a_bot_the_user_does_not_own(make_client):
    """The reason this operation has to admit a machine caller at all.

    A bot delegated by a collaborator belongs to someone else, so it appears in
    no listing of the delegating user's bots — the one above is owner-scoped
    underneath. Without this view it would be reachable but undiscoverable.
    """
    client = make_client(GRANTED, SHARED)

    listed = _data(client.get("/openapi/v1/bots/authorized"))

    assert {item["bot_id"] for item in listed["items"]} == {GRANTED, SHARED}


# ── bot_id is not unique across owners ───────────────────────────────────────


class _CrossOwnerGrants:
    """One grant, on **another owner's** bot that happens to share an id.

    The delegating user also owns a bot called ``default``. Every column the
    grant's unique key holds — app, bot_id, delegating user — is identical for
    the two bots, so the lookup alone cannot tell them apart.
    """

    def find(self, *, bot_id: str, owner_id: str, user_id: str, app_id: int):
        if (bot_id, user_id, app_id) != (LEGACY_ID, USER, APP_ID):
            return None
        # The one grant is on ``someone-else``'s bot. An owner-scoped request
        # addresses the delegating user's own, so it must not match.
        if owner_id != "someone-else":
            return None
        return _record(LEGACY_ID, owner_id="someone-else")

    def list_for_app(self, *, app_id: int, user_id: str):
        record = self.find(
            bot_id=LEGACY_ID,
            owner_id="someone-else",
            user_id=user_id,
            app_id=app_id,
        )
        return [record] if record else []


@pytest.fixture
def cross_owner_client(bots):
    def _build():
        class _M(Module):
            def configure(self, binder):
                binder.bind(BotServiceProtocol, to=bots)
                binder.bind(BotAppGrantServiceProtocol, to=_CrossOwnerGrants())
                binder.bind(
                    BusinessSpaceContextProtocol,
                    to=NoopBusinessSpaceContext(),
                )

        app = FastAPI()
        app.include_router(app_view_router)
        app.include_router(local_router)
        app.include_router(bots_router)
        app.dependency_overrides[require_principal] = lambda: _caller(with_user=False)
        attach_injector(app, Injector([_M()]))
        mount_public_error_handlers(app)
        return user_scoped_client(app, USER)

    return _build()


def test_a_grant_on_another_owners_samenamed_bot_does_not_admit_the_users_own(
    cross_owner_client,
):
    """The failure a bare ``bot_id`` invites, and it is silent without this.

    ``ac_bots`` has no unique key on ``bot_id`` — the legacy ``default``
    convention gave many owners one — so a grant naming *someone else's*
    ``default`` matches a request naming the delegating user's own on every
    column the grant is keyed by. An owner-scoped operation then resolves the
    user's own bot and serves it: a ``200`` carrying a bot nobody granted.

    Refused rather than reconciled. The caller is entitled to neither bot here:
    the granted one is unreachable on an owner-scoped operation — it is
    unreachable there for the delegating user too — and the user's own was never
    granted.
    """
    response = cross_owner_client.get(f"/openapi/v1/bots/{LEGACY_ID}")

    assert response.status_code == 404, response.json()


def test_an_injected_owner_id_does_not_authorize_an_owner_scoped_route(
    cross_owner_client,
):
    """A query parameter the route never declares must not steer the check.

    ``request.query_params`` is the raw parsed query string, not the parameters
    a route publishes, so an application can append ``owner_id`` to any of the
    wholly owner-scoped operations — none of which document it. If the grant
    check read it there, it would validate against the bot the application
    *does* hold a grant on while the handler, which reads only ``user_id``,
    resolved and acted on the delegating user's own same-named bot. A grant on
    anyone's ``default`` would become access to the delegator's ``default``.

    The check and the resolution must never be able to mean different bots.
    Only the operations that publish ``owner_id`` and adjudicate it themselves
    may take it from the wire.
    """
    response = cross_owner_client.get(
        f"/openapi/v1/bots/{LEGACY_ID}", params={"owner_id": "someone-else"}
    )

    assert response.status_code == 404, response.json()


def test_an_owner_scoped_listing_does_not_widen_through_a_shared_bot_id(
    cross_owner_client,
):
    """The same collision, reached through the listing's id filter.

    The narrowing set holds bare ids, so passing another owner's ``default``
    into an owner-scoped query matches the delegating user's own.
    """
    listed = _data(cross_owner_client.get("/openapi/v1/bots"))

    assert listed["items"] == [] and listed["total"] == 0


# ── Mode C: an answer about the user's account ───────────────────────────────


def test_the_ceiling_is_readable_with_a_delegation(make_client):
    client = make_client(GRANTED, space_context=NoopBusinessSpaceContext())

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


# ── derived from the table: every filtered/gated operation is covered ────────
#
# The hand-written tests above pin the *behavior* of the three operations that
# exist today. What they cannot do is notice a fourth: GRANT_FILTERED and
# USER_GATED have no dependency a structural test could look for — the
# narrowing is the handler's own work, through `granted_bot_ids` — so an
# operation added tomorrow with a correct table entry and no narrowing in its
# handler would fail nothing. These two tests parametrize over the table
# itself: a new entry appears here automatically, and until someone writes it
# an ungranted-app case in `_UNGRANTED_APP_CASES`, the test fails loudly
# instead of silently covering nothing. That is the same ratchet
# `test_self_checked_routes_refuse.py` uses for its BODIES map.

#: How to call each filtered/gated operation as an application granted
#: *nothing*, and what the empty answer must look like. `assert_starved` gets
#: the response; it must assert the app learned nothing.
_UNGRANTED_APP_CASES = {
    ("GET", "/openapi/v1/bots/skills/repository"): {
        "request": lambda client: client.get("/openapi/v1/bots/skills/repository"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/skills/repository/tree"): {
        "request": lambda client: client.get("/openapi/v1/bots/skills/repository/tree"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/skills/repository/{skill_id}"): {
        "request": lambda client: client.get("/openapi/v1/bots/skills/repository/1"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/skills/{skill_id}/readme"): {
        "request": lambda client: client.get("/openapi/v1/bots/skills/1/readme"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/skills/repository/sync"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/skills/repository/sync"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/market/skill-center/sync"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/market/skill-center/sync"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots"): {
        "request": lambda client: client.get("/openapi/v1/bots"),
        "assert_starved": lambda response: (
            _data(response)["items"] == [] and _data(response)["total"] == 0
        ),
    },
    ("GET", "/openapi/v1/bots/authorized"): {
        "request": lambda client: client.get("/openapi/v1/bots/authorized"),
        "assert_starved": lambda response: (
            _data(response)["items"] == [] and _data(response)["total"] == 0
        ),
    },
    ("GET", "/openapi/v1/bots/all"): {
        "request": lambda client: client.get("/openapi/v1/bots/all"),
        "assert_starved": lambda response: (
            _data(response)["items"] == [] and _data(response)["total"] == 0
        ),
    },
    ("GET", "/openapi/v1/bots/local"): {
        "request": lambda client: client.get("/openapi/v1/bots/local"),
        "assert_starved": lambda response: (
            _data(response)["items"] == [] and _data(response)["total"] == 0
        ),
    },
    ("GET", "/openapi/v1/bots/local/devices"): {
        "request": lambda client: client.get("/openapi/v1/bots/local/devices"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/local/devices/{machine_id}/files"): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/local/devices/machine-1/files"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/ceiling"): {
        "request": lambda client: client.get("/openapi/v1/bots/ceiling"),
        # USER_GATED: no delegation, no relationship — masked as not-found, so
        # a stranger app cannot read a person's quota by naming them.
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/routines/all"): {
        "request": lambda client: client.get("/openapi/v1/bots/routines/all"),
        # USER_GATED, the ceiling's exact shape: the aggregate reads the named
        # user's whole routine fleet, so an app with no delegation from them is
        # answered as if the user did not exist. The cron service stays
        # untouched — it is bound to `_UnexpectedService` in the fixture, so a
        # regression that asks it anyway fails here rather than leaking rows.
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/spaces"): {
        "request": lambda client: client.get("/openapi/v1/bots/spaces"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/spaces/{space_id}/members"): {
        "request": lambda client: client.get("/openapi/v1/bots/spaces/1/members"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills"): {
        "request": lambda client: client.get("/openapi/v1/bots/spaces/1/skills"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/consumable"): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/spaces/1/skills/consumable"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}"): {
        "request": lambda client: client.get("/openapi/v1/bots/spaces/1/skills/1"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files"): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/spaces/1/skills/1/draft/files"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    (
        "GET",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files/{path:path}",
    ): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/spaces/1/skills/1/draft/files/SKILL.md"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions"): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/spaces/1/skills/1/versions"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    (
        "GET",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}",
    ): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/spaces/1/skills/1/versions/1"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    (
        "GET",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/files",
    ): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/spaces/1/skills/1/versions/1/files"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    (
        "GET",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/files/{path:path}",
    ): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/spaces/1/skills/1/versions/1/files/SKILL.md"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/grants"): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/spaces/1/skills/1/grants"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/spaces/{space_id}/market-favorites"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/spaces/1/market-favorites",
            json={
                "market_source": "SKILLCENTER",
                "target_type": "SKILL",
                "target_code": "skill-1",
            },
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/spaces/{space_id}/market-favorites/cancel"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/spaces/1/market-favorites/cancel",
            json={
                "market_source": "SKILLCENTER",
                "target_type": "SKILL",
                "target_code": "skill-1",
            },
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/spaces/{space_id}/market-favorites/search"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/spaces/1/market-favorites/search", json={}
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/spaces/{space_id}/market-favorites/status"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/spaces/1/market-favorites/status",
            json={
                "market_source": "SKILLCENTER",
                "target_type": "SKILL",
                "target_codes": ["skill-1"],
            },
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/{bot_id}/editor-requests"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/bot-1/editor-requests",
            params={"owner_id": "owner-1"},
            json={"reason": "joint editing"},
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/work-orders"): {
        "request": lambda client: client.get("/openapi/v1/bots/work-orders"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/work-orders/{work_order_id}"): {
        "request": lambda client: client.get("/openapi/v1/bots/work-orders/1"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/work-orders/events"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/work-orders/events",
            json={
                "event_category": "NOTICE",
                "biz_type": "TEST",
                "biz_id": "1",
                "event_type": "SPACE_JOIN_REVIEWED",
                "recipient_user_ids": ["u-2"],
                "title": "notice",
            },
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/work-orders/{work_order_id}/approval"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/work-orders/1/approval",
            json={"decision": "APPROVED", "review_remark": "approve"},
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/work-order-notifications/unread-count"): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/work-order-notifications/unread-count"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/work-order-notifications/{notification_id}"): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/work-order-notifications/1"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/work-order-notifications/read-all"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/work-order-notifications/read-all"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/work-order-notifications/{notification_id}/read"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/work-order-notifications/1/read"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
}

_FILTERED_OR_GATED = sorted(
    key
    for key, mode in ADMISSION.items()
    if mode in {AdmissionMode.GRANT_FILTERED, AdmissionMode.USER_GATED}
    and key not in LEGACY_ROUTES
)


@pytest.mark.parametrize(
    ("method", "path"),
    _FILTERED_OR_GATED,
    ids=[f"{m} {p}" for m, p in _FILTERED_OR_GATED],
)
def test_every_filtered_or_gated_operation_starves_an_ungranted_app(
    make_client, method, path
):
    """An application granted nothing learns nothing, on every such operation.

    Parametrized over the admission table so a new GRANT_FILTERED or USER_GATED
    entry is covered the day it is written — the failure below is the demand
    for its case, not a bug in this test.
    """
    case = _UNGRANTED_APP_CASES.get((method, path))
    assert case is not None, (
        f"{method} {path} is GRANT_FILTERED or USER_GATED but has no "
        "ungranted-app case in _UNGRANTED_APP_CASES. Add one asserting an "
        "application granted nothing gets an empty answer (filtered) or a "
        "masked 404 (gated) — the mode's promise is only real if it is tested."
    )
    client = make_client()  # no grants at all

    response = case["request"](client)

    assert case["assert_starved"](response), (
        f"{method} {path} answered an application granted nothing with "
        f"{response.status_code}: {response.text[:300]}"
    )


def test_the_derived_set_is_not_empty():
    """If both modes ever empty out, delete the ratchet deliberately."""
    assert _FILTERED_OR_GATED, (
        "no GRANT_FILTERED or USER_GATED operations left — remove these "
        "derived tests along with the modes, not silently"
    )
