"""agentcompute HTTP adapter tests: routes, DTO conversion, error mapping.

Drives the router through fastapi.testclient.TestClient with a stub
service injected on ``app.state`` — no database, no network.

Stage B wiring: every route authenticates through a stub ``AuthPlugin``
bound on the test injector, so the tenant comes from the principal's
``tenantId``; ``X-Tenant-Id`` is only the fallback tested explicitly.
"""
from __future__ import annotations

import injector
import json
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.agentcompute.router import (
    register_exception_handlers, router,
)
from agentclaw.community.core.agentcompute.repository.audit.impl import (
    AgentComputeAuditWriter, AuditAction,
)
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.plugin_api.auth import AuthPlugin, AuthenticatedIdentity
from agentclaw.community.core.agentcompute.domain import models as dm
from agentclaw.community.core.agentcompute.service.protocols import (
    AgentComputeConfigError, AgentEvent, GuardEventSummary, RoundEvent,
    RunEvents, SubmitRunRequest, TenantRunLimitExceeded,
)
from agentclaw.community.api.agentcompute_service import (
    AgentComputeServiceProtocol,
)


TENANT = "tenant-a"
STAFF_ID = "staff-1"


def _mk_run(status: dm.RunStatus = dm.RunStatus.COMPLETED) -> dm.Run:
    return dm.Run(
        run_id=dm.RunId("run-1"),
        tenant=dm.TenantId(TENANT),
        goal="analyze the build logs",
        agent_catalog=(dm.AgentSpec(name="r1", role="research"),),
        provider=dm.ProviderKind("stub"),
        request_metadata=dm.RequestMetadata(),
        status=status,
        started_at=dm.EpochMillis(1),
        ended_at=dm.EpochMillis(2),
        final_answer="the build broke in stage 3",
    )


def _mk_events(status: dm.RunStatus = dm.RunStatus.RUNNING) -> RunEvents:
    return RunEvents(
        agents=(
            AgentEvent(
                agent_id="agent-1", name="r1", status=dm.AgentStatus.RUNNING,
                kind=dm.AgentKind.LLM, spawn_round=0, row_cursor=1,
            ),
        ),
        rounds=(RoundEvent(round_id="round-1", stage_index=0,
                           cohort_size=1, row_cursor=1),),
        guards=(GuardEventSummary(kind=dm.GuardKind.DEDUPE_REJECT, row_cursor=1),),
        run_status=status,
        terminal=None if status is dm.RunStatus.RUNNING else status,
    )


class StubAgentComputeService:

    def __init__(self) -> None:
        self.run = _mk_run()
        self.events: RunEvents | None = None
        self.run_events_calls: list[dict] = []
        self.submitted: list[SubmitRunRequest] = []
        self.calls: list[str] = []
        self.list_calls: list[dict] = []
        self.cancelled: list[tuple[str, str]] = []
        self.active_counts: dict[str, int] = {}
        self.submit_error: Exception | None = None

    async def submit_async(self, req: SubmitRunRequest) -> str:
        if self.submit_error is not None:
            raise self.submit_error
        self.submitted.append(req)
        self.calls.append("submit_async")
        return self.run.run_id

    async def submit_sync(self, req: SubmitRunRequest) -> dm.Run:
        if self.submit_error is not None:
            raise self.submit_error
        self.submitted.append(req)
        self.calls.append("submit_sync")
        return self.run

    def get_run(self, run_id: str, tenant: str) -> dm.Run | None:
        if run_id != self.run.run_id or tenant != TENANT:
            return None
        return self.run

    def list_runs(self, tenant: str, status: dm.RunStatus | None = None,
                  *, page: int = 1, page_size: int = 20) -> dm.RunPage:
        self.list_calls.append(
            {"tenant": tenant, "status": status, "page": page, "page_size": page_size}
        )
        return dm.RunPage(items=(self.run,), total=1, page=page, page_size=page_size)

    def run_events(self, run_id: str, tenant: str, after_agent_id: int = 0,
                   after_round_id: int = 0, after_event: int = 0) -> RunEvents:
        self.run_events_calls.append({
            "run_id": run_id, "tenant": tenant,
            "after_agent_id": after_agent_id,
            "after_round_id": after_round_id,
            "after_event": after_event,
        })
        if self.events is not None:
            return self.events
        return RunEvents(
            agents=(), rounds=(), guards=(),
            run_status=self.run.status,
            terminal=None if self.run.status is dm.RunStatus.RUNNING else self.run.status,
        )

    async def cancel(self, run_id: str, tenant: str) -> str:
        self.cancelled.append((run_id, tenant))
        if run_id != self.run.run_id or tenant != TENANT:
            return "not_found"
        return self.run.status.value


class StubAuthPlugin:
    """AuthPlugin double — resolve_user_from_request returns a mutable tenant."""

    def __init__(self, tenant_id: str | None) -> None:
        self.tenant_id = tenant_id

    async def resolve_user_from_request(self, ctx) -> AuthenticatedIdentity:
        return AuthenticatedIdentity(
            id="auth-user-1",
            operatorName="auth-user-1",
            outUserNo=STAFF_ID,
            nickName="Auth User",
            tntInstId=self.tenant_id,
        )

    async def get_login_user(self, cookie: str, referer: str | None = None):
        raise NotImplementedError("not exercised by the agentcompute router")

    def is_operator_allowed(self, staff_id: str) -> bool:
        return True

    async def authorize_entity_access(self, ctx, requested_entity_id,
                                       requested_entity_type):
        return requested_entity_id, requested_entity_type


class RecordingAuditWriter:

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record(self, *, tenant: str, actor: str, action: AuditAction,
               reason: str | None = None, run_id: str | None = None,
               source: str = "http") -> None:
        self.rows.append({
            "tenant": tenant, "actor": actor, "action": action.value,
            "reason": reason, "run_id": run_id, "source": source,
        })


@pytest.fixture()
def stub() -> StubAgentComputeService:
    return StubAgentComputeService()


@pytest.fixture()
def auth() -> StubAuthPlugin:
    return StubAuthPlugin(tenant_id=TENANT)


@pytest.fixture()
def audit() -> RecordingAuditWriter:
    return RecordingAuditWriter()


@pytest.fixture()
def client(stub, auth, audit) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    register_exception_handlers(app)
    inj = injector.Injector()
    inj.binder.bind(AgentComputeServiceProtocol, to=stub)
    inj.binder.bind(AuthPlugin, to=auth)
    inj.binder.bind(AgentComputeAuditWriter, to=audit)
    app.state.injector = inj
    return TestClient(app)


def _spec_body() -> dict:
    return {
        "goal": "analyze the build logs",
        "agents": [
            {
                "name": "r1",
                "role": "research",
                "instructions": "scan logs",
                "kind": "llm",
                "model": "m1",
                "metadata": {"tier": "1"},
            }
        ],
        "provider": "stub",
        "model": "orchestrator-x",
        "idempotency_key": "idem-1",
    }


def _read_sse(resp) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    event_name = None
    for line in resp.iter_lines():
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            events.append((event_name, json.loads(line.split(":", 1)[1])))
    return events


class TestTenantResolution:

    def test_principal_tenant_used_without_header_or_body_tenant(self, client, stub):
        resp = client.post("/agentcompute/v1/runs", json=_spec_body())
        assert resp.status_code == 200
        (req,) = stub.submitted
        assert req.tenant == TENANT

    def test_header_fallback_used_when_principal_has_no_tenant(self, client, stub, auth):
        auth.tenant_id = None
        resp = client.post(
            "/agentcompute/v1/runs", json=_spec_body(), headers={"X-Tenant-Id": TENANT}
        )
        assert resp.status_code == 200
        (req,) = stub.submitted
        assert req.tenant == TENANT

    def test_no_tenant_in_principal_or_header_is_precise_400(self, client, auth):
        auth.tenant_id = None
        resp = client.post("/agentcompute/v1/runs", json=_spec_body())
        assert resp.status_code == 400
        assert resp.json()["detail"] == (
            "tenant unresolvable: authenticate or set X-Tenant-Id"
        )
        # same for the per-run routes
        assert client.get("/agentcompute/v1/runs/run-1").status_code == 400
        assert client.get("/agentcompute/v1/runs").status_code == 400
        assert client.post("/agentcompute/v1/runs/run-1/cancel").status_code == 400
        assert client.get("/agentcompute/v1/runs/run-1/events").status_code == 400
        assert client.get("/agentcompute/v1/runs/run-1/report").status_code == 400

    def test_header_cannot_override_principal_tenant(self, client, stub, auth):
        resp = client.post(
            "/agentcompute/v1/runs",
            json=_spec_body(),
            headers={"X-Tenant-Id": "tenant-b"},
        )
        assert resp.status_code == 200
        assert stub.submitted[0].tenant == TENANT

    def test_legacy_body_tenant_key_is_ignored(self, client, stub):
        resp = client.post(
            "/agentcompute/v1/runs", json={**_spec_body(), "tenant": "tenant-b"}
        )
        assert resp.status_code == 200
        assert stub.submitted[0].tenant == TENANT

    def test_reads_use_principal_tenant_without_header(self, client, stub):
        assert client.get("/agentcompute/v1/runs/run-1").status_code == 200
        assert client.get("/agentcompute/v1/runs").status_code == 200
        assert stub.list_calls[0]["tenant"] == TENANT


class TestCrossTenantIsolation:

    def test_cross_tenant_and_unknown_run_404s_indistinguishable(self, client, auth):
        # caller's principal is tenant-b; a spoofed header claims the owning
        # tenant — header ignored, data layer scopes to tenant-b → 404
        auth.tenant_id = "tenant-b"
        spoofs_cross = client.get(
            "/agentcompute/v1/runs/run-1", headers={"X-Tenant-Id": TENANT}
        )

        # same run, genuinely other tenant, no spoofing
        auth.tenant_id = "tenant-b"
        cross = client.get("/agentcompute/v1/runs/run-1", headers={})

        # a run that exists for the caller's tenant, but wrong id
        auth.tenant_id = TENANT
        unknown = client.get("/agentcompute/v1/runs/nope")

        assert spoofs_cross.status_code == 404
        assert cross.status_code == 404
        assert unknown.status_code == 404
        assert spoofs_cross.json() == cross.json() == unknown.json()

    def test_submit_limit_blocks_only_the_principal_tenant(self, client, stub, auth):
        def submit_for(tenant: str) -> int:
            auth.tenant_id = tenant
            stub.submit_error = (
                TenantRunLimitExceeded(tenant=tenant, limit=8)
                if tenant == TENANT
                else None
            )
            return client.post("/agentcompute/v1/runs", json=_spec_body()).status_code

        assert submit_for(TENANT) == 429
        assert submit_for("tenant-b") == 200
        assert stub.submitted[-1].tenant == "tenant-b"


class TestSyncRunRoundTrip:

    def test_submit_sync_converts_dto_to_typed_request(self, client, stub):
        resp = client.post("/agentcompute/v1/runs", json=_spec_body())
        assert resp.status_code == 200
        (req,) = stub.submitted
        assert req.tenant == TENANT
        assert req.goal == "analyze the build logs"
        assert req.provider == "stub"
        assert req.model == "orchestrator-x"
        assert req.idempotency_key == "idem-1"
        (agent,) = req.agents
        assert isinstance(agent, dm.AgentSpec)
        assert agent.name == "r1"
        assert agent.role == "research"
        assert agent.kind is dm.AgentKind.LLM
        assert agent.model == "m1"
        assert agent.metadata == {"tier": "1"}
        body = resp.json()
        assert body["run_id"] == "run-1"
        assert stub.calls == ["submit_async"]

    def test_wait_true_uses_sync_path_and_returns_summary(self, client, stub):
        resp = client.post(
            "/agentcompute/v1/runs", params={"wait": "true"}, json=_spec_body()
        )
        assert resp.status_code == 200
        assert stub.calls == ["submit_sync"]
        body = resp.json()
        assert body["run_id"] == "run-1"
        assert body["status"] == "COMPLETED"
        assert body["final_answer"] == "the build broke in stage 3"

    def test_wait_false_defaults_to_async(self, client, stub):
        resp = client.post(
            "/agentcompute/v1/runs", params={"wait": "false"}, json=_spec_body()
        )
        assert resp.status_code == 200
        assert stub.calls == ["submit_async"]
        assert resp.json()["run_id"] == "run-1"


class TestListRuns:

    def test_pagination_and_status_filter_passthrough(self, client, stub):
        resp = client.get(
            "/agentcompute/v1/runs",
            params={"page": 2, "page_size": 50, "status": "COMPLETED"},
        )
        assert resp.status_code == 200
        (call,) = stub.list_calls
        assert call == {
            "tenant": TENANT,
            "status": dm.RunStatus.COMPLETED,
            "page": 2,
            "page_size": 50,
        }
        body = resp.json()
        assert body["total"] == 1
        assert body["page"] == 2
        assert body["page_size"] == 50
        (item,) = body["items"]
        assert item["run_id"] == "run-1"

    def test_invalid_status_is_422(self, client, stub):
        resp = client.get(
            "/agentcompute/v1/runs",
            headers={"X-Tenant-Id": TENANT},
            params={"status": "WARPED"},
        )
        assert resp.status_code == 422
        assert stub.list_calls == []

    def test_page_zero_and_oversized_page_size_are_422(self, client, stub):
        assert client.get("/agentcompute/v1/runs", params={"page": 0}).status_code == 422
        assert client.get(
            "/agentcompute/v1/runs", params={"page_size": 200}
        ).status_code == 422


class TestCancel:

    def test_known_run_returns_status(self, client, stub):
        resp = client.post("/agentcompute/v1/runs/run-1/cancel")
        assert resp.status_code == 200
        assert resp.json() == {"run_id": "run-1", "status": "COMPLETED"}

    def test_unknown_run_is_404(self, client, stub):
        resp = client.post("/agentcompute/v1/runs/nope/cancel")
        assert resp.status_code == 404

    def test_cross_tenant_cancel_is_404(self, client, stub, auth):
        auth.tenant_id = "tenant-b"
        resp = client.post(
            "/agentcompute/v1/runs/run-1/cancel", headers={"X-Tenant-Id": TENANT}
        )
        assert resp.status_code == 404
        assert stub.cancelled == [("run-1", "tenant-b")]


class TestSmokeRoutes:

    def test_health_reports_version(self, client):
        assert client.get("/agentcompute/v1/health").json() == {
            "status": "ok", "version": "v1",
        }
        assert client.get("/agentcompute/v1/ready").json() == {"ready": True}

    def test_report_renders_html(self, client):
        resp = client.get("/agentcompute/v1/runs/run-1/report")
        assert resp.status_code == 200
        assert "run-1" in resp.text
        assert "COMPLETED" in resp.text

    def test_unknown_run_report_is_404(self, client):
        assert client.get("/agentcompute/v1/runs/nope/report").status_code == 404

    def test_stream_emits_event_stream(self, client):
        with client.stream("GET", "/agentcompute/v1/runs/run-1/events") as resp:
            assert resp.headers["content-type"].startswith("text/event-stream")
            first = next(resp.iter_lines())
        assert first.startswith("event:")

    def test_stream_unknown_run_emits_error_event(self, client):
        with client.stream("GET", "/agentcompute/v1/runs/nope/events") as resp:
            events = _read_sse(resp)
        assert events == [("error", {"error": "not found"})]

    def test_stream_emits_durable_agent_round_guard_events(self, client, stub):
        stub.events = _mk_events(status=dm.RunStatus.COMPLETED)
        stub.run = _mk_run(status=dm.RunStatus.COMPLETED)
        with client.stream("GET", "/agentcompute/v1/runs/run-1/events") as resp:
            events = _read_sse(resp)
        assert events[0][0] == "status"
        assert events[0][1]["data"] == {"status": "COMPLETED"}
        agent_payloads = [p["data"] for name, p in events if name == "agent"]
        assert agent_payloads == [{
            "agent_id": "agent-1", "name": "r1", "status": "RUNNING",
            "kind": "llm", "spawn_round": 0,
        }]
        round_payloads = [p["data"] for name, p in events if name == "round"]
        assert round_payloads == [{
            "round_id": "round-1", "stage_index": 0, "cohort_size": 1,
        }]
        guard_payloads = [p["data"] for name, p in events if name == "guard"]
        assert guard_payloads == [{"kind": "dedupe_reject"}]
        assert events[-1][0] == "terminal"
        assert events[-1][1]["data"] == {"status": "COMPLETED"}
        assert stub.run_events_calls[0]["after_agent_id"] == 0
        assert stub.run_events_calls[0]["after_round_id"] == 0
        assert stub.run_events_calls[0]["after_event"] == 0

    def test_stream_terminal_immediately_when_run_already_done(self, client, stub):
        with client.stream("GET", "/agentcompute/v1/runs/run-1/events") as resp:
            events = _read_sse(resp)
        assert [name for name, _ in events] == ["status", "terminal"]
        assert events[-1][1]["data"] == {"status": "COMPLETED"}
        assert stub.run_events_calls[0]["run_id"] == "run-1"
        assert stub.run_events_calls[0]["tenant"] == TENANT


class TestErrorMapping:

    def test_tenant_limit_exceeded_maps_to_429(self, client, stub):
        stub.submit_error = TenantRunLimitExceeded(tenant=TENANT, limit=8)
        resp = client.post("/agentcompute/v1/runs", json=_spec_body())
        assert resp.status_code == 429
        assert resp.json()["error"] == "tenant_run_limit_exceeded"
        assert resp.json()["tenant"] == TENANT
        assert resp.json()["limit"] == 8

    def test_config_error_maps_to_400(self, client, stub):
        stub.submit_error = AgentComputeConfigError(
            "agentcompute llm config missing: base_url/api_key required"
        )
        resp = client.post("/agentcompute/v1/runs", json=_spec_body())
        assert resp.status_code == 400
        assert "llm config missing" in resp.json()["error"]


class TestAuthWiring:

    def test_unauthenticated_caller_gets_fastapi_401_from_auth_dep(
            self, stub, audit):
        from agentclaw.community.core.errors import Unauthorized

        class RejectingAuth(StubAuthPlugin):
            async def resolve_user_from_request(self, ctx):
                raise Unauthorized("no identity")

        app = FastAPI()
        app.include_router(router)
        register_exception_handlers(app)
        # 401 translation for DomainError family is owned by the composed app
        # (adapters/http/app.py _DOMAIN_ERROR_STATUS_MAP); mirror it locally so
        # this bare-app fixture asserts the route contract, not the map wiring.
        app.add_exception_handler(
            Unauthorized,
            lambda request, exc: JSONResponse(
                {"detail": str(exc)}, status_code=401),
        )
        inj = injector.Injector()
        inj.binder.bind(AgentComputeServiceProtocol, to=stub)
        inj.binder.bind(AuthPlugin, to=RejectingAuth(tenant_id=None))
        inj.binder.bind(AgentComputeAuditWriter, to=audit)
        app.state.injector = inj
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/agentcompute/v1/runs", json=_spec_body())
        assert resp.status_code == 401
        assert stub.submitted == []

    def test_authenticated_user_shape_reaches_routes(self):
        # the adapter-level identity DTO must keep carrying tenantId (D25 seam)
        user = AuthenticatedUser(
            id="u", staffId="s", operatorName="o", nickName="n", tenantId="t"
        )
        assert user.tenantId == "t"