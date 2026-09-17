"""Creation for an application acting alone: admitted on a user-level
delegation, and granted the bot it creates.

Three properties, each of which fails silently if it breaks:

- **No delegation, no creation.** An application naming a user who never
  delegated to it is refused with the same 404 a missing bot grant gets, and
  the creation flow never runs — so guessing a ``user_id`` buys nothing.
- **A delegated application is granted the bot it creates**, when the
  creation starts, as an ordinary bot grant. That grant is what admits it to
  the bot-scoped poll that completes a pending creation.
- **A human's creation is unchanged**: no grant is written for a person.

The creation flows themselves are stubbed at the seam the routers call —
``create_bot_with_authorization``, ``submit_bot_creation_with_manifest``, the
local workflow service — because what is under test is admission and the
grant, not Passport.
"""

from __future__ import annotations

import importlib
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
    user_router,
)
from agentclaw.community.adapters.http.openapi_v1.bots import router as bots_router
from agentclaw.community.adapters.http.openapi_v1.bots.create_with_manifest import (
    router as create_with_manifest_router,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.local import router as local_router
from agentclaw.community.api.bot_app_grant_service import BotAppGrantServiceProtocol
from agentclaw.community.api.bot_config_manifest_apply_service import (
    BotConfigManifestApplyServiceProtocol,
)
from agentclaw.community.api.bot_quota_service import BotQuotaServiceProtocol
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.local_bot_workflow_service import (
    LocalBotWorkflowServiceProtocol,
)
from agentclaw.community.api.skill_set_service_factory import (
    SkillSetServiceFactoryProtocol,
)
from agentclaw.community.api.user_app_grant_service import (
    UserAppGrantServiceProtocol,
)
from agentclaw.community.core.bot_app_grant.errors import GrantNotFoundError
from agentclaw.community.core.bot_app_grant.models import (
    BotAppGrantRecord,
    UserAppGrantRecord,
)
from agentclaw.community.core.bot_inventory.adapters.noop_business_space import (
    NoopBusinessSpaceContext,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
)
from agentclaw.community.core.bot_management.bot_quota import (
    BotQuotaScope,
    BotQuotaSnapshot,
)
from agentclaw.community.core.bot_management.create_flow import (
    AuthPending,
    AuthStatusResult,
    ManifestCreationSubmitted,
)
from agentclaw.community.core.bot_management.manifest_seam import (
    ManifestCreationSeam,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.gateway_principal import (
    AppPrincipal,
    GatewayApp,
    GatewayUser,
    UserPrincipal,
    VerifiedCaller,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.spaces.models import SpaceType
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)

APP_ID = 42
APP_NAME = "partner"
USER = "u-1"
NEW_BOT = "20260917_newbot01"
LOCAL_BOT = "local-bot-1"

_bots_module = importlib.import_module(
    "agentclaw.community.adapters.http.openapi_v1.bots.router"
)
_manifest_module = importlib.import_module(
    "agentclaw.community.adapters.http.openapi_v1.bots.create_with_manifest"
)

_CREATE_BODY = {
    "bot_name": "research-assistant",
    "bot_desc": "Summarizes weekly industry news.",
    "engine": "openclaw",
    "cluster_name": "ACRA",
    "bot_type": "personal",
}
_MANIFEST_BODY = {
    **_CREATE_BODY,
    "config_manifest": 'schema_version: 1\nscript:\n  body: "echo provisioned"\n',
}
_LOCAL_BODY = {"bot_name": "Local", "machine_id": "m-1", "engine": "openclaw"}
_POLL_BODY = {**_CREATE_BODY}


def _caller(*, with_user: bool) -> VerifiedCaller:
    principals: list = []
    if with_user:
        principals.append(UserPrincipal(subject=GatewayUser(id=USER, username=USER)))
    principals.append(
        AppPrincipal(
            tenant="teamclaw",
            app=GatewayApp(
                app_id=APP_ID,
                app_name=APP_NAME,
                owners="platform-team",
                tenant="teamclaw",
            ),
        )
    )
    return VerifiedCaller(principals=tuple(principals))


class _BotGrants:
    """An in-memory bot grant service: what the creation writes, and what the
    bot-scoped poll then reads."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str, int], BotAppGrantRecord] = {}

    def grant_for_creation(self, *, bot_id, user_id, owner_id, app_id, app_name):
        key = (bot_id, owner_id, user_id, app_id)
        self.rows.setdefault(
            key,
            BotAppGrantRecord(
                id=len(self.rows) + 1,
                app_id=app_id,
                app_name=app_name,
                bot_id=bot_id,
                user_id=user_id,
                owner_id=owner_id,
                avernet_tenant="teamclaw",
                env="test",
                gmt_create=datetime(2026, 9, 17),
            ),
        )
        return self.rows[key]

    def find(self, *, bot_id, owner_id, user_id, app_id):
        return self.rows.get((bot_id, owner_id, user_id, app_id))

    def list_for_app(self, *, app_id, user_id):
        return [r for r in self.rows.values() if r.app_id == app_id and r.user_id == user_id]

    def revoke(self, *, bot_id, user_id, owner_id, app_id):
        if self.rows.pop((bot_id, owner_id, user_id, app_id), None) is None:
            raise GrantNotFoundError("nothing live")


class _UserGrants:
    """The user-level delegation: which users have delegated to ``APP_ID``."""

    def __init__(self, *delegating_users: str) -> None:
        self.delegating_users = set(delegating_users)

    def find(self, *, user_id, app_id):
        if app_id != APP_ID or user_id not in self.delegating_users:
            return None
        return UserAppGrantRecord(
            id=1,
            app_id=app_id,
            app_name=APP_NAME,
            user_id=user_id,
            avernet_tenant="teamclaw",
            env="test",
            gmt_create=datetime(2026, 9, 17),
        )


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


class _LocalWorkflow:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls = 0

    def start_create(self, **kwargs):
        self.calls += 1
        return dict(self.result)


@pytest.fixture
def bot_grants():
    return _BotGrants()


@pytest.fixture
def flows(monkeypatch):
    """The creation seams, stubbed and counted."""
    calls = {"create": 0, "submit": 0}

    def _pending(**kwargs):
        calls["create"] += 1
        return AuthPending(
            bot_id=kwargs["bot_id"], iframe_url="https://consent.example", redirect_url=""
        )

    def _submitted(**kwargs):
        calls["submit"] += 1
        return ManifestCreationSubmitted(
            bot_id=kwargs["bot_id"],
            iframe_url="https://consent.example",
            redirect_url="",
            spec=kwargs["spec"],
            context=kwargs["context"],
        )

    monkeypatch.setattr(_bots_module, "create_bot_with_authorization", _pending)
    monkeypatch.setattr(_bots_module, "generate_bot_id", lambda owner, repo: NEW_BOT)
    monkeypatch.setattr(
        _bots_module,
        "complete_bot_authorization",
        lambda **kwargs: AuthStatusResult(status="PENDING"),
    )
    monkeypatch.setattr(_manifest_module, "submit_bot_creation_with_manifest", _submitted)
    monkeypatch.setattr(_manifest_module, "generate_bot_id", lambda owner, repo: NEW_BOT)
    return calls


@pytest.fixture
def make_client(bot_grants):
    def _build(*delegating_users: str, with_user: bool = False, local_result=None):
        local = _LocalWorkflow(
            local_result
            or {"need_authorization": True, "bot_id": LOCAL_BOT, "iframe_url": "https://c"}
        )

        class _M(Module):
            def configure(self, binder):
                binder.bind(BotAppGrantServiceProtocol, to=bot_grants)
                binder.bind(UserAppGrantServiceProtocol, to=_UserGrants(*delegating_users))
                binder.bind(BotServiceProtocol, to=MagicMock())
                binder.bind(BotRepository, to=MagicMock())
                binder.bind(PassportPlugin, to=MagicMock())
                binder.bind(AuthRelationshipPlugin, to=MagicMock())
                binder.bind(SkillSetServiceFactoryProtocol, to=MagicMock())
                binder.bind(ManifestCreationSeam, to=MagicMock())
                binder.bind(BotConfigManifestApplyServiceProtocol, to=MagicMock())
                binder.bind(BotQuotaServiceProtocol, to=_Quota())
                binder.bind(BusinessSpaceContextProtocol, to=NoopBusinessSpaceContext())
                binder.bind(LocalBotWorkflowServiceProtocol, to=local)

        app = FastAPI()
        # Literals before the `{bot_id}` wildcard, as ``build_public_router``
        # mounts them.
        app.include_router(create_with_manifest_router)
        app.include_router(user_router)
        app.include_router(local_router)
        app.include_router(bots_router)
        app.dependency_overrides[require_principal] = lambda: _caller(with_user=with_user)
        attach_injector(app, Injector([_M()]))
        mount_public_error_handlers(app)
        client = user_scoped_client(app, USER)
        client.local = local  # type: ignore[attr-defined]
        return client

    return _build


# ── the table ────────────────────────────────────────────────────────────────


def test_the_creations_are_user_delegated_and_their_polls_grant_checked():
    for key in (
        ("POST", "/openapi/v1/bots"),
        ("POST", "/openapi/v1/bots/with-manifest"),
        ("POST", "/openapi/v1/bots/local"),
    ):
        assert ADMISSION[key] is AdmissionMode.USER_DELEGATED, key
    for key in (
        ("POST", "/openapi/v1/bots/{bot_id}/auth-status"),
        ("GET", "/openapi/v1/bots/{bot_id}/with-manifest/status"),
        ("GET", "/openapi/v1/bots/{bot_id}/local/auth-status"),
    ):
        assert ADMISSION[key] is AdmissionMode.GRANT_CHECKED_OWN_BOT, key


def test_the_user_level_consent_refuses_a_machine_caller():
    """An application must not be able to delegate to itself."""
    for key in (
        ("POST", "/openapi/v1/bots/authorized-apps"),
        ("GET", "/openapi/v1/bots/authorized-apps"),
        ("DELETE", "/openapi/v1/bots/authorized-apps/{app_id}"),
    ):
        assert ADMISSION[key] is AdmissionMode.REFUSED, key


# ── POST /openapi/v1/bots ────────────────────────────────────────────────────


def test_an_undelegated_app_cannot_create_a_bot(make_client, flows, bot_grants):
    """Refused before the flow runs, with the 404 a missing grant gets."""
    client = make_client()  # nobody delegated

    response = client.post("/openapi/v1/bots", json=_CREATE_BODY)

    assert response.status_code == 404, response.json()
    assert flows["create"] == 0
    assert bot_grants.rows == {}


def test_a_delegated_app_creates_a_bot_and_is_granted_it(
    make_client, flows, bot_grants
):
    client = make_client(USER)

    response = client.post("/openapi/v1/bots", json=_CREATE_BODY)

    assert response.status_code == 202, response.json()
    assert response.json()["data"]["bot_id"] == NEW_BOT
    assert flows["create"] == 1
    record = bot_grants.find(bot_id=NEW_BOT, owner_id=USER, user_id=USER, app_id=APP_ID)
    assert record is not None
    assert record.app_name == APP_NAME


def test_the_creation_grant_admits_the_app_to_the_pending_poll(
    make_client, flows, bot_grants
):
    """The point of writing the grant at the start: the poll is bot-scoped."""
    client = make_client(USER)
    client.post("/openapi/v1/bots", json=_CREATE_BODY)

    poll = client.post(f"/openapi/v1/bots/{NEW_BOT}/auth-status", json=_POLL_BODY)

    assert poll.status_code == 200, poll.json()
    assert poll.json()["data"]["status"] == "PENDING"


def test_a_bot_the_app_did_not_create_is_not_pollable(make_client, flows):
    """A user-level delegation reaches no bot on its own."""
    client = make_client(USER)

    poll = client.post("/openapi/v1/bots/someone-elses/auth-status", json=_POLL_BODY)

    assert poll.status_code == 404, poll.json()


def test_a_creation_that_fails_to_start_withdraws_the_grant(
    make_client, flows, bot_grants, monkeypatch
):
    """The grant precedes the flow, so a flow that refuses must take it back."""

    def _refused(**kwargs):
        raise BotNotFoundError("the space has no capacity")

    monkeypatch.setattr(_bots_module, "create_bot_with_authorization", _refused)
    client = make_client(USER)

    response = client.post("/openapi/v1/bots", json=_CREATE_BODY)

    assert response.status_code == 404, response.json()
    assert bot_grants.rows == {}


def test_a_human_creation_writes_no_grant(make_client, flows, bot_grants):
    """Nothing changes for a person, even with an App riding along."""
    client = make_client(with_user=True)

    response = client.post("/openapi/v1/bots", json=_CREATE_BODY)

    assert response.status_code == 202, response.json()
    assert flows["create"] == 1
    assert bot_grants.rows == {}


# ── POST /openapi/v1/bots/with-manifest ──────────────────────────────────────


def test_an_undelegated_app_cannot_submit_a_manifest_creation(
    make_client, flows, bot_grants
):
    client = make_client()

    response = client.post("/openapi/v1/bots/with-manifest", json=_MANIFEST_BODY)

    assert response.status_code == 404, response.json()
    assert flows["submit"] == 0
    assert bot_grants.rows == {}


def test_a_delegated_app_submits_a_manifest_creation_and_is_granted_the_bot(
    make_client, flows, bot_grants
):
    client = make_client(USER)

    response = client.post("/openapi/v1/bots/with-manifest", json=_MANIFEST_BODY)

    assert response.status_code == 202, response.json()
    assert response.json()["data"]["bot_id"] == NEW_BOT
    assert flows["submit"] == 1
    assert bot_grants.find(bot_id=NEW_BOT, owner_id=USER, user_id=USER, app_id=APP_ID)


# ── POST /openapi/v1/bots/local ──────────────────────────────────────────────


def test_an_undelegated_app_cannot_create_a_local_bot(make_client, bot_grants):
    client = make_client()

    response = client.post("/openapi/v1/bots/local", json=_LOCAL_BODY)

    assert response.status_code == 404, response.json()
    assert client.local.calls == 0
    assert bot_grants.rows == {}


def test_a_delegated_app_creating_a_local_bot_is_granted_it_once_the_id_is_known(
    make_client, bot_grants
):
    """The id is the desktop service's to allocate, so the grant follows it —
    on the pending shape too, since the auth-status poll is bot-scoped."""
    client = make_client(USER)

    response = client.post("/openapi/v1/bots/local", json=_LOCAL_BODY)

    assert response.status_code == 202, response.json()
    assert client.local.calls == 1
    assert bot_grants.find(bot_id=LOCAL_BOT, owner_id=USER, user_id=USER, app_id=APP_ID)


# ── the user-level delegation as proof of relationship ───────────────────────


def test_a_user_level_delegation_satisfies_a_user_gated_read(make_client):
    """An app onboarded to create bots can read the ceiling before it holds
    any bot grant — the read it needs first."""
    assert make_client().get("/openapi/v1/bots/ceiling").status_code == 404
    assert make_client(USER).get("/openapi/v1/bots/ceiling").status_code == 200
