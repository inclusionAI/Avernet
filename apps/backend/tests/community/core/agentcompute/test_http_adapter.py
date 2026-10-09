"""agentcompute HTTP adapter tests: routes, DTO conversion, error mapping.

Drives the router through fastapi.testclient.TestClient with a stub
service injected on ``app.state`` — no database, no network.
"""
from __future__ import annotations

import injector
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.agentcompute.router import (
    register_exception_handlers, router,
)
from agentclaw.community.core.agentcompute.domain import models as dm
from agentclaw.community.core.agentcompute.service_protocol import (
    AgentComputeConfigError, AgentEvent, GuardEventSummary, RoundEvent,
    RunEvents, SubmitRunRequest, TenantRunLimitExceeded,
)
from agentclaw.community.api.agentcompute_service import (
    AgentComputeServiceProtocol,
)


TENANT = "tenant-a"


def _mk_run(status: dm.RunStatus = dm.RunStatus.COMPLETED) -> dm.Run:
    return dm.Run(
        run_id=dm.RunId("run-1"),
        tenant=dm.TenantId(TENANT),
        goal="analyze the build logs",
        agent_catalog=(dm.AgentSpecRaw(name="r1", role="research"),),
        provider=dm.ProviderId("stub"),
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


@pytest.fixture()
def stub() -> StubAgentComputeService:
    return StubAgentComputeService()


@pytest.fixture()
def client(stub) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    register_exception_handlers(app)
    inj = injector.Injector()
    inj.binder.bind(AgentComputeServiceProtocol, to=stub)
    app.state.injector = inj
    return TestClient(app)


def _spec_body() -> dict:
    return {
        "tenant": TENANT,
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
        "provider": "acme",
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


class TestTenantRequired:

    def test_post_run_without_tenant_is_422(self, client):
        resp = client.post("/agentcompute/v1/runs", json={**_spec_body(), "tenant": ""})
        assert resp.status_code == 422
        del_body = _spec_body()
        del_body.pop("tenant")
        resp = client.post("/agentcompute/v1/runs", json=del_body)
        assert resp.status_code == 422

    def test_get_without_tenant_header_is_400(self, client):
        assert client.get("/agentcompute/v1/runs/run-1").status_code == 400
        assert client.get(
            "/agentcompute/v1/runs/run-1", headers={"X-Tenant-Id": ""}
        ).status_code == 400

    def test_list_cancel_stream_report_without_tenant_header_are_400(self, client):
        assert client.get("/agentcompute/v1/runs").status_code == 400
        assert client.post("/agentcompute/v1/runs/run-1/cancel").status_code == 400
        assert client.get("/agentcompute/v1/runs/run-1/events").status_code == 400
        assert client.get("/agentcompute/v1/runs/run-1/report").status_code == 400


class TestSyncRunRoundTrip:

    def test_submit_sync_converts_dto_to_typed_request(self, client, stub):
        resp = client.post("/agentcompute/v1/runs", json=_spec_body())
        assert resp.status_code == 200
        (req,) = stub.submitted
        assert req.tenant == TENANT
        assert req.goal == "analyze the build logs"
        assert req.provider == "acme"
        assert req.model == "orchestrator-x"
        assert req.idempotency_key == "idem-1"
        (agent,) = req.agents
        assert isinstance(agent, dm.AgentSpecRaw)
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
            headers={"X-Tenant-Id": TENANT},
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
        base = {"headers": {"X-Tenant-Id": TENANT}}
        assert client.get("/agentcompute/v1/runs", params={"page": 0}, **base).status_code == 422
        assert client.get(
            "/agentcompute/v1/runs", params={"page_size": 200}, **base
        ).status_code == 422


class TestCancel:

    def test_known_run_returns_status(self, client, stub):
        resp = client.post(
            "/agentcompute/v1/runs/run-1/cancel", headers={"X-Tenant-Id": TENANT}
        )
        assert resp.status_code == 200
        assert resp.json() == {"run_id": "run-1", "status": "COMPLETED"}

    def test_unknown_run_is_404(self, client, stub):
        resp = client.post(
            "/agentcompute/v1/runs/nope/cancel", headers={"X-Tenant-Id": TENANT}
        )
        assert resp.status_code == 404


class TestSmokeRoutes:

    def test_health_and_ready(self, client):
        assert client.get("/agentcompute/v1/health").json() == {"status": "ok"}
        assert client.get("/agentcompute/v1/ready").json() == {"ready": True}

    def test_report_renders_html(self, client):
        resp = client.get(
            "/agentcompute/v1/runs/run-1/report", headers={"X-Tenant-Id": TENANT}
        )
        assert resp.status_code == 200
        assert "run-1" in resp.text
        assert "COMPLETED" in resp.text

    def test_unknown_run_report_is_404(self, client):
        resp = client.get(
            "/agentcompute/v1/runs/nope/report", headers={"X-Tenant-Id": TENANT}
        )
        assert resp.status_code == 404

    def test_stream_emits_event_stream(self, client):
        with client.stream(
            "GET", "/agentcompute/v1/runs/run-1/events", headers={"X-Tenant-Id": TENANT}
        ) as resp:
            assert resp.headers["content-type"].startswith("text/event-stream")
            first = next(resp.iter_lines())
        assert first.startswith("event:")

    def test_stream_unknown_run_emits_error_event(self, client):
        with client.stream(
            "GET", "/agentcompute/v1/runs/nope/events", headers={"X-Tenant-Id": TENANT}
        ) as resp:
            events = _read_sse(resp)
        assert events == [("error", {"error": "not found"})]

    def test_stream_emits_durable_agent_round_guard_events(self, client, stub):
        stub.events = _mk_events(status=dm.RunStatus.COMPLETED)
        stub.run = _mk_run(status=dm.RunStatus.COMPLETED)
        with client.stream(
            "GET", "/agentcompute/v1/runs/run-1/events", headers={"X-Tenant-Id": TENANT}
        ) as resp:
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
        with client.stream(
            "GET", "/agentcompute/v1/runs/run-1/events", headers={"X-Tenant-Id": TENANT}
        ) as resp:
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


class TestCrossTenantIsolation:

    def test_submit_for_tenant_a_rejected_tenant_b_accepted(self, client, stub):
        stub.active_counts[TENANT] = 8

        def submit_for(tenant: str) -> int:
            stub.submit_error = (
                TenantRunLimitExceeded(tenant=tenant, limit=8)
                if stub.active_counts.get(tenant, 0) >= 8
                else None
            )
            return client.post(
                "/agentcompute/v1/runs", json={**_spec_body(), "tenant": tenant}
            ).status_code

        assert submit_for(TENANT) == 429
        assert submit_for("tenant-b") == 200
        assert stub.submitted[-1].tenant == "tenant-b"