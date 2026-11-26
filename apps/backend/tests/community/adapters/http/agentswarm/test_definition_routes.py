"""The definition routes: status codes, ownership, and the audit trail.

Drives the router through a stub service, so these tests assert the *contract*
(what a caller sees) rather than storage behaviour, which the service and
repository suites already cover.
"""
from __future__ import annotations

import injector
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.agentswarm.router import (
    register_exception_handlers, router,
)
from agentclaw.community.api.agentswarm_service import AgentSwarmServiceProtocol
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository.audit.impl import (
    AgentSwarmAuditWriter, AuditAction,
)
from agentclaw.community.core.agentswarm.service.protocols import (
    AgentSwarmRunNotFoundError, DefinitionManifestInvalid, DefinitionNameConflict,
    DefinitionRowConflict,
)
from agentclaw.community.plugin_api.auth import AuthPlugin, AuthenticatedIdentity

TENANT = "teamclaw"
ME = "49912"


class _Auth:
    async def resolve_user_from_request(self, ctx) -> AuthenticatedIdentity:
        return AuthenticatedIdentity(
            id="u", operatorName="u", outUserNo=ME, nickName="me",
            tntInstId=TENANT,
        )

    async def get_login_user(self, cookie, referer=None):
        raise NotImplementedError

    def is_operator_allowed(self, staff_id: str) -> bool:
        return True

    async def authorize_entity_access(self, ctx, requested_entity_id,
                                      requested_entity_type):
        return requested_entity_id, requested_entity_type


class _Audit:
    def __init__(self):
        self.rows: list[dict] = []

    def record(self, *, tenant, actor, action, reason=None, run_id=None,
               source="http"):
        self.rows.append({"action": action.value, "actor": actor})


def _defn(role="v1", row_id=101):
    return dm.AgentDefinition(
        definition_id=dm.AgentDefinitionId("def-1"), tenant=dm.TenantId(TENANT),
        owner_id=ME, creator_id=ME, modifier_id=ME, name="reviewer",
        role=role, kind=dm.AgentKind.BOT, engine="openclaw",
        manifest_document="", manifest_digest="sha256:" + "a" * 64,
        row_id=row_id,
    )


class _Stub:
    def __init__(self):
        self.create_error = None
        self.update_error = None
        self.live = None
        self.versions = []
        self.submit_error = None
        self.submitted = []

    async def submit_async(self, req):
        if self.submit_error:
            raise self.submit_error
        self.submitted.append(req)
        return "run-1"

    def create_definition(self, **kw):
        if self.create_error:
            raise self.create_error
        return _defn()

    def get_definition(self, definition_id, tenant, owner_id):
        return self.live

    def list_definitions(self, tenant, owner_id, name_filter=None, page=1,
                         page_size=20):
        return dm.AgentDefinitionPage(
            items=(self.live,) if self.live else (), total=1 if self.live else 0,
            page=page, page_size=page_size,
        )

    def list_definition_versions(self, definition_id, tenant, owner_id):
        return self.versions

    def update_definition(self, **kw):
        if self.update_error:
            raise self.update_error
        return _defn(role="v2", row_id=102)

    def delete_definition(self, definition_id, tenant, owner_id):
        return 1


@pytest.fixture()
def stub():
    return _Stub()


@pytest.fixture()
def audit():
    return _Audit()


@pytest.fixture()
def client(stub, audit):
    app = FastAPI()
    app.include_router(router)
    register_exception_handlers(app)
    inj = injector.Injector()
    inj.binder.bind(AgentSwarmServiceProtocol, to=stub)
    inj.binder.bind(AuthPlugin, to=_Auth())
    inj.binder.bind(AgentSwarmAuditWriter, to=audit)
    app.state.injector = inj
    return TestClient(app)


_BASE = "/openapi/v1/agentswarm/agent/definitions"


def _url(path=""):
    return f"{_BASE}{path}?tenant={TENANT}"


# ── create ──────────────────────────────────────────────────────────────────

def test_create_returns_201_and_the_actor_fields(client):
    r = client.post(_url("/add"), json={"name": "reviewer", "role": "v1"})
    assert r.status_code == 201
    body = r.json()
    assert body["owner_id"] == ME and body["creator_id"] == ME
    assert body["modifier_id"] == ME


def test_name_conflict_is_409(client, stub, audit):
    stub.create_error = DefinitionNameConflict("reviewer")
    r = client.post(_url("/add"), json={"name": "reviewer"})
    assert r.status_code == 409
    assert any(row["action"] == "definition_name_conflict" for row in audit.rows)


def test_invalid_manifest_is_422(client, stub):
    stub.create_error = DefinitionManifestInvalid("bad yaml")
    r = client.post(_url("/add"), json={"name": "reviewer", "manifest_document": "x"})
    assert r.status_code == 422
    assert "bad yaml" in r.json()["detail"]


def test_a_request_supplying_owner_is_rejected(client):
    # owner_id is derived from the session, never accepted from the body.
    r = client.post(_url("/add"), json={"name": "reviewer", "owner_id": "99999"})
    assert r.status_code == 422


# ── read ────────────────────────────────────────────────────────────────────

def test_absent_definition_is_404(client):
    assert client.post(_url("/get"), json={"definition_id": "def-1"}).status_code == 404


def test_live_definition_is_served_with_its_actor_fields(client, stub):
    stub.live = _defn()
    r = client.post(_url("/get"), json={"definition_id": "def-1"})
    assert r.status_code == 200
    body = r.json()
    assert body["definition_id"] == "def-1"
    assert body["manifest_digest"].startswith("sha256:")
    # owner / author / editor are all reported, and are the caller's here.
    assert body["owner_id"] == ME
    assert body["creator_id"] == ME and body["modifier_id"] == ME


def test_versions_404_when_none_visible(client):
    assert client.post(_url("/versions"), json={"definition_id": "def-1"}).status_code == 404


def test_versions_never_carry_retired_bodies(client, stub):
    stub.versions = [_defn(role="v2", row_id=102), _defn(role="v1", row_id=101)]
    r = client.post(_url("/versions"), json={"definition_id": "def-1"})
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] is False
    assert {v["definition_row_id"] for v in body["versions"]} == {101, 102}
    # A version entry carries a digest, never a manifest document.
    assert all("manifest_document" not in v for v in body["versions"])
    assert all(
        v["manifest_digest"].startswith("sha256:") for v in body["versions"]
    )


def test_versions_report_a_deleted_definition_explicitly(client, stub):
    """Deleted is stated, not inferred from every entry being non-live."""
    import dataclasses

    retired = dataclasses.replace(_defn(role="v1", row_id=101), is_live=False)
    stub.versions = [retired]

    r = client.post(_url("/versions"), json={"definition_id": "def-1"})
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] is True
    # The history itself is still served.
    assert body["versions"][0]["definition_row_id"] == 101


def test_versions_of_a_live_definition_are_not_deleted(client, stub):
    stub.versions = [_defn(role="v1", row_id=101)]
    body = client.post(_url("/versions"), json={"definition_id": "def-1"}).json()
    assert body["deleted"] is False



# ── revise / delete ─────────────────────────────────────────────────────────

def test_stale_revise_is_409(client, stub, audit):
    stub.update_error = DefinitionRowConflict(101)
    r = client.post(_url("/update"), json={
        "definition_id": "def-1", "name": "reviewer", "definition_row_id": 101,
    })
    assert r.status_code == 409
    assert any(row["action"] == "definition_row_conflict" for row in audit.rows)


def test_revise_of_absent_definition_is_404(client, stub):
    stub.update_error = AgentSwarmRunNotFoundError("def-1")
    r = client.post(_url("/update"), json={
        "definition_id": "def-1", "name": "reviewer", "definition_row_id": 101,
    })
    assert r.status_code == 404


def test_delete_reports_retired_count(client):
    r = client.post(_url("/delete"), json={"definition_id": "def-1"})
    assert r.status_code == 200 and r.json() == {"retired": 1}


def test_revise_requires_the_row_id(client):
    r = client.post(_url("/update"), json={"definition_id": "def-1", "name": "reviewer"})
    assert r.status_code == 422


# ── OpenAPI ─────────────────────────────────────────────────────────────────

def test_openapi_lists_all_six_definition_operations(client):
    """Every definition route is POST; the path names the action."""
    spec = client.get("/openapi.json").json()["paths"]
    defs = {
        p: sorted(m for m in ops if m in {"get", "post", "put", "delete"})
        for p, ops in spec.items()
        if "/agent/definitions" in p
    }
    assert defs == {
        f"{_BASE}/add": ["post"],
        f"{_BASE}/list": ["post"],
        f"{_BASE}/get": ["post"],
        f"{_BASE}/versions": ["post"],
        f"{_BASE}/update": ["post"],
        f"{_BASE}/delete": ["post"],
    }


# ── revise success path ─────────────────────────────────────────────────────

def test_revise_returns_the_new_version(client, stub, audit):
    stub.live = _defn(role="v1", row_id=101)
    r = client.post(_url("/update"), json={
        "definition_id": "def-1", "name": "reviewer", "role": "v2",
        "definition_row_id": 101,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["definition_row_id"] == 102 and body["role"] == "v2"
    assert any(row["action"] == "definition_revised" for row in audit.rows)


def test_revise_with_an_invalid_manifest_is_422(client, stub, audit):
    stub.update_error = DefinitionManifestInvalid("bad yaml")
    r = client.post(_url("/update"), json={
        "definition_id": "def-1", "name": "reviewer", "definition_row_id": 101,
    })
    assert r.status_code == 422
    assert any(
        row["action"] == "definition_manifest_rejected" for row in audit.rows
    )


def test_revise_renaming_onto_a_live_name_is_409(client, stub):
    stub.update_error = DefinitionNameConflict("taken")
    r = client.post(_url("/update"), json={
        "definition_id": "def-1", "name": "taken", "definition_row_id": 101,
    })
    assert r.status_code == 409


def test_revise_with_an_over_long_role_is_422(client, stub):
    stub.update_error = ValueError("role is 900 characters, over the 512 limit")
    r = client.post(_url("/update"), json={
        "definition_id": "def-1", "name": "reviewer", "definition_row_id": 101,
    })
    assert r.status_code == 422


def test_delete_of_an_absent_definition_is_404(client, stub):
    stub.delete_definition = lambda *a, **kw: 0
    assert client.post(_url("/delete"), json={"definition_id": "def-1"}).status_code == 404


# ── listing ─────────────────────────────────────────────────────────────────

def test_listing_returns_a_page_envelope(client, stub):
    stub.live = _defn()
    r = client.post(_url("/list"), json={})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1 and len(body["items"]) == 1
    assert body["items"][0]["name"] == "reviewer"


def test_create_with_an_over_long_role_is_422(client, stub):
    stub.create_error = ValueError("role too long")
    r = client.post(_url("/add"), json={"name": "reviewer"})
    assert r.status_code == 422


# ── submit refusal on an unresolvable reference ─────────────────────────────

def test_submit_with_an_unresolvable_reference_is_422(client, stub, audit):
    from agentclaw.community.core.agentswarm.service.protocols import (
        AgentReferenceUnresolved,
    )

    stub.submit_error = AgentReferenceUnresolved("def-missing")
    r = client.post(
        f"/openapi/v1/agentswarm/runs?tenant={TENANT}",
        json={
            "tenant": TENANT,
            "goal": "g",
            "agent_refs": [{"definition_id": "def-missing"}],
        },
    )
    assert r.status_code == 422
    assert "def-missing" in r.json()["detail"]
    # The refusal is audited as a rejected submit, with a reason that names it.
    assert any(
        row["action"] == "submit_rejected" for row in audit.rows
    )


def test_submit_with_both_roster_forms_is_422(client):
    r = client.post(
        f"/openapi/v1/agentswarm/runs?tenant={TENANT}",
        json={
            "tenant": TENANT,
            "goal": "g",
            "agents": [{"name": "a", "role": "r"}],
            "agent_refs": [{"definition_id": "d1"}],
        },
    )
    assert r.status_code == 422
