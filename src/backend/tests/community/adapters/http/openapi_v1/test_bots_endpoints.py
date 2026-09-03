"""Endpoint tests for the public ``/openapi/v1/bots`` API (Track B).

A minimal FastAPI app hosts the bots router with the caller principal overridden
and the bot services bound to mocks via the injector — mirroring the internal
router test harness. The real authenticator stays a stub; ``require_principal``
is overridden per test to supply (or withhold) a caller.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

import importlib

from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)
from agentclaw.community.adapters.http.openapi_v1.bots.engine_config import (
    router as engine_config_router,
)
from agentclaw.community.adapters.http.openapi_v1.bots.router import router
from agentclaw.community.adapters.http.openapi_v1.deprecated.auth_status import (
    router as legacy_auth_status_router,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.bot_space_service import BotSpaceServiceProtocol
from agentclaw.community.api.policy_service import PolicyServiceProtocol
from agentclaw.community.api.skill_set_service_factory import (
    SkillSetServiceFactoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotOperationNotAllowedError,
)
from agentclaw.community.core.spaces.errors import (
    SpaceAccessDeniedError,
    SpaceNotFoundError,
)
from agentclaw.community.core.bot_inventory.adapters.noop_business_space import (
    NoopBusinessSpaceContext,
)
from agentclaw.community.core.bot_inventory.errors import (
    BotInventoryPermissionError,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
)
from agentclaw.community.core.bot_inventory.types import BusinessSpaceRef
from agentclaw.community.api.engine_config_service import EngineConfigServiceProtocol
from agentclaw.community.api.bot_startup_script_service import (
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin

# The bots package re-exports ``router`` (the APIRouter), which shadows the
# submodule attribute — so fetch the real module object to patch module globals.
bots_router = importlib.import_module(
    "agentclaw.community.adapters.http.openapi_v1.bots.router"
)

BOT = {
    # ``id`` is ac_bots' primary key, distinct from the logical "b1". The
    # startup-script row keys on bot_id, not on this: uk_bot_id_entity_id_env
    # means a deleted bot_id is never reissued, so it names one bot for good.
    "id": 77,
    "bot_id": "b1",
    "bot_name": "N",
    "bot_desc": "D",
    "active_engine": "teclaw",
    "bot_type": "personal",
    "status": "ACTIVE",
    "owner_id": "u1",
    "entity_id": "u1",
    "entity_type": "staff",
    "device_binding": {"device_id": "dev-9"},
    # Stored template snapshot: returned on the wire verbatim (2026-09-01
    # passthrough decision — owner-scoped faces echo the caller's own input).
    "template_type": "applicationCoding",
    "template_config": {
        "devflow_workflow": "release-notes",
        "token": "echoed-to-owner",
        "bot_template_config": {"ext_config": {"thetaKey": "enc:v1:x"}},
        "runtime": "codefuse",
    },
}


@pytest.fixture
def svc():
    m = MagicMock()
    m.get_bot.return_value = BOT
    m.list_bots_by_conditions.return_value = {"total": 1, "items": [BOT]}
    m.list_bots_by_owner_bot_pairs.return_value = {"total": 1, "items": [BOT]}
    m.check_bot_name_exists.return_value = True
    m.update_bot.return_value = {**BOT, "bot_name": "Renamed"}
    m.restart_bot.return_value = {**BOT, "status": "PENDING"}
    m.delete_bot.return_value = True
    m.check_create_bot_preflight.return_value = None
    m.create_bot.return_value = BOT
    m.get_bots_ceiling_for_owner.return_value = 7
    return m


@pytest.fixture
def bot_repo():
    return MagicMock()


@pytest.fixture
def bot_space():
    service = MagicMock()
    service.change_space.return_value = SimpleNamespace(
        bot={**BOT, "space_id": 42},
        space=SimpleNamespace(
            id=42,
            space_code="spc-42",
            name="Team",
            space_type=SimpleNamespace(value="TEAM"),
        ),
        changed=True,
    )
    return service


@pytest.fixture
def skill_set_factory():
    m = MagicMock()
    m.create.return_value.get_bot_mcp_codes.return_value = []
    return m


@pytest.fixture
def auth_rel():
    return MagicMock()


@pytest.fixture
def engine_config():
    m = AsyncMock()
    m.read_bot_config.return_value = {"k": "v"}
    m.write_bot_config.return_value = None
    return m


@pytest.fixture
def policy():
    m = MagicMock()
    m.get_bots_ceiling.return_value = 7
    return m


@pytest.fixture
def passport():
    m = MagicMock()
    m.query_agent_passport.return_value = {"agent_code": "ac-1"}
    return m


@pytest.fixture
def startup_script():
    m = MagicMock()
    m.get.return_value = None
    m.delete.return_value = True
    # Supported by default; the unsupported cases override this per test.
    m.resolve_support.return_value = ("supported", "")
    return m


class _CountingNoopSpace(NoopBusinessSpaceContext):
    """Noop space context that counts ``bot_space`` resolutions per instance."""

    def __init__(self) -> None:
        super().__init__()
        self.bot_space_calls = 0

    def bot_space(self, **kwargs):
        self.bot_space_calls += 1
        return super().bot_space(**kwargs)


@pytest.fixture
def client(
    svc,
    bot_space,
    policy,
    passport,
    engine_config,
    bot_repo,
    skill_set_factory,
    auth_rel,
    startup_script,
):
    space = _CountingNoopSpace()

    class _M(Module):
        def configure(self, binder):
            binder.bind(BotServiceProtocol, to=svc)
            binder.bind(BotSpaceServiceProtocol, to=bot_space)
            binder.bind(PolicyServiceProtocol, to=policy)
            binder.bind(PassportPlugin, to=passport)
            binder.bind(EngineConfigServiceProtocol, to=engine_config)
            binder.bind(BotRepository, to=bot_repo)
            binder.bind(SkillSetServiceFactoryProtocol, to=skill_set_factory)
            binder.bind(AuthRelationshipPlugin, to=auth_rel)
            binder.bind(BotStartupScriptServiceProtocol, to=startup_script)
            binder.bind(BusinessSpaceContextProtocol, to=space)

    app = FastAPI()
    app.include_router(router)
    # Engine config is bots-component work served at an engine-component
    # address, so it hangs off its own router and has to be mounted alongside.
    app.include_router(engine_config_router)
    # The retiring GET spelling of the auth-status poll, mounted so the GET
    # tests keep exercising the frozen contract beside its POST replacement.
    app.include_router(legacy_auth_status_router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "u1"}
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    app.state.business_space = space
    return user_scoped_client(app, "u1")


def _ok(resp, code=200000):
    body = resp.json()
    assert resp.status_code == 200, body
    assert body["code"] == code, body
    assert "request_id" in body
    return body["data"]


def test_get_bot(client):
    data = _ok(client.get("/openapi/v1/bots/b1"))
    assert data["bot_id"] == "b1"
    assert data["engine"] == "teclaw"
    assert data["cluster_name"] == "ANDC"  # derived from engine
    assert data["owner_entity_id"] == "u1"


def test_list_bots(client):
    data = _ok(client.get("/openapi/v1/bots"))
    assert data["total"] == 1
    assert data["items"][0]["bot_id"] == "b1"


def test_list_bots_carries_template_snapshot_and_space(client):
    data = _ok(client.get("/openapi/v1/bots"))
    item = data["items"][0]
    # The stored snapshot reaches the wire verbatim — the passthrough decision
    # (2026-09-01): an owner-scoped face echoes the caller's own creation
    # input, secrets included, with no allowlist filtering.
    assert item["template_type"] == "applicationCoding"
    assert item["template_config"] == {
        "devflow_workflow": "release-notes",
        "token": "echoed-to-owner",
        "bot_template_config": {"ext_config": {"thetaKey": "enc:v1:x"}},
        "runtime": "codefuse",
    }
    # A NULL ac_bots.space_id resolves to the owner's synthetic personal space.
    space = item["space"]
    assert space["kind"] == "personal"
    assert space["space_id"] == "personal:u1"


def test_list_bots_resolves_space_once_per_distinct_space(client):
    data = _ok(client.get("/openapi/v1/bots"))
    assert data["total"] == 1
    # All rows share the NULL space_id column value, so memoization collapses
    # the per-page resolution to a single bot_space call.
    assert client.app.state.business_space.bot_space_calls == 1


def test_list_bots_survives_a_space_resolution_refusal(svc, startup_script):
    # A legacy row whose space lookup the space module refuses (a personal
    # space record exists but the membership row is gone) degrades to
    # space=null for that row; it must not fail the whole page.
    class _RefusingSpace:
        def bot_space(self, **_kwargs):
            raise BotInventoryPermissionError("business space is not available")

        def resolve_current(self, **_kwargs):
            raise AssertionError("listing never resolves the current space")

    class _M(Module):
        def configure(self, binder):
            binder.bind(BotServiceProtocol, to=svc)
            binder.bind(BusinessSpaceContextProtocol, to=_RefusingSpace())

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "u1"}
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    refusing_client = user_scoped_client(app, "u1")

    data = _ok(refusing_client.get("/openapi/v1/bots"))

    assert data["total"] == 1
    assert data["items"][0]["bot_id"] == "b1"
    assert data["items"][0]["space"] is None


def test_template_config_is_gated_on_template_type(client, svc):
    # get_bot attaches template_config whenever a template row exists —
    # NOT gated on template_type like the listing attach. A bot whose row
    # exists without a template_type must surface null config on every
    # face, per the published "null without a template" contract.
    row = {
        **BOT,
        "template_type": None,
        "template_config": {"devflow_workflow": "release-notes", "token": "raw"},
    }
    svc.list_bots_by_conditions.return_value = {"total": 1, "items": [row]}
    svc.get_bot.return_value = row

    listing = _ok(client.get("/openapi/v1/bots"))
    assert listing["items"][0]["template_type"] is None
    assert listing["items"][0]["template_config"] is None

    detail = _ok(client.get("/openapi/v1/bots/b1"))
    assert detail["template_type"] is None
    assert detail["template_config"] is None


def test_list_bots_filters_reach_service(client, svc):
    client.get(
        "/openapi/v1/bots?keyword=x&engine=teclaw&status=ACTIVE&page=2&page_size=5"
    )
    kw = svc.list_bots_by_conditions.call_args.kwargs
    assert kw["owner_id"] == "u1"
    assert kw["bot_name"] == "x"
    assert kw["engine"] == "teclaw"
    assert kw["status"] == "ACTIVE"
    assert kw["page"] == 2 and kw["page_size"] == 5


def test_search_bot_metadata_returns_only_display_fields(client, svc):
    response = client.post(
        "/openapi/v1/bots/metadata/queries?page=2&page_size=5",
        json={
            "bots": [
                {"bot_id": "b1", "owner_id": "u1"},
                {"bot_id": "b1", "owner_id": "u1"},
                {"bot_id": "b2", "owner_id": "u2"},
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "total": 1,
        "items": [
            {
                "bot_id": "b1",
                "owner_id": "u1",
                "bot_name": "N",
                "bot_desc": "D",
                "engine": "teclaw",
                "bot_type": "personal",
                "status": "ACTIVE",
            }
        ],
    }
    assert svc.list_bots_by_owner_bot_pairs.call_args.kwargs == {
        "page": 2,
        "page_size": 5,
        "pairs": [("b1", "u1"), ("b2", "u2")],
    }


@pytest.mark.parametrize(
    "bots",
    [
        [],
        [{"bot_id": f"b{i}", "owner_id": f"u{i}"} for i in range(101)],
    ],
)
def test_search_bot_metadata_rejects_invalid_batch_size(client, bots):
    response = client.post(
        "/openapi/v1/bots/metadata/queries",
        json={"bots": bots},
    )

    assert response.status_code == 422


def test_check_name_needs_no_user_id(client):
    """The one bots operation with no user dimension answers without one.

    Name uniqueness is checked across the tenant, so there is nothing to scope
    by. ``user_id=None`` is how ``user_scoped_client`` is told to omit the
    parameter rather than send it empty.
    """
    response = client.get(
        "/openapi/v1/bots/check-name", params={"name": "Foo", "user_id": None}
    )

    assert response.status_code == 200, response.json()


def test_check_name(client):
    data = _ok(client.get("/openapi/v1/bots/check-name?name=Foo"))
    assert data == {"name": "Foo", "exists": True}


def test_ceiling(client):
    assert _ok(client.get("/openapi/v1/bots/ceiling"))["ceiling"] == 7


def test_status(client):
    data = _ok(client.get("/openapi/v1/bots/b1/status"))
    assert data["status"] == "ACTIVE"
    assert data["is_ready"] is True
    assert data["device_id"] == "dev-9"


def test_passport(client):
    data = _ok(client.get("/openapi/v1/bots/b1/passport"))
    # passport_id (agent_code/agent_id) is the existence signal; the license
    # fields stay nullable when the PassportPlugin did not return them.
    assert data["bot_id"] == "b1"
    assert data["passport_id"] == "ac-1"
    assert data["expire_at"] is None
    assert data["certificate_url"] is None


def test_passport_forwards_license_fields(client, passport):
    passport.query_agent_passport.return_value = {
        "agent_code": "ac-1",
        "expire_at": "2027-01-01T00:00:00Z",
        "certificate_url": "https://cert/ac-1",
    }
    data = _ok(client.get("/openapi/v1/bots/b1/passport"))
    assert data["expire_at"] == "2027-01-01T00:00:00Z"
    assert data["certificate_url"] == "https://cert/ac-1"


def test_passport_missing_is_404(client, passport):
    passport.query_agent_passport.return_value = None
    resp = client.get("/openapi/v1/bots/b1/passport")
    assert resp.status_code == 404


def test_missing_principal_is_401(client):
    client.app.dependency_overrides[require_principal] = lambda: None
    resp = client.get("/openapi/v1/bots/b1")
    assert resp.status_code == 401
    assert resp.json()["code"] == 401000


def test_not_found_is_masked_404(client, svc):
    svc.get_bot.side_effect = BotNotFoundError("Bot not found: b1")
    resp = client.get("/openapi/v1/bots/b1")
    assert resp.status_code == 404
    # Fixed message — never the raw internal text.
    assert resp.json()["message"] == "Not found"


# ----- mutating endpoints (Task 7) -----------------------------------------


def test_update_bot(client, svc):
    data = _ok(client.put("/openapi/v1/bots/b1", json={"bot_name": "Renamed"}))
    assert data["bot_name"] == "Renamed"
    kw = svc.update_bot.call_args
    assert kw.args == ("b1", "u1")
    assert kw.kwargs["bot_name"] == "Renamed"


def test_delete_bot(client, svc):
    data = _ok(client.delete("/openapi/v1/bots/b1"))
    assert data == {"deleted": True}
    svc.delete_bot.assert_called_once_with("b1", "u1")


def test_change_bot_space(client, bot_space):
    data = _ok(client.put("/openapi/v1/bots/b1/space", json={"space_id": 42}))

    assert data == {
        "bot_id": "b1",
        "space_id": 42,
        "space_code": "spc-42",
        "space_name": "Team",
        "space_type": "TEAM",
        "changed": True,
    }
    bot_space.change_space.assert_called_once_with(
        bot_id="b1", owner_id="u1", space_id=42
    )


def test_change_bot_space_requires_explicit_user_id(client, bot_space):
    response = client.put(
        "/openapi/v1/bots/b1/space",
        params={"user_id": None},
        json={"space_id": 42},
    )

    assert response.status_code == 422
    assert response.json()["code"] == 422000
    bot_space.change_space.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"space_id": 0},
        {"space_id": None},
        {"space_id": 42, "owner_id": "u2"},
    ],
)
def test_change_bot_space_rejects_invalid_bodies(client, bot_space, body):
    response = client.put("/openapi/v1/bots/b1/space", json=body)

    assert response.status_code == 422
    assert response.json()["code"] == 422000
    bot_space.change_space.assert_not_called()


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (BotNotFoundError("missing"), 404),
        (SpaceNotFoundError("missing"), 404),
        (SpaceAccessDeniedError("denied"), 403),
        (BotOperationNotAllowedError("desktop"), 409),
    ],
)
def test_change_bot_space_domain_failures_use_standard_envelope(
    client, bot_space, error, status
):
    bot_space.change_space.side_effect = error

    response = client.put("/openapi/v1/bots/b1/space", json={"space_id": 42})

    assert response.status_code == status
    body = response.json()
    assert body["code"] == status * 1000
    assert body["data"] is None
    assert "request_id" in body


def test_restart_bot(client):
    data = _ok(client.post("/openapi/v1/bots/b1/restart"))
    assert data["status"] == "PENDING"


def test_get_engine_config(client, engine_config):
    data = _ok(client.get("/openapi/v1/bots/b1/engine/config"))
    assert data == {"k": "v"}
    kw = engine_config.read_bot_config.call_args.kwargs
    assert kw["bot_id"] == "b1" and kw["owner_id"] == "u1"
    assert kw["engine_type"] == "teclaw"  # from bot active_engine


def test_update_engine_config(client, engine_config):
    data = _ok(client.put("/openapi/v1/bots/b1/engine/config", json={"a": 1}))
    assert data == {"a": 1}  # echoes the written config
    kw = engine_config.write_bot_config.call_args.kwargs
    assert kw["config"] == {"a": 1} and kw["owner_id"] == "u1"


def test_mutating_not_found_masked(client, svc):
    svc.get_bot.side_effect = BotNotFoundError("x")
    # engine-config guards via get_bot → masked 404
    assert client.get("/openapi/v1/bots/b1/engine/config").status_code == 404
    svc.update_bot.side_effect = BotNotFoundError("x")
    assert client.put("/openapi/v1/bots/b1", json={"bot_name": "y"}).status_code == 404


# ----- create + auth-status (Task 8) ---------------------------------------

# openclaw and teclaw are both in the default SUPPORTED_ENGINE_TYPES registry.
# Tests that need a narrower registry still patch _get_engine_types explicitly.
_CREATE_BODY = {
    "bot_name": "NewBot",
    "bot_desc": "d",
    "engine": "openclaw",
    "cluster_name": "ACRA",
    "bot_type": "personal",
}


def test_create_bot_201(client, svc, passport):
    passport.apply_first_agent_passport.return_value = {
        "token": "tok",
        "agent_code": "ac",
    }
    with patch.object(bots_router, "generate_bot_id", return_value="default"):
        resp = client.post("/openapi/v1/bots", json=_CREATE_BODY)
    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert body["code"] == 201000
    assert body["data"]["bot_id"] == "b1"
    svc.create_bot.assert_called_once()
    assert svc.create_bot.call_args.kwargs["space_id"] is None


def test_real_space_reference_exposes_numeric_id():
    assert (
        BusinessSpaceRef(space_id="42", name="Team", kind="team").numeric_id == 42
    )


def test_synthetic_personal_reference_has_no_numeric_id():
    assert (
        BusinessSpaceRef(
            space_id="personal:u1",
            name="Personal",
            kind="personal",
        ).numeric_id
        is None
    )


def test_create_bot_owner_relationship_failure_is_enveloped_502(
    client, passport, auth_rel
):
    passport.apply_first_agent_passport.return_value = {
        "token": "tok",
        "agent_code": "ac",
    }
    auth_rel.create_relationship.return_value = None
    with patch.object(bots_router, "generate_bot_id", return_value="default"):
        resp = client.post("/openapi/v1/bots", json=_CREATE_BODY)

    assert resp.status_code == 502
    assert resp.json()["code"] == 502000
    assert resp.json()["message"] == "Authorization relationship service error"
    assert resp.json()["data"] is None


def test_create_bot_normalizes_unexpected_relationship_failure(
    client, passport, auth_rel
):
    passport.apply_first_agent_passport.return_value = {
        "token": "tok",
        "agent_code": "ac",
    }
    auth_rel.create_relationship.side_effect = RuntimeError("downstream unavailable")
    with patch.object(bots_router, "generate_bot_id", return_value="default"):
        response = client.post("/openapi/v1/bots", json=_CREATE_BODY)

    assert response.status_code == 502
    assert response.json()["code"] == 502000
    assert response.json()["message"] == "Authorization relationship service error"


def test_create_bot_rejects_unresolved_business_space_before_side_effects(
    client, svc, passport
):
    response = client.post(
        "/openapi/v1/bots",
        json={
            "bot_name": "N2",
            "bot_desc": "D",
            "engine": "teclaw",
            "cluster_name": "ANDC",
            "bot_type": "personal",
            "space_id": "team:unknown",
        },
    )

    assert response.status_code == 404
    passport.apply_passport.assert_not_called()
    svc.create_bot.assert_not_called()


def test_create_bot_202_pending(client, passport):
    passport.apply_first_agent_passport.return_value = {
        "token": None,
        "iframe_url": "http://auth",
    }
    with patch.object(bots_router, "generate_bot_id", return_value="default"):
        resp = client.post("/openapi/v1/bots", json=_CREATE_BODY)
    assert resp.status_code == 202, resp.json()
    body = resp.json()
    assert body["code"] == 202000
    assert body["data"]["iframe_url"] == "http://auth"


def test_create_application_coding_requires_engine_properties_template(client, svc, passport):
    response = client.post(
        "/openapi/v1/bots",
        json={
            **_CREATE_BODY,
            "engine": "claude_code",
            "cluster_name": "ACRA",
            "bot_type": "personal",
            "engine_properties": {},
        },
    )
    assert response.status_code == 422, response.json()
    passport.apply_first_agent_passport.assert_not_called()
    svc.create_bot.assert_not_called()


def test_create_application_coding_maps_template_to_internal_spec(
    client, svc, passport
):
    properties = {
        "devflow_workflow": "app-flow",
        "yuque_kb_repos": [],
        "code_repos": [],
        "bot_template_config": {
            "preset_capabilities": {},
            "ext_config": {"thetaKey": "value"},
        },
    }
    passport.apply_first_agent_passport.return_value = {
        "token": "tok",
        "agent_code": "ac",
    }

    with patch.object(bots_router, "generate_bot_id", return_value="default"):
        response = client.post(
            "/openapi/v1/bots",
            json={
                **_CREATE_BODY,
                "engine": "claude_code",
                "engine_properties": {"template_config": properties},
            },
        )

    assert response.status_code == 201, response.json()
    kwargs = svc.create_bot.call_args.kwargs
    assert kwargs["template_type"] == "applicationCoding"
    assert kwargs["template_config"] == properties


def test_create_rejects_legacy_template_envelope(client, svc):
    response = client.post(
        "/openapi/v1/bots",
        json={
            **_CREATE_BODY,
            "template": {"type": "applicationCoding", "properties": {}},
        },
    )

    assert response.status_code == 422
    svc.create_bot.assert_not_called()


def test_create_rejects_legacy_top_level_template_fields(client, svc):
    response = client.post(
        "/openapi/v1/bots",
        json={
            **_CREATE_BODY,
            "template_type": "applicationCoding",
            "template_config": {},
        },
    )

    assert response.status_code == 422
    svc.create_bot.assert_not_called()


def test_create_rejects_unknown_engine_properties_fields(client, svc):
    response = client.post(
        "/openapi/v1/bots",
        json={
            **_CREATE_BODY,
            "engine_properties": {
                "template_config": {},
                "template_uid": "caller-controlled",
            },
        },
    )

    assert response.status_code == 422
    svc.create_bot.assert_not_called()


def test_create_engine_properties_for_non_coding_engine_is_combination_error(
    client, svc
):
    # openclaw + application-coding intent answers 409 (combination
    # unsupported), not a template-invalid 422 — the mapping the routing
    # through the default engine strategy preserves.
    response = client.post(
        "/openapi/v1/bots",
        json={
            **_CREATE_BODY,
            "engine_properties": {"template_config": {"devflow_workflow": "x"}},
        },
    )
    assert response.status_code == 409, response.json()
    assert response.json()["code"] == 409000
    svc.create_bot.assert_not_called()


_FACTORY_SNAPSHOT_BODY = {
    "template_type": "applicationCoding",
    "template_config": {
        "template_key": "applicationCoding",
        "template_uid": "aicoding_bot_template",
        "template_version": "V1",
        "template_version_id": 2800006,
        "template_name": "应用 Bot",
        "image": "reg.antgroup-inc.cn/aixcoding/arca:20260901140138",
        "resource_spec": {"cpu": "4", "memory": "8g", "disk": "50"},
        "envs": {"AIX_SKIP_DAEMON": "false"},
        "capabilities": {"channel_management": False},
        "bot_template_config": {"id": 2800006},
        "custom_field_values": {"field_a": "value_a"},
    },
}


def test_create_factory_snapshot_passthrough_persists_verbatim(
    client, svc, passport
):
    passport.apply_first_agent_passport.return_value = {
        "token": "tok",
        "agent_code": "ac",
    }
    with patch.object(bots_router, "generate_bot_id", return_value="default"):
        response = client.post(
            "/openapi/v1/bots",
            json={
                **_CREATE_BODY,
                "engine": "claude_code",
                "engine_properties": dict(_FACTORY_SNAPSHOT_BODY),
            },
        )
    assert response.status_code == 201, response.json()
    kwargs = svc.create_bot.call_args.kwargs
    assert kwargs["template_type"] == "applicationCoding"
    assert kwargs["template_config"] == _FACTORY_SNAPSHOT_BODY["template_config"]


def test_create_factory_snapshot_missing_template_type_is_422(client, svc):
    response = client.post(
        "/openapi/v1/bots",
        json={
            **_CREATE_BODY,
            "engine": "claude_code",
            "engine_properties": {
                "template_config": _FACTORY_SNAPSHOT_BODY["template_config"]
            },
        },
    )
    assert response.status_code == 422, response.json()
    svc.create_bot.assert_not_called()


def test_create_factory_snapshot_server_managed_field_is_422(client, svc):
    response = client.post(
        "/openapi/v1/bots",
        json={
            **_CREATE_BODY,
            "engine": "claude_code",
            "engine_properties": {
                **_FACTORY_SNAPSHOT_BODY,
                "template_config": {
                    **_FACTORY_SNAPSHOT_BODY["template_config"],
                    "engine_form": "aicoding",
                },
            },
        },
    )
    assert response.status_code == 422, response.json()
    svc.create_bot.assert_not_called()


def test_create_factory_snapshot_with_form_fields_persists_verbatim(
    client, svc, passport
):
    # tc-list 快照的 custom_field 表单值展开在顶层(与手填键同名):
    # 工厂身份由 template_key+template_uid 判定,不按键名拒绝
    passport.apply_first_agent_passport.return_value = {
        "token": "tok",
        "agent_code": "ac",
    }
    snapshot = {
        **_FACTORY_SNAPSHOT_BODY["template_config"],
        "architect_name": "大安全",
        "yuque_kb_repos": [],
        "devflow_workflow": None,
    }
    with patch.object(bots_router, "generate_bot_id", return_value="default"):
        response = client.post(
            "/openapi/v1/bots",
            json={
                **_CREATE_BODY,
                "engine": "claude_code",
                "engine_properties": {
                    "template_type": "architect",
                    "template_config": snapshot,
                },
            },
        )
    assert response.status_code == 201, response.json()
    kwargs = svc.create_bot.call_args.kwargs
    assert kwargs["template_type"] == "architect"
    assert kwargs["template_config"] == snapshot


def test_create_handcrafted_with_foreign_template_type_is_422(client, svc):
    response = client.post(
        "/openapi/v1/bots",
        json={
            **_CREATE_BODY,
            "engine": "claude_code",
            "engine_properties": {
                "template_type": "architect",
                "template_config": {"devflow_workflow": "x"},
            },
        },
    )
    assert response.status_code == 422, response.json()
    svc.create_bot.assert_not_called()


def test_create_rejects_legacy_template_key_name(client, svc):
    # 改名反向回归:v1 契约的 "template" 键已不存在
    response = client.post(
        "/openapi/v1/bots",
        json={
            **_CREATE_BODY,
            "engine": "claude_code",
            "engine_properties": {"template": {"devflow_workflow": "x"}},
        },
    )
    assert response.status_code == 422, response.json()
    svc.create_bot.assert_not_called()


def test_create_factory_snapshot_for_service_bot_is_409(client, svc):
    response = client.post(
        "/openapi/v1/bots",
        json={
            **_CREATE_BODY,
            "engine": "claude_code",
            "bot_type": "service",
            "engine_properties": dict(_FACTORY_SNAPSHOT_BODY),
        },
    )
    assert response.status_code == 409, response.json()
    svc.create_bot.assert_not_called()


def test_create_bot_cluster_mismatch_400(client, svc):
    bad = {**_CREATE_BODY, "cluster_name": "ANDC"}  # openclaw must be ACRA
    with patch.object(bots_router, "generate_bot_id", return_value="default"):
        resp = client.post("/openapi/v1/bots", json=bad)
    assert resp.status_code == 400, resp.json()
    svc.create_bot.assert_not_called()


def test_create_bot_missing_principal_401(client):
    client.app.dependency_overrides[require_principal] = lambda: None
    resp = client.post("/openapi/v1/bots", json=_CREATE_BODY)
    assert resp.status_code == 401


def test_auth_status_pending(client, passport):
    passport.query_auth_status.return_value = {"status": "PENDING"}
    data = _ok(client.get("/openapi/v1/bots/b1/auth-status"))
    assert data["status"] == "PENDING"
    assert data["bot"] is None


def test_auth_status_issued(client, svc, passport):
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_agent_passport.return_value = {"agent_code": "ac"}
    data = _ok(client.get("/openapi/v1/bots/b1/auth-status"))
    assert data["status"] == "ISSUED"
    assert data["bot"]["bot_id"] == "b1"
    svc.create_bot.assert_called_once()


def test_auth_status_missing_agent_code_is_enveloped_before_create(
    client, svc, passport
):
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_agent_passport.return_value = {"agent_code": None}

    response = client.get("/openapi/v1/bots/b1/auth-status")

    assert response.status_code == 502
    assert response.json()["code"] == 502000
    assert response.json()["message"] == "Authorization service error"
    svc.create_bot.assert_not_called()


def test_auth_status_agent_identity_query_failure_is_enveloped_before_create(
    client, svc, passport
):
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_agent_passport.side_effect = RuntimeError("identity unavailable")

    response = client.get("/openapi/v1/bots/b1/auth-status")

    assert response.status_code == 502
    assert response.json()["code"] == 502000
    assert response.json()["message"] == "Authorization service error"
    svc.create_bot.assert_not_called()


def test_auth_status_issued_preserves_create_attributes(client, svc, passport):
    """Re-supplied attributes reach completion so the bot isn't downgraded."""
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_agent_passport.return_value = {"agent_code": "ac"}
    with patch.object(
        bots_router, "_get_engine_types", return_value=["openclaw", "teclaw"]
    ):
        _ok(
            client.get(
                "/openapi/v1/bots/b1/auth-status"
                "?engine=teclaw&cluster_name=ANDC&bot_name=NewBot&bot_desc=d"
            )
        )
    kw = svc.create_bot.call_args.kwargs
    assert kw["engine_type"] == "teclaw"  # not defaulted to openclaw
    assert kw["bot_name"] == "NewBot"
    assert kw["bot_desc"] == "d"


def test_auth_status_rejects_unresolved_business_space_before_polling(
    client, svc, passport
):
    response = client.get(
        "/openapi/v1/bots/b1/auth-status",
        params={
            "engine": "teclaw",
            "cluster_name": "ANDC",
            "space_id": "team:unknown",
        },
    )

    assert response.status_code == 404
    passport.query_auth_status.assert_not_called()
    svc.create_bot.assert_not_called()


def test_auth_status_engine_cluster_mismatch_400(client, svc):
    # teclaw is enabled here on purpose: the rejection must come from the
    # engine/cluster bijection, not incidentally from the registry check.
    with patch.object(
        bots_router, "_get_engine_types", return_value=["openclaw", "teclaw"]
    ):
        resp = client.get(
            "/openapi/v1/bots/b1/auth-status?engine=teclaw&cluster_name=ACRA"
        )
    assert resp.status_code == 400
    svc.create_bot.assert_not_called()


# ----- POST auth-status (the current spelling; GET above is retiring) -------


def test_post_auth_status_pending(client, passport):
    passport.query_auth_status.return_value = {"status": "PENDING"}
    data = _ok(client.post("/openapi/v1/bots/b1/auth-status", json={}))
    assert data["status"] == "PENDING"
    assert data["bot"] is None


def test_post_auth_status_issued(client, svc, passport):
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_agent_passport.return_value = {"agent_code": "ac"}
    data = _ok(client.post("/openapi/v1/bots/b1/auth-status", json={}))
    assert data["status"] == "ISSUED"
    assert data["bot"]["bot_id"] == "b1"
    svc.create_bot.assert_called_once()


def test_post_auth_status_preserves_create_attributes(client, svc, passport):
    """The body fields reach completion so the bot isn't downgraded."""
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_agent_passport.return_value = {"agent_code": "ac"}
    with patch.object(
        bots_router, "_get_engine_types", return_value=["openclaw", "teclaw"]
    ):
        _ok(client.post(
            "/openapi/v1/bots/b1/auth-status",
            json={
                "engine": "teclaw", "cluster_name": "ANDC",
                "bot_name": "NewBot", "bot_desc": "d", "bot_type": "service",
            },
        ))
    kw = svc.create_bot.call_args.kwargs
    assert kw["engine_type"] == "teclaw"  # not defaulted to openclaw
    assert kw["bot_name"] == "NewBot"
    assert kw["bot_desc"] == "d"
    assert kw["bot_type"] == "service"


def test_post_auth_status_application_coding_requires_engine_properties_template(
    client, svc, passport
):
    response = client.post(
        "/openapi/v1/bots/b1/auth-status",
        json={
            "engine": "claude_code",
            "cluster_name": "ACRA",
            "engine_properties": {},
        },
    )
    assert response.status_code == 422, response.json()
    passport.query_auth_status.assert_not_called()
    svc.create_bot.assert_not_called()


def test_post_auth_status_maps_template_to_internal_spec(client, svc, passport):
    properties = {
        "devflow_workflow": "app-flow",
        "bot_template_config": {"ext_config": {"thetaKey": "value"}},
    }
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_agent_passport.return_value = {"agent_code": "ac"}

    response = client.post(
        "/openapi/v1/bots/b1/auth-status",
        json={
            "engine": "claude_code",
            "cluster_name": "ACRA",
            "engine_properties": {"template_config": properties},
        },
    )

    assert response.status_code == 200, response.json()
    kwargs = svc.create_bot.call_args.kwargs
    assert kwargs["template_type"] == "applicationCoding"
    assert kwargs["template_config"] == properties


def test_post_auth_status_engine_cluster_mismatch_400(client, svc):
    with patch.object(
        bots_router, "_get_engine_types", return_value=["openclaw", "teclaw"]
    ):
        resp = client.post(
            "/openapi/v1/bots/b1/auth-status",
            json={"engine": "teclaw", "cluster_name": "ACRA"},
        )
    assert resp.status_code == 400
    svc.create_bot.assert_not_called()


def test_post_auth_status_rejects_unknown_engine(client, svc, passport):
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    resp = client.post(
        "/openapi/v1/bots/b1/auth-status",
        json={"engine": "not-a-real-engine", "cluster_name": "ACRA"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == 400000
    svc.create_bot.assert_not_called()


def test_post_auth_status_rejects_desktop_bot_type(client, svc, passport):
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    resp = client.post(
        "/openapi/v1/bots/b1/auth-status", json={"bot_type": "desktop"}
    )
    assert resp.status_code == 422
    svc.create_bot.assert_not_called()


def test_post_auth_status_terminal_state_is_400(client, passport):
    passport.query_auth_status.return_value = {"status": "REJECTED"}
    resp = client.post("/openapi/v1/bots/b1/auth-status", json={})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 400000
    assert body["data"]["status"] == "REJECTED"  # caller can still see why


def test_post_auth_status_passport_not_ready_is_pending(client, svc, passport):
    """A passport service with no status yet answers PENDING, not an error.

    Both public spellings answer this way (the GET's twin is
    test_missing_auth_status_is_pending_not_an_error); the internal /api/bots
    route keeps raising. Nothing may be created on this answer.
    """
    passport.query_auth_status.return_value = None
    data = _ok(client.post("/openapi/v1/bots/b1/auth-status", json={}))
    assert data["status"] == "PENDING"
    assert "not ready" in data["message"]
    assert data["bot"] is None
    svc.create_bot.assert_not_called()


# ----- round-1 review regressions ------------------------------------------


def test_application_bot_not_ready_until_repos_cloned(client, svc):
    """R1/F1: ACTIVE alone must not report an application bot as ready."""
    svc.get_bot.return_value = {
        **BOT,
        "template_type": "applicationCoding",
        "active_engine": "aicoding",
        "ext": {"start_status": "STARTING"},
    }
    assert _ok(client.get("/openapi/v1/bots/b1/status"))["is_ready"] is False

    # ...and ready once the clone reports SUCCEEDED.
    svc.get_bot.return_value = {
        **BOT,
        "template_type": "applicationCoding",
        "active_engine": "aicoding",
        "ext": {"start_status": "SUCCEEDED"},
    }
    assert _ok(client.get("/openapi/v1/bots/b1/status"))["is_ready"] is True


def test_non_application_bot_ignores_start_status(client, svc):
    """R1/F1 guard: the extra gate must not regress ordinary bots."""
    svc.get_bot.return_value = {**BOT, "ext": {"start_status": "STARTING"}}
    assert _ok(client.get("/openapi/v1/bots/b1/status"))["is_ready"] is True


def test_base_service_error_is_enveloped(client, svc):
    """R1/F2: a bare BotServiceError must not escape as {"detail": ...}."""
    from agentclaw.community.core.bot_management.services.bot_service import (
        BotServiceError,
    )

    svc.get_bot.side_effect = BotServiceError("device blew up")
    resp = client.get("/openapi/v1/bots/b1")
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == 500000
    assert body["data"] is None
    assert "request_id" in body
    assert body["message"] == "Internal error"  # no internal text leaked


def test_update_rejects_invalid_bot_name(client, svc):
    """R1/F4: the public update path enforces the same name rule as create."""
    resp = client.put("/openapi/v1/bots/b1", json={"bot_name": "bad@name"})
    assert resp.status_code == 400
    svc.update_bot.assert_not_called()


def test_update_syncs_passport_identity(client, passport):
    """R1/F5: renaming must not leave the Passport carrying stale metadata."""
    client.put("/openapi/v1/bots/b1", json={"bot_name": "Renamed"})
    kw = passport.update_passport.call_args.kwargs
    assert kw["bot_id"] == "b1"
    assert kw["user_id"] == "u1"
    assert kw["bot_name"] == "Renamed"


def test_update_passport_failure_is_enveloped_502(client, svc, passport):
    passport.update_passport.side_effect = RuntimeError("downstream unavailable")

    resp = client.put("/openapi/v1/bots/b1", json={"bot_name": "Renamed"})

    assert svc.update_bot.called
    assert resp.status_code == 502
    assert resp.json()["code"] == 502000
    assert resp.json()["message"] == "Authorization service error"
    assert resp.json()["data"] is None


def test_update_without_identity_change_skips_passport(client, passport):
    """No name/desc change → no passport call (mirrors the internal route)."""
    client.put("/openapi/v1/bots/b1", json={})
    passport.update_passport.assert_not_called()


def test_update_rejects_unknown_and_immutable_fields(client, svc):
    """R2/F9: sending `engine` must fail, not be silently ignored."""
    resp = client.put("/openapi/v1/bots/b1", json={"engine": "teclaw"})
    assert resp.status_code == 422
    svc.update_bot.assert_not_called()


def test_clearing_description_still_syncs_passport(client, passport):
    """R2/F10: `bot_desc=""` is a real change and must reach the Passport."""
    client.put("/openapi/v1/bots/b1", json={"bot_desc": ""})
    kw = passport.update_passport.call_args.kwargs
    assert kw["bot_desc"] == ""


def test_engine_config_device_not_bound_is_enveloped(client, engine_config):
    """R2/F11: engine-config failures must not escape as raw {"detail": ...}."""
    from agentclaw.community.core.devices.services.device_context import (
        DeviceNotBoundError,
    )

    engine_config.read_bot_config.side_effect = DeviceNotBoundError("no binding")
    resp = client.get("/openapi/v1/bots/b1/engine/config")
    assert resp.status_code == 409
    assert resp.json()["code"] == 409000


def test_engine_config_malformed_json_is_enveloped(client, engine_config):
    """R2/F11: a malformed stored config is enveloped, not a bare 500 detail."""
    import json

    engine_config.read_bot_config.side_effect = json.JSONDecodeError("bad", "{", 0)
    resp = client.get("/openapi/v1/bots/b1/engine/config")
    assert resp.status_code == 500
    assert resp.json()["code"] == 500000
    assert resp.json()["data"] is None


def test_pending_auth_forwards_redirect_url(client, passport, svc):
    """R2/F12: a redirect-only Passport response must not lose the handle."""
    passport.apply_first_agent_passport.return_value = {
        "token": None,
        "redirect_url": "http://redirect",
        "iframe_url": None,
    }
    with patch.object(bots_router, "generate_bot_id", return_value="default"):
        resp = client.post("/openapi/v1/bots", json=_CREATE_BODY)
    assert resp.status_code == 202
    assert resp.json()["data"]["redirect_url"] == "http://redirect"


def test_deleting_default_bot_is_client_error(client, svc):
    """R2/F13: an unsupported operation is 4xx, not a retryable 500."""
    from agentclaw.community.core.bot_management.services.bot_service import (
        BotOperationNotAllowedError,
    )

    svc.delete_bot.side_effect = BotOperationNotAllowedError("default 不允许删除")
    resp = client.delete("/openapi/v1/bots/default")
    assert resp.status_code == 409
    assert resp.json()["code"] == 409000


def test_unsupported_engine_rejected_before_side_effects(
    client, svc, passport, bot_repo
):
    """R3/F16: an unknown engine must not allocate an id or apply for a Passport."""
    bad = {**_CREATE_BODY, "engine": "not-a-real-engine", "cluster_name": "ACRA"}
    with patch.object(bots_router, "generate_bot_id", return_value="default") as gen:
        resp = client.post("/openapi/v1/bots", json=bad)
    assert resp.status_code == 400
    assert resp.json()["code"] == 400000
    # Rejected up front — no id allocated, no Passport applied, nothing created.
    gen.assert_not_called()
    passport.apply_first_agent_passport.assert_not_called()
    svc.create_bot.assert_not_called()


def test_teclaw_andc_create_allowed_when_engine_configured(client, svc, passport):
    """The ANDC cluster is reachable wherever teclaw is a configured engine.

    teclaw is absent from the default registry, so the engine check rejects it
    unless the deployment enables it via ENGINE_TYPES — this pins that the
    teclaw/ANDC pairing itself is valid, not accidentally unreachable.
    """
    passport.apply_first_agent_passport.return_value = {
        "token": "tok",
        "agent_code": "ac",
    }
    body = {**_CREATE_BODY, "engine": "teclaw", "cluster_name": "ANDC"}
    with (
        patch.object(
            bots_router, "_get_engine_types", return_value=["openclaw", "teclaw"]
        ),
        patch.object(bots_router, "generate_bot_id", return_value="default"),
    ):
        resp = client.post("/openapi/v1/bots", json=body)
    assert resp.status_code == 201, resp.json()
    svc.create_bot.assert_called_once()


def test_service_create_rejects_non_service_engine_before_side_effects(
    client, svc, passport
):
    body = {
        **_CREATE_BODY,
        "engine": "hermes",
        "cluster_name": "ACRA",
        "bot_type": "service",
    }
    with (
        patch.object(
            bots_router, "_get_engine_types", return_value=["openclaw", "hermes"]
        ),
        patch.object(bots_router, "generate_bot_id", return_value="default") as gen,
    ):
        response = client.post("/openapi/v1/bots", json=body)

    assert response.status_code == 409
    assert response.json()["code"] == 409000
    gen.assert_not_called()
    passport.apply_first_agent_passport.assert_not_called()
    svc.create_bot.assert_not_called()


def test_desktop_bot_type_rejected(client, svc):
    """R3/F17: desktop bots have their own flow; 201-ing a PENDING shell is wrong."""
    resp = client.post("/openapi/v1/bots", json={**_CREATE_BODY, "bot_type": "desktop"})
    assert resp.status_code == 422
    svc.create_bot.assert_not_called()


def test_missing_auth_status_is_pending_not_an_error(client, svc, passport):
    """R3/F18 successor: a null query_auth_status must not escape as a 500 —
    and on this public surface it is not an error at all. The GET answers the
    not-ready wait exactly as its POST replacement does (shared completion
    body); the internal /api/bots route keeps raising."""
    passport.query_auth_status.return_value = None
    data = _ok(client.get("/openapi/v1/bots/b1/auth-status"))
    assert data["status"] == "PENDING"
    assert "not ready" in data["message"]
    assert data["bot"] is None
    svc.create_bot.assert_not_called()


def test_rejected_authorization_is_not_reported_as_success(client, passport):
    """R1/F8: a terminal auth state must not come back as 200/200000 OK."""
    passport.query_auth_status.return_value = {"status": "REJECTED"}
    resp = client.get("/openapi/v1/bots/b1/auth-status")
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 400000
    assert body["data"]["status"] == "REJECTED"  # caller can still see why


# ----- round-4 review regressions ------------------------------------------


def test_auth_status_rejects_unknown_engine(client, svc, passport):
    """R4/F19: the completion path must apply POST's engine registry check.

    The bot row is actually inserted here, so an engine ``POST`` rejects must
    not become creatable by echoing it back on the poll.
    """
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    resp = client.get(
        "/openapi/v1/bots/b1/auth-status?engine=not-a-real-engine&cluster_name=ACRA"
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == 400000
    svc.create_bot.assert_not_called()


def test_auth_status_rejects_desktop_bot_type(client, svc, passport):
    """R4/F19: ``bot_type`` is restricted on completion exactly as on create."""
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    resp = client.get("/openapi/v1/bots/b1/auth-status?bot_type=desktop")
    assert resp.status_code == 422
    svc.create_bot.assert_not_called()


def test_auth_status_accepts_supported_bot_type(client, svc, passport):
    """The restriction rejects only what create rejects — service still passes."""
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_agent_passport.return_value = {"agent_code": "ac"}
    _ok(client.get("/openapi/v1/bots/b1/auth-status?bot_type=service"))
    assert svc.create_bot.call_args.kwargs["bot_type"] == "service"


def test_auth_status_rejects_non_service_engine_before_authorization_lookup(
    client, svc, passport
):
    with patch.object(
        bots_router, "_get_engine_types", return_value=["openclaw", "hermes"]
    ):
        response = client.get(
            "/openapi/v1/bots/b1/auth-status"
            "?engine=hermes&cluster_name=ACRA&bot_type=service"
        )

    assert response.status_code == 409
    assert response.json()["code"] == 409000
    passport.query_auth_status.assert_not_called()
    svc.create_bot.assert_not_called()


def test_engine_config_unknown_provider_is_enveloped(client, engine_config):
    """R4/F21: a documented resolver failure must not escape the envelope.

    ``UnknownProviderError`` is a sibling of ``DeviceNotBoundError``, not a
    subclass, so mapping the latter does not cover it.
    """
    from agentclaw.community.core.devices.services.device_context import (
        UnknownProviderError,
    )

    engine_config.read_bot_config.side_effect = UnknownProviderError("bad provider")
    resp = client.get("/openapi/v1/bots/b1/engine/config")
    assert resp.status_code == 500
    assert resp.json()["code"] == 500000
    assert resp.json()["data"] is None


def test_engine_config_conn_info_failure_is_enveloped(client, engine_config):
    """R4/F21: the underlying device-service call failing is a 502, enveloped."""
    from agentclaw.community.core.devices.services.device_context import (
        ConnInfoBuildError,
    )

    engine_config.read_bot_config.side_effect = ConnInfoBuildError("baas down")
    resp = client.get("/openapi/v1/bots/b1/engine/config")
    assert resp.status_code == 502
    assert resp.json()["code"] == 502000
    assert resp.json()["data"] is None


# ----- round-5 review regressions ------------------------------------------


def test_create_rejects_unappliable_engine_options(client, svc, passport, bot_repo):
    """R5/F22 + R8/F35: nothing reads extra_properties, so 201 would discard it.

    R5 rejected a non-empty value at runtime while the schema still advertised
    the field — a contract slot the server always refused. The field is now
    absent, so ``extra="forbid"`` names it in the validation error and the
    published schema stops promising something untrue.
    """
    body = {**_CREATE_BODY, "engine_options": {"model": "x"}}
    with patch.object(bots_router, "generate_bot_id", return_value="default") as gen:
        resp = client.post("/openapi/v1/bots", json=body)
    assert resp.status_code == 422
    # Rejected up front — no id allocated, no Passport applied, nothing created.
    gen.assert_not_called()
    passport.apply_first_agent_passport.assert_not_called()
    svc.create_bot.assert_not_called()


def test_create_rejects_even_an_empty_engine_options(client, svc):
    """R8/F35: the field is gone from the contract, not merely constrained."""
    resp = client.post("/openapi/v1/bots", json={**_CREATE_BODY, "engine_options": {}})
    assert resp.status_code == 422
    svc.create_bot.assert_not_called()


def test_create_schema_does_not_advertise_engine_options(client):
    """A generated client must not be able to compile a request we always reject."""
    schema = client.app.openapi()["components"]["schemas"]["BotCreate"]
    assert "engine_options" not in schema["properties"]


def test_create_schema_nests_template_config_under_engine_properties(client):
    schemas = client.app.openapi()["components"]["schemas"]
    create_properties = schemas["BotCreate"]["properties"]
    poll_properties = schemas["BotAuthStatusPoll"]["properties"]
    engine_properties = schemas["BotCreateEngineProperties"]["properties"]

    assert "engine_properties" in create_properties
    assert "engine_properties" in poll_properties
    assert "template" not in create_properties
    assert "template" not in poll_properties
    assert "template_type" not in create_properties
    assert "template_config" not in create_properties
    assert "template_type" not in poll_properties
    assert "template_config" not in poll_properties
    assert set(engine_properties) == {"template_type", "template_config"}
    assert schemas["BotCreateEngineProperties"]["required"] == ["template_config"]


def test_auth_status_validates_cluster_against_default_engine(client, svc, passport):
    """R5/F23: omitting ``engine`` means the default engine, not "no engine".

    Without this the ANDC cluster passed unchecked and completion provisioned
    the ACRA default — a success response contradicting the request.
    """
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    resp = client.get("/openapi/v1/bots/b1/auth-status?cluster_name=ANDC")
    assert resp.status_code == 400
    assert resp.json()["code"] == 400000
    svc.create_bot.assert_not_called()


def test_auth_status_default_engine_accepts_matching_cluster(client, svc, passport):
    """…and the cluster the default engine *does* belong to still passes."""
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_agent_passport.return_value = {"agent_code": "ac"}
    _ok(client.get("/openapi/v1/bots/b1/auth-status?cluster_name=ACRA"))
    svc.create_bot.assert_called_once()


def test_delete_desktop_bot_is_rejected(client, svc):
    """R5/F26: generic delete leaves the BaaS container running."""
    svc.get_bot.return_value = {**BOT, "bot_type": "desktop"}
    resp = client.delete("/openapi/v1/bots/b1")
    assert resp.status_code == 409
    assert resp.json()["code"] == 409000
    svc.delete_bot.assert_not_called()


def test_restart_desktop_bot_is_rejected(client, svc):
    """Same policy for restart — desktop re-provisioning has its own path."""
    svc.get_bot.return_value = {**BOT, "bot_type": "desktop"}
    resp = client.post("/openapi/v1/bots/b1/restart")
    assert resp.status_code == 409
    svc.restart_bot.assert_not_called()


def test_non_desktop_lifecycle_operations_still_work(client, svc):
    """The guard must not block the bot types this surface does manage."""
    _ok(client.delete("/openapi/v1/bots/b1"))
    svc.delete_bot.assert_called_once_with("b1", "u1")
    _ok(client.post("/openapi/v1/bots/b1/restart"))
    svc.restart_bot.assert_called_once()


# ----- round-6 review regressions ------------------------------------------


def test_update_rejects_fields_it_cannot_apply(client, svc):
    """R6/F29: 200 for a request that changed nothing is a lie.

    ``cluster_name`` is engine-derived and the engine is immutable;
    ``engine_options`` belongs to the engine-config endpoints. Neither can be
    applied here, so neither is accepted — same treatment as ``engine`` (F9).
    """
    for field, value in [("cluster_name", "ACRA"), ("engine_options", {"model": "x"})]:
        resp = client.put("/openapi/v1/bots/b1", json={field: value})
        assert resp.status_code == 422, f"{field} was accepted: {resp.json()}"
    svc.update_bot.assert_not_called()


def test_passport_accepts_the_local_plugin_identifier(client, passport):
    """R6/F32: the local plugin issues ``agent_id`` and leaves ``agent_code`` null."""
    passport.query_agent_passport.return_value = {
        "agent_id": "b1",
        "agent_code": None,
        "mcps": [],
    }
    data = _ok(client.get("/openapi/v1/bots/b1/passport"))
    # ``agent_id`` sets passport_id; license fields stay null when absent.
    assert data["bot_id"] == "b1"
    assert data["passport_id"] == "b1"
    assert data["expire_at"] is None
    assert data["certificate_url"] is None


def test_passport_prefers_agent_code_when_both_present(client, passport):
    """``agent_code`` stays the primary identifier where the provider issues one."""
    passport.query_agent_passport.return_value = {
        "agent_id": "b1",
        "agent_code": "ac-1",
    }
    data = _ok(client.get("/openapi/v1/bots/b1/passport"))
    assert data["passport_id"] == "ac-1"


def test_passport_absent_is_still_404(client, passport):
    """Neither identifier present still means no passport."""
    passport.query_agent_passport.return_value = {"agent_id": None, "agent_code": None}
    assert client.get("/openapi/v1/bots/b1/passport").status_code == 404


# ----- round-8 review regressions ------------------------------------------


def test_check_name_rejects_names_create_would_reject(client, svc):
    """R8/F36: "available" has to mean "you could create this".

    ``check_bot_name_exists`` only does a repository lookup, so an invalid name
    came back ``exists: false`` — reported free, then rejected by the very next
    create call.
    """
    for bad in ["bad@name", "   ", "x" * 33]:
        resp = client.get("/openapi/v1/bots/check-name", params={"name": bad})
        assert resp.status_code == 400, f"{bad!r} was accepted: {resp.json()}"
        assert resp.json()["code"] == 400000
    svc.check_bot_name_exists.assert_not_called()


def test_check_name_echoes_the_normalized_name(client, svc):
    """The answer applies to the trimmed name that was actually looked up."""
    data = _ok(client.get("/openapi/v1/bots/check-name", params={"name": "  Foo  "}))
    assert data == {"name": "Foo", "exists": True}
    svc.check_bot_name_exists.assert_called_once_with("Foo")


def test_update_forwards_bearer_token_for_bcn_sync(client, svc):
    """R8/F37: without it the downstream BCN sync runs unauthenticated."""
    client.put(
        "/openapi/v1/bots/b1",
        json={"bot_name": "Renamed"},
        headers={"Authorization": "Bearer tok-1", "Cookie": "session=secret"},
    )
    headers = svc.update_bot.call_args.kwargs["request_headers"]
    assert headers == {"Authorization": "Bearer tok-1"}
    # The browser-session credential stays above the adapter boundary.
    assert "Cookie" not in headers


def test_update_without_authorization_forwards_nothing(client, svc):
    """No credential in, no credential out — not a header with an empty value."""
    client.put("/openapi/v1/bots/b1", json={"bot_name": "Renamed"})
    assert svc.update_bot.call_args.kwargs["request_headers"] == {}


# ----- round-10 review regressions -----------------------------------------


def test_update_rejects_explicit_null(client, svc):
    """R10/F41: `{"bot_desc": null}` was a 200 that changed nothing.

    ``update_bot`` reads ``None`` as "field omitted", so an explicit null was
    indistinguishable from not sending the field — the caller asked to clear the
    description and got a success back with it untouched.
    """
    for field in ["bot_name", "bot_desc"]:
        resp = client.put("/openapi/v1/bots/b1", json={field: None})
        assert resp.status_code == 422, f"{field}: null accepted as a no-op"
    svc.update_bot.assert_not_called()


def test_update_schema_does_not_advertise_nullable_fields(client):
    """A client must not be able to compile the request we reject."""
    props = client.app.openapi()["components"]["schemas"]["BotUpdate"]["properties"]
    for field in ["bot_name", "bot_desc"]:
        assert props[field].get("type") == "string", props[field]
        assert "anyOf" not in props[field], props[field]


def test_update_omitting_a_field_still_leaves_it_unchanged(client, svc):
    """Omission keeps its meaning — only explicit null is refused."""
    _ok(client.put("/openapi/v1/bots/b1", json={"bot_name": "Renamed"}))
    kw = svc.update_bot.call_args.kwargs
    assert kw["bot_name"] == "Renamed"
    assert kw["bot_desc"] is None  # untouched


def test_ceiling_uses_the_limit_creation_enforces(client, svc):
    """R10/F42: reporting must not resolve the quota differently from create.

    ``PolicyService.get_bots_ceiling`` defaults to a hardcoded 5; creation falls
    back to the configured ``max_devices_per_entity``. Reading the policy service
    directly advertised 5 to a deployment that allows something else.
    """
    svc.get_bots_ceiling_for_owner.return_value = 12
    assert _ok(client.get("/openapi/v1/bots/ceiling"))["ceiling"] == 12
    svc.get_bots_ceiling_for_owner.assert_called_once_with("u1")


# ----- round-13 review regressions ------------------------------------------


def test_auth_status_validates_the_defaulted_engine(client, svc, passport):
    """R13/F46: omitting `engine` still means an engine — validate that one.

    On a registry that excludes openclaw, completion would otherwise create a
    bot on the default engine anyway, and F44 now persists the configured
    registry — so the bot's own active_engine would be missing from its
    enabled-engine list.
    """
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    with patch.object(bots_router, "_get_engine_types", return_value=["teclaw"]):
        resp = client.get("/openapi/v1/bots/b1/auth-status")
    assert resp.status_code == 400
    assert resp.json()["code"] == 400000
    # Rejected before Passport is consulted or anything is created.
    passport.query_auth_status.assert_not_called()
    svc.create_bot.assert_not_called()


def test_auth_status_defaulted_engine_passes_when_configured(client, svc, passport):
    """The check binds to the registry, not to "engine was omitted"."""
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_agent_passport.return_value = {"agent_code": "ac"}
    _ok(client.get("/openapi/v1/bots/b1/auth-status"))
    svc.create_bot.assert_called_once()


# ----- round-14 review regressions ------------------------------------------


def test_update_re_enables_bcn_sync(client, svc):
    """BCN sync is back on for this surface.

    The F49 stopgap forced ``sync_to_bcn=False`` because ``bot_id`` was only
    unique per owner — every owner's first bot was "default" — so two tenants
    sharing a principal collapsed to one BCN record. Now that
    ``(bot_id, owner_workno)`` is globally unique, the cross-tenant write is no
    longer possible, so the openapi update surface no longer forces the flag
    off. ``update_bot`` defaults ``sync_to_bcn`` to ``True``; the contract here
    is "not forced False" — explicitly ``True`` or omitted (default) both pass.
    """
    _ok(client.put("/openapi/v1/bots/b1", json={"bot_name": "Renamed"}))
    assert svc.update_bot.call_args.kwargs.get("sync_to_bcn", True) is True


def test_update_still_carries_the_bearer_token(client, svc):
    """The F37 header stays, so re-enabling the sync can't silently regress it."""
    client.put(
        "/openapi/v1/bots/b1",
        json={"bot_name": "Renamed"},
        headers={"Authorization": "Bearer tok-1"},
    )
    assert svc.update_bot.call_args.kwargs["request_headers"] == {
        "Authorization": "Bearer tok-1"
    }


# ----- round-16 review regressions ------------------------------------------


def test_delete_service_bot_is_rejected(client, svc):
    """R16/F53: service bots are deleted through their publish lifecycle.

    ``BotPublishService.delete_service_bot`` refuses unless the publication is a
    deletable draft with no successful publish, and destroys the verification
    histories first. Generic ``delete_bot`` does none of that, so it would
    remove the source bot, Passport and device while successful publication
    records and verification resources survive.
    """
    svc.get_bot.return_value = {**BOT, "bot_type": "service"}
    resp = client.delete("/openapi/v1/bots/b1")
    assert resp.status_code == 409
    assert resp.json()["code"] == 409000
    svc.delete_bot.assert_not_called()


def test_service_bot_restart_is_still_allowed(client, svc):
    """Only deletion is refused — restart does not touch the publication."""
    svc.get_bot.return_value = {**BOT, "bot_type": "service"}
    _ok(client.post("/openapi/v1/bots/b1/restart"))
    svc.restart_bot.assert_called_once()


def test_personal_bot_delete_is_unaffected(client, svc):
    """The guard must not widen to the type this surface does manage."""
    _ok(client.delete("/openapi/v1/bots/b1"))
    svc.delete_bot.assert_called_once_with("b1", "u1")


# ---------------------------------------------------------------------------
# Startup script (issue #926)
#
# BOT above is teclaw-engined, which is one of the two unsupported cases, so
# supported-path tests override the engine and the binding's provider.
# ---------------------------------------------------------------------------

_SUPPORTED_BOT = {
    **BOT,
    "active_engine": "openclaw",
    "device_binding": {"device_id": "dev-9", "device_provider": "baas"},
}


def _record(script="echo hi", modifier="u1", size=7):
    from datetime import datetime

    r = MagicMock()
    r.script = script
    r.size_bytes = size
    r.modifier = modifier
    r.gmt_modified = datetime(2026, 8, 12, 3, 4, 5)
    return r


def test_startup_script_absent_reads_as_empty_not_an_error(client, svc, startup_script):
    svc.get_bot.return_value = _SUPPORTED_BOT
    startup_script.get.return_value = None

    data = _ok(client.get("/openapi/v1/bots/b1/startup-script"))
    assert data["script"] == ""
    assert data["size_bytes"] == 0
    assert data["updated_at"] is None
    assert data["supported"] is True
    assert data["unsupported_reason"] == ""


def test_startup_script_get_returns_the_stored_body_and_audit(
    client, svc, startup_script
):
    svc.get_bot.return_value = _SUPPORTED_BOT
    startup_script.get.return_value = _record(modifier="alice")

    data = _ok(client.get("/openapi/v1/bots/b1/startup-script"))
    assert data["script"] == "echo hi"
    assert data["updated_by"] == "alice"
    assert data["updated_at"].startswith("2026-08-12")


def test_startup_script_put_stores_and_takes_modifier_from_the_principal(
    client, svc, startup_script
):
    svc.get_bot.return_value = _SUPPORTED_BOT
    startup_script.put.return_value = _record(script="echo new")

    data = _ok(
        client.put("/openapi/v1/bots/b1/startup-script", json={"script": "echo new"})
    )
    assert data["script"] == "echo new"
    # The principal is what gets recorded — never anything from the body.
    assert startup_script.put.call_args.kwargs["modifier"] == "u1"
    assert startup_script.put.call_args.kwargs["script"] == "echo new"


def test_a_put_whose_bot_is_still_there_stores_and_returns_200(
    client, svc, startup_script
):
    """The ordinary path: the re-check finds the same bot and the write stands.

    Two ``get_bot`` calls, not one — the second is the post-write re-check, and
    it costs one read on the write path rather than a lock held across every
    script write.
    """
    svc.get_bot.return_value = _SUPPORTED_BOT
    startup_script.put.return_value = _record(script="echo new")

    data = _ok(
        client.put("/openapi/v1/bots/b1/startup-script", json={"script": "echo new"})
    )

    assert data["script"] == "echo new"
    assert svc.get_bot.call_count == 2
    startup_script.delete.assert_not_called()


def test_a_put_that_loses_the_race_with_deletion_is_withdrawn_and_404s(
    client, svc, startup_script
):
    """A PUT can pass its existence check and then write after the deletion's
    purge has already run, putting a row back for a bot that is gone.

    Nothing will ever execute that row — the unique key means no later bot can
    hold this key — but it is the caller's script text outliving the bot they
    deleted, which is exactly what the pre-delete purge propagates failures to
    avoid. So the write takes itself back, and the caller is told the bot is
    gone rather than that their script was stored on it.
    """
    svc.get_bot.side_effect = [_SUPPORTED_BOT, BotNotFoundError("gone")]
    startup_script.put.return_value = _record(script="echo new")

    resp = client.put("/openapi/v1/bots/b1/startup-script", json={"script": "echo new"})

    assert resp.status_code == 404
    # Unconditional now: the key cannot have changed hands, so the only row
    # that can be here is the one this request just wrote.
    startup_script.delete.assert_called_once_with(entity_id="u1", bot_id="b1")


def test_a_withdrawal_that_cannot_complete_is_not_reported_as_a_clean_404(
    client, svc, startup_script
):
    """404 says the write did not take effect. If the row could not be removed
    that is false, and nothing else is coming for it once the deletion has
    finished — so the failure surfaces instead of being dressed up."""
    svc.get_bot.side_effect = [_SUPPORTED_BOT, BotNotFoundError("gone")]
    startup_script.put.return_value = _record(script="echo new")
    startup_script.delete.side_effect = RuntimeError("db down")

    with pytest.raises(RuntimeError, match="db down"):
        client.put("/openapi/v1/bots/b1/startup-script", json={"script": "echo new"})


def test_the_write_contract_does_not_promise_starts_that_never_recompose(
    client, svc, startup_script
):
    """A targeted device restart and a scale-out reuse the deploy config stored
    at the last compose, so an edit does not reach them.

    The published description has to say so. "Takes effect on the next start"
    read as a promise this feature cannot keep on those two paths, and a caller
    who deleted a script would reasonably believe a device restart removed it.
    """
    # Asserted on the *published* description, not the docstring: that string is
    # what a caller reads, and it is what promised more than the feature does.
    from tests.community.adapters.http.openapi_v1.conftest import public_document

    ops = public_document()["paths"]["/openapi/v1/bots/{bot_id}/startup-script"]

    put_doc = ops["put"]["description"]
    assert "composes" in put_doc
    # The caveat is stated in caller terms; the engine's private restart route
    # must NOT be named — published text carries no internal paths (see
    # test_schema_docs.py).
    assert "scale-out" in put_doc
    assert "/api/" not in put_doc
    assert "next start." not in put_doc

    delete_doc = ops["delete"]["description"]
    assert "composes" in delete_doc and "scale-out" in delete_doc


def test_delete_clears_the_script_at_the_key(client, svc, startup_script):
    """DELETE clears the bot's row outright.

    Unconditional is correct here for the same reason: the key names this bot
    for the life of the data, so there is no other owner whose script could be
    cleared by mistake.
    """
    _ok(client.delete("/openapi/v1/bots/b1/startup-script"))

    startup_script.delete.assert_called_once_with(entity_id="u1", bot_id="b1")


def test_startup_script_put_rejects_an_attempt_to_set_audit_fields(
    client, svc, startup_script
):
    """extra="forbid": a caller asserting updated_by fails validation rather
    than getting a 200 with their value silently dropped."""
    svc.get_bot.return_value = _SUPPORTED_BOT
    resp = client.put(
        "/openapi/v1/bots/b1/startup-script",
        json={"script": "echo new", "updated_by": "attacker"},
    )
    assert resp.status_code == 422, resp.json()
    startup_script.put.assert_not_called()


def test_startup_script_put_is_refused_for_a_teclaw_bot(client, svc, startup_script):
    """BOT is teclaw — provisioned without a start sequence."""
    startup_script.resolve_support.return_value = (
        "unsupported",
        "teclaw bots are provisioned without a start sequence",
    )
    resp = client.put("/openapi/v1/bots/b1/startup-script", json={"script": "echo hi"})
    assert resp.status_code == 409, resp.json()
    startup_script.put.assert_not_called()
    # The surface uses fixed messages (never str(exc)), so the *reason* is
    # discovered from GET rather than from the refusal.
    assert (
        "teclaw"
        in _ok(client.get("/openapi/v1/bots/b1/startup-script"))["unsupported_reason"]
    )


def test_startup_script_get_still_answers_for_an_unsupported_bot(
    client, svc, startup_script
):
    """A caller must be able to discover *why* before attempting a write."""
    startup_script.resolve_support.return_value = (
        "unsupported",
        "teclaw bots are provisioned without a start sequence",
    )
    data = _ok(client.get("/openapi/v1/bots/b1/startup-script"))
    assert data["supported"] is False
    assert "teclaw" in data["unsupported_reason"]


def test_startup_script_put_rejects_an_oversize_body(client, svc, startup_script):
    from agentclaw.community.core.bot_startup_script.services.startup_script_service import (
        MAX_SCRIPT_BYTES,
        StartupScriptTooLargeError,
    )

    svc.get_bot.return_value = _SUPPORTED_BOT
    startup_script.put.side_effect = StartupScriptTooLargeError(MAX_SCRIPT_BYTES + 1)

    resp = client.put("/openapi/v1/bots/b1/startup-script", json={"script": "x"})
    assert resp.status_code == 413, resp.json()
    # The docs promise the 413 names the limit; a bare "too large" would leave
    # a caller bisecting their script to find the permitted size.
    assert str(MAX_SCRIPT_BYTES) in resp.json()["message"]


def test_startup_script_delete_is_idempotent(client, svc, startup_script):
    svc.get_bot.return_value = _SUPPORTED_BOT
    startup_script.delete.return_value = False

    data = _ok(client.delete("/openapi/v1/bots/b1/startup-script"))
    assert data["deleted"] is True


def test_startup_script_requires_ownership(client, svc):
    svc.get_bot.side_effect = BotNotFoundError("nope")
    assert client.get("/openapi/v1/bots/b1/startup-script").status_code == 404
    assert (
        client.put(
            "/openapi/v1/bots/b1/startup-script", json={"script": "x"}
        ).status_code
        == 404
    )
    assert client.delete("/openapi/v1/bots/b1/startup-script").status_code == 404


def test_startup_script_writes_never_touch_a_running_container(
    client, svc, startup_script
):
    """Spec: editing or clearing must not disturb a running container.

    The write path stores a row and stops — no restart, no exec, no publish.
    Asserted against the services that could reach a container.
    """
    svc.get_bot.return_value = _SUPPORTED_BOT
    startup_script.put.return_value = _record(script="echo new")

    client.put("/openapi/v1/bots/b1/startup-script", json={"script": "echo new"})
    client.delete("/openapi/v1/bots/b1/startup-script")

    svc.restart_bot.assert_not_called()


def test_startup_script_get_and_put_agree_about_a_desktop_bot(
    client, svc, startup_script
):
    """DesktopBotService builds its hook by calling ``_get_start_cmd`` directly,
    bypassing ``_build_create_bot_payload`` where the script is resolved — so a
    script stored for a desktop bot would never run.

    Both halves are asserted together on purpose. Gating only the write left GET
    reporting ``supported: true`` for a bot whose next PUT was certain to fail,
    which defeats the discovery path GET exists to provide.
    """
    svc.get_bot.return_value = {**_SUPPORTED_BOT, "bot_type": "desktop"}
    startup_script.resolve_support.return_value = (
        "unsupported",
        "desktop bots build their start command outside the shared sequence",
    )

    resp = client.put("/openapi/v1/bots/b1/startup-script", json={"script": "echo hi"})
    assert resp.status_code == 409, resp.json()
    startup_script.put.assert_not_called()

    data = _ok(client.get("/openapi/v1/bots/b1/startup-script"))
    assert data["supported"] is False
    assert "desktop" in data["unsupported_reason"]


def test_startup_script_audit_names_the_application_not_the_delegating_user():
    """``user_id`` is the *delegating* user for an application caller, which is
    right for scoping and wrong for an audit field: recording it would attribute
    this executable body to someone who did not write it."""
    from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller
    from agentclaw.community.adapters.http.openapi_v1.bots.router import _audit_actor

    human = ActingCaller(user_id="alice", app_id=None)
    assert _audit_actor(human, "alice") == "alice"

    app = ActingCaller(user_id="alice", app_id=7)
    actor = _audit_actor(app, "alice")
    assert actor != "alice", "an app's write must not read as the user's own"
    assert "7" in actor and "alice" in actor, "name the app, keep who it acted for"

