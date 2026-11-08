"""agentswarm CLI smoke tests: --help parse + submit over the HTTP router.

The CLI's ``main`` takes an injected httpx client, so the tests pass a
fastapi.testclient.TestClient wired to the router with a stub service.
Tenant (D25): an explicit per-request API parameter — the submit body
carries ``tenant`` from ``--tenant`` → ``AGENTSWARM_TENANT`` → the spec's
own ``tenant`` key (in that precedence); reads carry ``?tenant=``. Any
conflict between the sources warns on stderr; with no tenant source the
server fails closed at validation and the CLI maps it to rc 1.
"""
from __future__ import annotations

import injector
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.adapters.cli import agentswarm
from agentclaw.community.api.agentswarm_service import (
    AgentSwarmServiceProtocol,
)
from agentclaw.community.adapters.http.agentswarm.router import (
    register_exception_handlers, router,
)
from agentclaw.community.core.agentswarm.repository.audit.impl import AgentSwarmAuditWriter
from agentclaw.community.plugin_api.auth import AuthPlugin, AuthenticatedIdentity
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.service.protocols import (
    RunEvents, SubmitRunRequest,
)


TENANT = "tenant-cli"


class StubService:

    def __init__(self) -> None:
        self.run = dm.Run(
            run_id=dm.RunId("run-cli"),
            tenant=dm.TenantId(TENANT),
            goal="ship it",
            agent_catalog=(dm.AgentSpec(name="a1", role="worker"),),
            provider=dm.ProviderKind("stub"),
            request_metadata=dm.RequestMetadata(),
            status=dm.RunStatus.COMPLETED,
            started_at=dm.EpochMillis(1),
            final_answer="delivered",
        )
        self.submitted: list[SubmitRunRequest] = []

    async def submit_async(self, req: SubmitRunRequest) -> str:
        self.submitted.append(req)
        return self.run.run_id

    async def submit_sync(self, req: SubmitRunRequest) -> dm.Run:
        self.submitted.append(req)
        return self.run

    def get_run(self, run_id: str, tenant: str) -> dm.Run | None:
        if run_id != self.run.run_id or tenant != TENANT:
            return None
        return self.run

    def list_runs(self, tenant: str, status: dm.RunStatus | None = None,
                  *, page: int = 1, page_size: int = 20) -> dm.RunPage:
        return dm.RunPage(items=(self.run,), total=1, page=page, page_size=page_size)

    def run_events(self, run_id: str, tenant: str, after_agent_id: int = 0,
                    after_round_id: int = 0, after_event: int = 0) -> RunEvents:
        return RunEvents(
            agents=(), rounds=(), guards=(),
            run_status=self.run.status,
            terminal=None if self.run.status is dm.RunStatus.RUNNING
            else self.run.status,
        )

    async def cancel(self, run_id: str, tenant: str) -> str:
        return "not_found"


class AuthenticatedAuth:
    """Every request resolves an authenticated end-user; the tenant is an
    explicit API parameter, never taken from the principal."""

    async def resolve_user_from_request(self, ctx) -> AuthenticatedIdentity:
        return AuthenticatedIdentity(
            id="cli-user", operatorName="cli-user", outUserNo="cli-staff",
            tntInstId="irrelevant",
        )

    async def get_login_user(self, cookie: str, referer: str | None = None):
        raise NotImplementedError

    def is_operator_allowed(self, staff_id: str) -> bool:
        return True

    async def authorize_entity_access(self, ctx, requested_entity_id,
                                       requested_entity_type):
        return requested_entity_id, requested_entity_type


class RecordingAuditWriter:

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record(self, *, tenant, actor, action, reason=None, run_id=None,
               source="http") -> None:
        self.rows.append({"tenant": tenant, "actor": actor})


@pytest.fixture()
def stub() -> StubService:
    return StubService()


@pytest.fixture()
def client(stub) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    register_exception_handlers(app)
    inj = injector.Injector()
    inj.binder.bind(AgentSwarmServiceProtocol, to=stub)
    inj.binder.bind(AuthPlugin, to=AuthenticatedAuth())
    inj.binder.bind(AgentSwarmAuditWriter, to=RecordingAuditWriter())
    app.state.injector = inj
    return TestClient(app)


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        agentswarm.build_parser().parse_args(["--help"])
    assert exc.value.code == 0
    assert "agentswarm" in capsys.readouterr().out


def test_submit_waits_and_prints_report(client, stub, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv(agentswarm.TENANT_ENV_VAR, TENANT)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps({
        "goal": "ship it",
        "agents": [{"name": "a1", "role": "worker"}],
        "idempotency_key": "cli-1",
    }), encoding="utf-8")
    rc = agentswarm.main(
        ["submit", "--spec", str(spec_path), "--wait", "--interval", "0.01"],
        client=client,
    )
    assert rc == 0
    (req,) = stub.submitted
    assert req.tenant == TENANT
    assert req.idempotency_key == "cli-1"
    assert req.agents[0].name == "a1"
    out = capsys.readouterr().out
    assert "run_id: run-cli" in out
    assert "status: COMPLETED" in out
    assert "Run Report" in out


def test_submit_tenant_flag_beats_env(client, stub, tmp_path, capsys, monkeypatch):
    """--tenant wins over AGENTSWARM_TENANT for the body field."""
    monkeypatch.setenv(agentswarm.TENANT_ENV_VAR, "tenant-env")
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps({
        "goal": "ship it",
        "agents": [{"name": "a1", "role": "worker"}],
    }), encoding="utf-8")
    rc = agentswarm.main(
        ["submit", "--spec", str(spec_path), "--tenant", TENANT],
        client=client,
    )
    assert rc == 0
    (req,) = stub.submitted
    assert req.tenant == TENANT


def test_spec_tenant_used_when_no_override(client, stub, tmp_path, capsys):
    """The spec's own tenant is a valid default when no flag/env names one."""
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps({
        "tenant": TENANT,
        "goal": "ship it",
        "agents": [{"name": "a1", "role": "worker"}],
    }), encoding="utf-8")
    rc = agentswarm.main(
        ["submit", "--spec", str(spec_path)],
        client=client,
    )
    assert rc == 0
    (req,) = stub.submitted
    assert req.tenant == TENANT
    assert capsys.readouterr().err == ""


def test_spec_tenant_conflict_with_env_warns(client, stub, tmp_path, capsys,
                                              monkeypatch):
    """An AGENTSWARM_TENANT that disagrees with the spec tenant warns and wins."""
    monkeypatch.setenv(agentswarm.TENANT_ENV_VAR, TENANT)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps({
        "tenant": "tenant-spec",
        "goal": "ship it",
        "agents": [{"name": "a1", "role": "worker"}],
    }), encoding="utf-8")
    capsys.readouterr()
    rc = agentswarm.main(
        ["submit", "--spec", str(spec_path)],
        client=client,
    )
    assert rc == 0
    err = capsys.readouterr().err
    assert "overridden by" in err
    assert "tenant-spec" in err
    (req,) = stub.submitted
    assert req.tenant == TENANT


def test_submit_missing_spec_fields_exits_with_message(tmp_path):
    spec_path = tmp_path / "bad.json"
    spec_path.write_text(json.dumps({"tenant": TENANT}), encoding="utf-8")
    with pytest.raises(SystemExit, match="agents"):
        agentswarm.main(
            ["submit", "--spec", str(spec_path)], client=TestClient(FastAPI())
        )


def test_submit_unresolved_tenant_maps_server_422_to_rc1(
        client, stub, tmp_path, capsys, monkeypatch):
    """No tenant anywhere: the server fails closed at validation (422) and
    the CLI surfaces it as rc 1 with the server's message."""
    monkeypatch.delenv(agentswarm.TENANT_ENV_VAR, raising=False)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps({
        "goal": "ship it",
        "agents": [{"name": "a1", "role": "worker"}],
    }), encoding="utf-8")
    rc = agentswarm.main(["submit", "--spec", str(spec_path)], client=client)
    assert rc == 1
    assert stub.submitted == []
    assert "422" in capsys.readouterr().err